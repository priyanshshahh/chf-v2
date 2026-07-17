"""Offline tests for the keyless data-breadth collectors.

No live network: every collector's single ``_get_json`` / ``_get_csv``
chokepoint is monkeypatched to return small synthetic payloads. Each collector
is exercised for three properties:

    1. parse correctness on a synthetic API payload,
    2. idempotent upsert (a second identical run adds no duplicate rows),
    3. graceful skip when an endpoint errors or returns empty.
"""

from __future__ import annotations

import pandas as pd
import pytest

from providers import (
    defillama,
    derivatives_bybit,
    derivatives_coinglass,
    derivatives_okx,
    macro_fred,
)


# ---------------------------------------------------------------------------
# Synthetic payloads
# ---------------------------------------------------------------------------
OKX_OI = {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "1000", "oiCcy": "12.5", "ts": "1700000000000"}]}
OKX_FUNDING = {
    "code": "0",
    "data": [
        {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0001", "fundingTime": "1700000000000"},
        {"instId": "BTC-USDT-SWAP", "fundingRate": "0.0002", "fundingTime": "1699971200000"},
    ],
}
OKX_RATIO = {"code": "0", "data": [["1700000000000", "1.23"], ["1699996400000", "0.98"]]}

BYBIT_FUNDING = {
    "retCode": 0,
    "result": {"list": [{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingRateTimestamp": "1700000000000"}]},
}
BYBIT_OI = {
    "retCode": 0,
    "result": {"list": [{"openInterest": "55000.5", "timestamp": "1700000000000"}]},
}
BYBIT_TICKERS = {
    "retCode": 0,
    "time": 1700000000000,
    "result": {"list": [{"symbol": "BTCUSDT", "openInterestValue": "1234567.0", "lastPrice": "42000.5"}]},
}

FRED_CSV = "observation_date,DGS10\n2024-01-01,3.95\n2024-01-02,.\n2024-01-03,4.01\n"

DL_PROTOCOL = {"tvl": [{"date": 1700000000, "totalLiquidityUSD": 5.0e9}, {"date": 1699913600, "totalLiquidityUSD": 4.9e9}]}
DL_CHAIN = [{"date": 1700000000, "tvl": 3.0e10}, {"date": 1699913600, "tvl": 2.9e10}]
DL_POOLS = {"data": [
    {"pool": "abc-1", "project": "aave-v3", "symbol": "USDC", "chain": "Ethereum", "apy": 4.2, "tvlUsd": 1.0e9},
    {"pool": "def-2", "project": "lido", "symbol": "STETH", "chain": "Ethereum", "apy": 3.1, "tvlUsd": 2.0e10},
]}
DL_STABLES = {"peggedAssets": [
    {"name": "Tether", "symbol": "USDT", "circulating": {"peggedUSD": 1.1e11}},
    {"name": "USD Coin", "symbol": "USDC", "circulating": {"peggedUSD": 3.0e10}},
]}

COINGLASS_LIQ = {"code": "0", "data": [
    {"t": 1700000000, "longLiquidationUsd": 1000.0, "shortLiquidationUsd": 2000.0},
]}


# ===========================================================================
# OKX
# ===========================================================================
def test_okx_parse_correctness():
    oi = derivatives_okx.parse_open_interest(OKX_OI, "BTC")
    assert oi == [{"symbol": "BTC", "ts_ms": 1700000000000, "metric": "open_interest", "value": 12.5, "source": "okx"}]
    fr = derivatives_okx.parse_funding_history(OKX_FUNDING, "BTC")
    assert len(fr) == 2 and fr[0]["metric"] == "funding_rate" and fr[0]["value"] == 0.0001
    ls = derivatives_okx.parse_long_short_ratio(OKX_RATIO, "BTC")
    assert len(ls) == 2 and ls[0]["metric"] == "long_short_ratio" and ls[0]["value"] == 1.23


def test_okx_collect_and_idempotent(tmp_path, monkeypatch):
    def fake_get(session, url, params=None):
        if "open-interest" in url:
            return OKX_OI
        if "funding-rate-history" in url:
            return OKX_FUNDING
        if "long-short-account-ratio" in url:
            return OKX_RATIO
        return {"code": "0", "data": []}

    monkeypatch.setattr(derivatives_okx, "_get_json", fake_get)
    monkeypatch.setattr(derivatives_okx.time, "sleep", lambda *_: None)
    out = tmp_path / "okx.parquet"
    cfg = {"instruments": {"BTC": "BTC-USDT-SWAP"}, "funding_history_pages": 1}

    df1 = derivatives_okx.collect_okx_derivatives(out, config=cfg)
    assert not df1.empty
    assert set(df1["metric"]) == {"open_interest", "funding_rate", "long_short_ratio"}
    n_first = len(pd.read_parquet(out))

    derivatives_okx.collect_okx_derivatives(out, config=cfg)  # second run
    assert len(pd.read_parquet(out)) == n_first  # idempotent, no dup rows


def test_okx_graceful_skip(tmp_path, monkeypatch):
    def boom(session, url, params=None):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(derivatives_okx, "_get_json", boom)
    monkeypatch.setattr(derivatives_okx.time, "sleep", lambda *_: None)
    df = derivatives_okx.collect_okx_derivatives(
        tmp_path / "okx.parquet", config={"instruments": {"BTC": "BTC-USDT-SWAP"}}
    )
    assert df.empty
    assert not (tmp_path / "okx.parquet").exists()


# ===========================================================================
# Bybit
# ===========================================================================
def test_bybit_parse_correctness():
    fr = derivatives_bybit.parse_funding_history(BYBIT_FUNDING, "BTC")
    assert fr == [{"symbol": "BTC", "ts_ms": 1700000000000, "metric": "funding_rate", "value": 0.0001, "source": "bybit"}]
    oi = derivatives_bybit.parse_open_interest(BYBIT_OI, "BTC")
    assert oi[0]["metric"] == "open_interest" and oi[0]["value"] == 55000.5
    tk = derivatives_bybit.parse_tickers(BYBIT_TICKERS, "BTC")
    metrics = {r["metric"]: r["value"] for r in tk}
    assert metrics == {"open_interest_value": 1234567.0, "last_price": 42000.5}


def test_bybit_collect_and_idempotent(tmp_path, monkeypatch):
    def fake_get(session, url, params=None):
        if "funding/history" in url:
            return BYBIT_FUNDING
        if "open-interest" in url:
            return BYBIT_OI
        if "tickers" in url:
            return BYBIT_TICKERS
        return {"retCode": 0, "result": {"list": []}}

    monkeypatch.setattr(derivatives_bybit, "_get_json", fake_get)
    monkeypatch.setattr(derivatives_bybit.time, "sleep", lambda *_: None)
    out = tmp_path / "bybit.parquet"
    cfg = {"symbols": {"BTC": "BTCUSDT"}}

    df1 = derivatives_bybit.collect_bybit_derivatives(out, config=cfg)
    assert set(df1["metric"]) == {"funding_rate", "open_interest", "open_interest_value", "last_price"}
    n_first = len(pd.read_parquet(out))

    derivatives_bybit.collect_bybit_derivatives(out, config=cfg)
    assert len(pd.read_parquet(out)) == n_first


def test_bybit_graceful_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        derivatives_bybit, "_get_json", lambda *a, **k: {"retCode": 10001, "retMsg": "bad", "result": {}}
    )
    monkeypatch.setattr(derivatives_bybit.time, "sleep", lambda *_: None)
    df = derivatives_bybit.collect_bybit_derivatives(
        tmp_path / "bybit.parquet", config={"symbols": {"BTC": "BTCUSDT"}}
    )
    assert df.empty


# ===========================================================================
# FRED macro
# ===========================================================================
def test_fred_parse_correctness():
    rows = macro_fred.parse_fred_csv(FRED_CSV, "DGS10")
    # the "." missing marker row is dropped
    assert len(rows) == 2
    assert rows[0]["series"] == "DGS10" and rows[0]["value"] == 3.95
    assert all(r["date"].hour == 0 for r in rows)


def test_fred_collect_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_fred, "_get_csv", lambda *a, **k: FRED_CSV)
    monkeypatch.setattr(macro_fred.time, "sleep", lambda *_: None)
    out = tmp_path / "fred.parquet"
    cfg = {"series": {"DGS10": "10Y"}}

    df1 = macro_fred.collect_macro_fred(out, config=cfg)
    assert len(df1) == 2
    n_first = len(pd.read_parquet(out))

    macro_fred.collect_macro_fred(out, config=cfg)
    assert len(pd.read_parquet(out)) == n_first


def test_fred_graceful_skip_on_404(tmp_path, monkeypatch):
    def raise_404(*a, **k):
        raise RuntimeError("404 Not Found")

    monkeypatch.setattr(macro_fred, "_get_csv", raise_404)
    monkeypatch.setattr(macro_fred.time, "sleep", lambda *_: None)
    df = macro_fred.collect_macro_fred(tmp_path / "fred.parquet", config={"series": {"NOPE": "x"}})
    assert df.empty
    assert not (tmp_path / "fred.parquet").exists()


# ===========================================================================
# DeFiLlama
# ===========================================================================
def test_defillama_parse_correctness():
    tvl = defillama.parse_protocol_tvl(DL_PROTOCOL, "uniswap")
    assert len(tvl) == 2 and tvl[0]["protocol"] == "uniswap" and tvl[0]["tvl_usd"] == 5.0e9
    chain = defillama.parse_chain_tvl(DL_CHAIN, "Ethereum")
    assert len(chain) == 2 and chain[0]["chain"] == "Ethereum"
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    pools = defillama.parse_pools(DL_POOLS, top_n=1, ts_utc=ts)
    assert len(pools) == 1 and pools[0]["project"] == "lido"  # sorted by TVL desc
    stables = defillama.parse_stablecoins(DL_STABLES, top_n=5, ts_utc=ts)
    assert stables[0]["stablecoin"] == "Tether" and stables[0]["circulating_usd"] == 1.1e11


def test_defillama_collect_and_idempotent(tmp_path, monkeypatch):
    def fake_get(session, url, params=None):
        if "/protocol/" in url:
            return DL_PROTOCOL
        if "historicalChainTvl" in url:
            return DL_CHAIN
        if url.endswith("/pools"):
            return DL_POOLS
        if "/stablecoins" in url:
            return DL_STABLES
        return {}

    monkeypatch.setattr(defillama, "_get_json", fake_get)
    monkeypatch.setattr(defillama._time, "sleep", lambda *_: None)
    cfg = {"protocols": ["uniswap"], "chains": ["Ethereum"], "yields_top_n": 2, "stablecoins_top_n": 5}

    frames = defillama.collect_defillama(tmp_path, config=cfg)
    assert not frames["tvl"].empty and not frames["yields"].empty
    counts = {name: len(pd.read_parquet(tmp_path / f"{name}.parquet")) for name in frames}

    defillama.collect_defillama(tmp_path, config=cfg)  # second run
    for name in frames:
        assert len(pd.read_parquet(tmp_path / f"{name}.parquet")) == counts[name]


def test_defillama_graceful_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(defillama, "_get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(defillama._time, "sleep", lambda *_: None)
    frames = defillama.collect_defillama(tmp_path, config={"protocols": ["uniswap"], "chains": ["Ethereum"]})
    assert all(f.empty for f in frames.values())
    assert not (tmp_path / "tvl.parquet").exists()


# ===========================================================================
# Coinglass (key-gated)
# ===========================================================================
def test_coinglass_parse_correctness():
    rows = derivatives_coinglass.parse_liquidations(COINGLASS_LIQ, "BTC")
    metrics = {r["metric"]: r["value"] for r in rows}
    assert metrics == {"long_liquidation_usd": 1000.0, "short_liquidation_usd": 2000.0}
    assert rows[0]["ts_ms"] == 1700000000 * 1000  # seconds promoted to ms


def test_coinglass_skips_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    df = derivatives_coinglass.collect_coinglass(
        tmp_path / "liquidations.parquet", config={"symbols": ["BTC"], "api_key_env": "COINGLASS_API_KEY"}
    )
    assert df.empty
    assert not (tmp_path / "liquidations.parquet").exists()


def test_coinglass_collect_with_key(tmp_path, monkeypatch):
    monkeypatch.setattr(derivatives_coinglass, "_get_json", lambda s, url, params=None: COINGLASS_LIQ if "liquidation" in url else {"code": "0", "data": []})
    monkeypatch.setattr(derivatives_coinglass.time, "sleep", lambda *_: None)
    out = tmp_path / "liquidations.parquet"
    cfg = {"symbols": ["BTC"], "api_key_env": "COINGLASS_API_KEY"}

    df1 = derivatives_coinglass.collect_coinglass(out, config=cfg, api_key="fake-key")
    assert set(df1["metric"]) == {"long_liquidation_usd", "short_liquidation_usd"}
    n_first = len(pd.read_parquet(out))

    derivatives_coinglass.collect_coinglass(out, config=cfg, api_key="fake-key")
    assert len(pd.read_parquet(out)) == n_first
