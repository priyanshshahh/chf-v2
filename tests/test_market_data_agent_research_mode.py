from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pandas as pd
import pytest

import agents.market_data_agent as market_module
import main as main_module
from agents.market_data_agent import MarketDataAgent
from configs.config import load_config
from providers.ccxt_market import CCXTMarketProvider
from providers.coinmarketcap import CoinMarketCapProvider
from providers.http_client import RateLimitError
from providers.market_fallbacks import FallbackFetchResult
from scripts.verify_market_run import inspect_market_outputs, validate_market_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    market_data = dict(cfg["market_data"])
    market_data.update(cfg["market_data_dev"])
    market_data["cache_dir"] = "data/cache/market"
    market_data["minimum_assets_required"] = 2
    market_data["maximum_failed_assets_allowed"] = 5
    market_data["min_history_days"] = 3
    market_data["backfill_days"] = 5
    cfg["market_data"] = market_data
    universe = dict(cfg["universe"])
    universe.update(cfg["universe_dev"])
    universe["output_dir"] = "data/raw/universe"
    cfg["universe"] = universe
    return cfg


def _write_universe(tmp_path: Path, rows: list[dict]) -> Path:
    out_dir = tmp_path / "data" / "raw" / "universe"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_dir / "universe_monthly.parquet", index=False)
    return out_dir


def _default_universe_rows() -> list[dict]:
    earlier = pd.Timestamp("2023-12-01T00:00:00Z")
    latest = pd.Timestamp("2024-01-01T00:00:00Z")
    return [
        {
            "snapshot_date": earlier,
            "is_eligible": True,
            "symbol": "OLD",
            "coin_id": "old",
            "exchange": "coinbase",
            "exchange_symbol": "OLD/USDC",
            "market_cap_rank": 999,
        },
        {
            "snapshot_date": latest,
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        },
        {
            "snapshot_date": latest,
            "is_eligible": True,
            "symbol": "ETH",
            "coin_id": "ethereum",
            "exchange": "kraken",
            "exchange_symbol": "ETH/USD",
            "market_cap_rank": 2,
        },
    ]


def _patch_now(monkeypatch):
    fixed_now = pd.Timestamp("2024-01-06T12:00:00Z")
    monkeypatch.setattr(MarketDataAgent, "_now_utc", lambda self: fixed_now)


def _cfg_cmc(tmp_path: Path) -> dict:
    cfg = _cfg(tmp_path)
    cfg["market_data"].update(
        {
            "primary_provider": "coinmarketcap",
            "use_cmc_ohlcv": True,
            "lookback_days": 1095,
            "interval": "daily",
            "convert": "USD",
            "cache_dir": "data/cache/cmc",
            "minimum_assets_required": 1,
            "min_history_days": 2,
            "fallback_to_free_providers": True,
            "live_api_enabled": False,
            "use_fixtures": False,
        }
    )
    return cfg


def _write_historical_cmc_universe(tmp_path: Path) -> Path:
    rows = []
    for snapshot in [pd.Timestamp("2023-12-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")]:
        rows.extend(
            [
                {
                    "snapshot_date": snapshot,
                    "is_eligible": True,
                    "symbol": "BTC",
                    "coin_id": "bitcoin",
                    "cmc_id": 1,
                    "exchange": "",
                    "exchange_symbol": "",
                    "market_cap_rank": 1,
                },
                {
                    "snapshot_date": snapshot,
                    "is_eligible": True,
                    "symbol": "ETH",
                    "coin_id": "ethereum",
                    "cmc_id": 1027,
                    "exchange": "",
                    "exchange_symbol": "",
                    "market_cap_rank": 2,
                },
            ]
        )
    return _write_universe(tmp_path, rows)


def _mock_cmc_ohlcv(monkeypatch):
    def fake_fetch(self, cmc_id, symbol, time_start, time_end, interval="daily", convert="USD", **kwargs):
        return pd.DataFrame(
            {
                "date_ts": [pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-03T00:00:00Z")],
                "cmc_id": [cmc_id, cmc_id, cmc_id],
                "symbol": [symbol, symbol, symbol],
                "open": [1.0, 1.1, 1.2],
                "high": [1.2, 1.3, 1.4],
                "low": [0.9, 1.0, 1.1],
                "close": [1.1, 1.2, 1.3],
                "volume": [100, 120, 140],
                "market_cap": [1000, 1100, 1200],
                "source": ["coinmarketcap", "coinmarketcap", "coinmarketcap"],
            }
        )

    monkeypatch.setattr(CoinMarketCapProvider, "fetch_ohlcv_historical", fake_fetch)


def _read_market_outputs(tmp_path: Path):
    out_dir = tmp_path / "data" / "raw" / "market"
    market = pd.read_parquet(out_dir / "market_ohlcv.parquet")
    coverage = pd.read_parquet(out_dir / "market_coverage_report.parquet")
    with open(out_dir / "market_manifest.json", "r") as f:
        manifest = json.load(f)
    return market, coverage, manifest


def datetime_from_str(value: str):
    return pd.Timestamp(value).to_pydatetime()


def test_does_not_import_binance_provider():
    source = inspect.getsource(market_module)
    assert "CCXTBinanceProvider" not in source
    assert "ccxt_binance" not in source


def test_loads_latest_universe_snapshot(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    agent = MarketDataAgent(cfg)
    agent.prepare()
    assert {r.symbol for r in agent.asset_requests} == {"BTC", "ETH"}
    assert all(r.symbol != "OLD" for r in agent.asset_requests)


def test_supports_max_assets(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["max_assets"] = 1
    _write_universe(tmp_path, _default_universe_rows())
    agent = MarketDataAgent(cfg)
    agent.prepare()
    assert len(agent.asset_requests) == 1
    assert agent.asset_requests[0].symbol == "BTC"


def test_uses_exchange_symbol_from_universe(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, _, _ = _read_market_outputs(tmp_path)
    assert set(market["exchange_symbol"]) == {"BTC/USDC", "ETH/USD"}


def test_never_builds_btc_usdt(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, _, _ = _read_market_outputs(tmp_path)
    assert not market["exchange_symbol"].astype(str).str.contains("USDT", case=False).any()


def test_writes_canonical_market_ohlcv_parquet(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market = pd.read_parquet(tmp_path / "data" / "raw" / "market" / "market_ohlcv.parquet")
    assert {"is_full_ohlcv", "data_type", "quote_currency"}.issubset(market.columns)


def test_writes_canonical_coverage_columns(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    coverage = pd.read_parquet(tmp_path / "data" / "raw" / "market" / "market_coverage_report.parquet")
    expected = {
        "symbol", "coin_id", "cmc_id", "exchange", "exchange_symbol", "requested", "fetched", "source_used",
        "row_count", "start_date", "end_date", "requested_start_date", "requested_end_date", "missing_days",
        "forward_filled_days", "incomplete_rows_dropped", "failure_reason", "passed_qa", "is_full_ohlcv",
        "data_type", "quote_currency", "provider_attempts", "provider_failure_reasons", "fallback_used",
    }
    assert expected.issubset(coverage.columns)


def test_writes_coverage_report(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    coverage = pd.read_parquet(tmp_path / "data" / "raw" / "market" / "market_coverage_report.parquet")
    expected = {
        "provider_attempts",
        "provider_failure_reasons",
        "data_type",
        "is_full_ohlcv",
        "quote_currency",
        "fallback_used",
    }
    assert expected.issubset(coverage.columns)


def test_writes_manifest(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    _, _, manifest = _read_market_outputs(tmp_path)
    assert "full_ohlcv_assets" in manifest


def test_fails_when_universe_missing(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        MarketDataAgent(cfg).prepare()


def test_fails_on_empty_ohlcv(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    cfg["market_data"]["use_fixtures"] = False
    cfg["market_data"]["live_api_enabled"] = False
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    assert (out_dir / "data_quality_daily.md").exists()
    assert not (out_dir / "market_ohlcv.parquet").exists()
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_fails_on_low_coverage(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = _default_universe_rows()
    rows.append(
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "ALGO",
            "coin_id": "algorand",
            "exchange": "coinbase",
            "exchange_symbol": "ALGO/USD",
            "market_cap_rank": 3,
        }
    )
    _write_universe(tmp_path, rows)
    cfg["market_data"]["minimum_assets_required"] = 3
    assert not MarketDataAgent(cfg).execute(max_retries=1)


def test_verify_market_run_rejects_binance_output(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "binance",
                "exchange_symbol": "BTC/USDT",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "source": "binance",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "binance",
                "exchange_symbol": "BTC/USDT",
                "requested": True,
                "fetched": True,
                "source_used": "binance",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": "[]",
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert any("binance" in failure.lower() or "USDT" in failure for failure in failures)


def test_market_cmc_loads_union_of_historical_universe_assets(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    _mock_cmc_ohlcv(monkeypatch)
    cfg = _cfg_cmc(tmp_path)
    _write_historical_cmc_universe(tmp_path)
    agent = MarketDataAgent(cfg)
    agent.prepare()
    assert {r.symbol for r in agent.asset_requests} == {"BTC", "ETH"}
    assert {r.cmc_id for r in agent.asset_requests} == {1, 1027}


def test_market_cmc_fetches_ohlcv_by_cmc_id(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    seen = []

    def fake_fetch(self, cmc_id, symbol, time_start, time_end, interval="daily", convert="USD", **kwargs):
        seen.append((cmc_id, symbol))
        return pd.DataFrame(
            {
                "date_ts": [pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-01-02T00:00:00Z")],
                "cmc_id": [cmc_id, cmc_id],
                "symbol": [symbol, symbol],
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.2],
                "volume": [100, 120],
                "market_cap": [1000, 1100],
            }
        )

    monkeypatch.setattr(CoinMarketCapProvider, "fetch_ohlcv_historical", fake_fetch)
    cfg = _cfg_cmc(tmp_path)
    _write_historical_cmc_universe(tmp_path)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    assert (1, "BTC") in seen and (1027, "ETH") in seen


def test_market_cmc_writes_cmc_id_in_market_ohlcv(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    _mock_cmc_ohlcv(monkeypatch)
    cfg = _cfg_cmc(tmp_path)
    _write_historical_cmc_universe(tmp_path)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, _, _ = _read_market_outputs(tmp_path)
    assert "cmc_id" in market.columns
    assert market["cmc_id"].notna().all()


def test_market_cmc_rejects_empty_ohlcv(tmp_path, monkeypatch):
    _patch_now(monkeypatch)

    def fake_fetch(self, cmc_id, symbol, time_start, time_end, interval="daily", convert="USD", **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(CoinMarketCapProvider, "fetch_ohlcv_historical", fake_fetch)
    cfg = _cfg_cmc(tmp_path)
    cfg["market_data"]["fallback_to_free_providers"] = False
    _write_historical_cmc_universe(tmp_path)
    assert not MarketDataAgent(cfg).execute(max_retries=1)


def test_market_cmc_falls_back_to_free_provider_when_enabled(tmp_path, monkeypatch):
    _patch_now(monkeypatch)

    def fake_fetch(self, cmc_id, symbol, time_start, time_end, interval="daily", convert="USD", **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(CoinMarketCapProvider, "fetch_ohlcv_historical", fake_fetch)
    def fake_fallback(*args, **kwargs):
        return pd.DataFrame(
            {
                "date_ts": [pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-01-02T00:00:00Z")],
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.2],
                "volume": [100, 120],
            }
        )
    monkeypatch.setattr(
        market_module.MarketFallbackProvider,
        "fetch_daily_data",
        lambda self, **kwargs: FallbackFetchResult(
            df=fake_fallback(),
            provider_name="cryptocompare",
            data_type="aggregate_ohlcv",
            is_full_ohlcv=True,
            attempts=["cryptocompare"],
            failure_reasons={},
        ),
    )
    cfg = _cfg_cmc(tmp_path)
    _write_historical_cmc_universe(tmp_path)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, coverage, _ = _read_market_outputs(tmp_path)
    assert not market.empty
    assert coverage["source_used"].astype(str).str.len().gt(0).all()


def test_verify_market_rejects_missing_cmc_id(tmp_path):
    cfg = _cfg_cmc(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "cmc_id": None,
                "exchange": "",
                "exchange_symbol": "",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "market_cap": 10,
                "source": "coinmarketcap",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "cmc_id": None,
                "exchange": "",
                "exchange_symbol": "",
                "requested": True,
                "fetched": True,
                "source_used": "coinmarketcap",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2021-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["coinmarketcap"]',
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": [], "lookback_days": 1095}, f)
    failures = validate_market_outputs(cfg)
    assert any("cmc_id" in failure for failure in failures)


def test_verifier_passes_when_all_close_values_are_positive(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 1
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["ccxt_coinbase"]',
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert failures == []


def test_verifier_fails_gracefully_on_missing_is_full_ohlcv(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "passed_qa": True,
                "data_type": "exchange_ohlcv",
                "quote_currency": "USD",
                "provider_attempts": "[]",
                "provider_failure_reasons": "{}",
                "fallback_used": False,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert "FAIL: market_ohlcv.parquet missing required column is_full_ohlcv" in failures


def test_verifier_fails_when_close_is_null_or_non_numeric(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": None,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["ccxt_coinbase"]',
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures, warnings = inspect_market_outputs(cfg)
    assert any("FAIL: close contains null/non-numeric values" in failure for failure in failures)
    assert any("first bad close rows" in warning for warning in warnings)


def test_forward_fill_missing_days_sets_volume_zero(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, _, _ = _read_market_outputs(tmp_path)
    btc = market[market["symbol"] == "BTC"]
    filled = btc[btc["date_ts"] == pd.Timestamp("2024-01-03T00:00:00Z")]
    assert len(filled) == 1
    assert bool(filled["is_forward_filled"].iloc[0]) is True
    assert float(filled["volume"].iloc[0]) == 0.0
    assert float(filled["close"].iloc[0]) > 0


def test_raw_ohlcv_with_close_zero_is_rejected(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)

    cfg["market_data"]["minimum_assets_required"] = 1

    def fake_fetch(self, exchange_symbol, since_dt, until_dt=None, limit=1000, max_pages=20, max_rows=3000):
        return pd.DataFrame(
            {
                "date_ts": pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC"),
                "open": [1, 2, 3, 4, 5],
                "high": [2, 3, 4, 5, 6],
                "low": [1, 2, 3, 4, 5],
                "close": [0, 0, 0, 0, 0],
                "volume": [1, 1, 1, 1, 1],
            }
        )

    monkeypatch.setattr(CCXTMarketProvider, "fetch_ohlcv", fake_fetch)
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    assert (out_dir / "data_quality_daily.md").exists()
    assert not (out_dir / "market_ohlcv.parquet").exists()
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_first_missing_row_without_previous_close_is_not_filled_with_zero(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 1
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)

    def fake_fetch(self, exchange_symbol, since_dt, until_dt=None, limit=1000, max_pages=20, max_rows=3000):
        return pd.DataFrame(
            {
                "date_ts": [
                    pd.Timestamp("2024-01-02T00:00:00Z"),
                    pd.Timestamp("2024-01-03T00:00:00Z"),
                    pd.Timestamp("2024-01-04T00:00:00Z"),
                ],
                "open": [2, 3, 4],
                "high": [3, 4, 5],
                "low": [1, 2, 3],
                "close": [2.5, 3.5, 4.5],
                "volume": [1, 1, 1],
            }
        )

    monkeypatch.setattr(CCXTMarketProvider, "fetch_ohlcv", fake_fetch)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market = pd.read_parquet(tmp_path / "data" / "raw" / "market" / "market_ohlcv.parquet")
    assert pd.Timestamp("2024-01-01T00:00:00Z") not in set(market["date_ts"])
    assert not (market["close"] <= 0).any()


def test_incomplete_current_day_candle_is_dropped(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, _, _ = _read_market_outputs(tmp_path)
    assert pd.Timestamp("2024-01-06T00:00:00Z") not in set(market["date_ts"])
    assert not market["is_incomplete_dropped"].any()


def test_cache_is_used_before_live_api(tmp_path):
    provider = CCXTMarketProvider(
        exchange_name="coinbase",
        cache_dir=tmp_path / "cache",
        timeframe="1d",
        live_api_enabled=False,
        use_fixtures=True,
        fixture_dir=Path("/Users/priyansh/Desktop/chf/tests/fixtures/market"),
    )
    df1 = provider.fetch_ohlcv("BTC/USDC", since_dt=datetime_from_str("2024-01-01T00:00:00Z"))
    assert not df1.empty
    provider2 = CCXTMarketProvider(
        exchange_name="coinbase",
        cache_dir=tmp_path / "cache",
        timeframe="1d",
        live_api_enabled=False,
        use_fixtures=False,
        fixture_dir=Path("/Users/priyansh/Desktop/chf/tests/fixtures/market"),
    )
    df2 = provider2.fetch_ohlcv("BTC/USDC", since_dt=datetime_from_str("2024-01-01T00:00:00Z"))
    assert not df2.empty
    assert provider2.cache_hit_count_by_provider["ccxt_coinbase"] == 1


def test_resolves_exchange_symbol_per_provider(tmp_path):
    provider = CCXTMarketProvider(
        exchange_name="kraken",
        cache_dir=tmp_path / "cache",
        live_api_enabled=False,
    )
    cache_path = provider._cache_path("markets", "kraken")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"XBT/USD": {"symbol": "XBT/USD"}}))
    assert provider.resolve_market_symbol("BTC", preferred_quote="USD", allow_usdt=False) == "XBT/USD"


def test_does_not_reuse_coinbase_symbol_blindly_on_kraken(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)
    seen_symbols = []

    def fake_has_market(self, exchange_symbol):
        return exchange_symbol == "BTC/USDC" and self.exchange_name == "coinbase"

    def fake_resolve(self, base_symbol, preferred_quote="USD", allow_usdt=False):
        if self.exchange_name == "kraken":
            return "XBT/USD"
        return None

    def fake_fetch(self, exchange_symbol, since_dt, until_dt=None, limit=1000, max_pages=20, max_rows=3000):
        seen_symbols.append((self.exchange_name, exchange_symbol))
        if self.exchange_name == "coinbase":
            raise RateLimitError(self.provider_key, "coinbase temporarily rate limited")
        return pd.DataFrame(
            {
                "date_ts": pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC"),
                "open": [1, 2, 3, 4, 5],
                "high": [2, 3, 4, 5, 6],
                "low": [1, 2, 3, 4, 5],
                "close": [1.5, 2.5, 3.5, 4.5, 5.5],
                "volume": [1, 1, 1, 1, 1],
            }
        )

    monkeypatch.setattr(CCXTMarketProvider, "has_market", fake_has_market)
    monkeypatch.setattr(CCXTMarketProvider, "resolve_market_symbol", fake_resolve)
    monkeypatch.setattr(CCXTMarketProvider, "fetch_ohlcv", fake_fetch)
    cfg["market_data"]["minimum_assets_required"] = 1
    assert MarketDataAgent(cfg).execute(max_retries=1)
    assert ("kraken", "XBT/USD") in seen_symbols
    assert ("kraken", "BTC/USDC") not in seen_symbols


def test_market_agent_fails_when_fetched_symbols_zero(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    cfg["market_data"]["use_fixtures"] = False
    cfg["market_data"]["live_api_enabled"] = False
    assert not MarketDataAgent(cfg).execute(max_retries=1)


def test_canonical_market_outputs_not_written_when_all_assets_fail(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    cfg["market_data"]["use_fixtures"] = False
    cfg["market_data"]["live_api_enabled"] = False
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    assert (out_dir / "data_quality_daily.md").exists()
    assert not (out_dir / "market_ohlcv.parquet").exists()
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_failure_reasons_are_written_for_every_requested_symbol(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    cfg["market_data"]["use_fixtures"] = False
    cfg["market_data"]["live_api_enabled"] = False
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    report = (out_dir / "data_quality_daily.md").read_text()
    assert "Fatal Errors" in report
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_main_market_exits_nonzero_when_zero_symbols_fetched(tmp_path, monkeypatch, capsys):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, _default_universe_rows())
    cfg["market_data"]["use_fixtures"] = False
    cfg["market_data"]["live_api_enabled"] = False

    monkeypatch.setattr(main_module, "_command_cfg", lambda args=None: cfg)
    with pytest.raises(SystemExit) as excinfo:
        main_module.cmd_market(type("Args", (), {})())
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "[market] ERROR: MarketDataAgent failed." in captured.out


def test_provider_fallback_after_rate_limit(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        },
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "ETH",
            "coin_id": "ethereum",
            "exchange": "coinbase",
            "exchange_symbol": "ETH/USD",
            "market_cap_rank": 2,
        },
    ]
    _write_universe(tmp_path, rows)
    calls = {"coinbase": 0}

    def fake_has_market(self, exchange_symbol):
        return True

    def fake_fetch(self, exchange_symbol, since_dt, until_dt=None, limit=1000, max_pages=20, max_rows=3000):
        if self.exchange_name == "coinbase":
            calls["coinbase"] += 1
            raise RateLimitError(self.provider_key, "coinbase temporarily rate limited")
        if self.exchange_name == "kraken":
            return pd.DataFrame()
        if self.exchange_name == "kucoin":
            if exchange_symbol == "BTC/USDC":
                rows = json.loads(Path("/Users/priyansh/Desktop/chf/tests/fixtures/market/ccxt_kucoin_ohlcv_BTC_USDC_2024-01-01_1d.json").read_text())
                return pd.DataFrame(
                    {
                        "date_ts": pd.to_datetime([row[0] for row in rows], unit="ms", utc=True),
                        "open": [row[1] for row in rows],
                        "high": [row[2] for row in rows],
                        "low": [row[3] for row in rows],
                        "close": [row[4] for row in rows],
                        "volume": [row[5] for row in rows],
                    }
                )
            return pd.DataFrame()
        return pd.DataFrame()

    monkeypatch.setattr(CCXTMarketProvider, "has_market", fake_has_market)
    monkeypatch.setattr(CCXTMarketProvider, "fetch_ohlcv", fake_fetch)
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    assert calls["coinbase"] == 1


def test_provider_circuit_breaker_prevents_repeated_hammering(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = _default_universe_rows()
    _write_universe(tmp_path, rows)
    calls = {"coinbase": 0}

    def fake_has_market(self, exchange_symbol):
        return True

    def fake_fetch(self, exchange_symbol, since_dt, until_dt=None, limit=1000, max_pages=20, max_rows=3000):
        if self.exchange_name == "coinbase":
            calls["coinbase"] += 1
            raise RateLimitError(self.provider_key, "coinbase temporarily rate limited")
        return pd.DataFrame()

    monkeypatch.setattr(CCXTMarketProvider, "has_market", fake_has_market)
    monkeypatch.setattr(CCXTMarketProvider, "fetch_ohlcv", fake_fetch)
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    assert calls["coinbase"] == 1


def test_kucoin_fallback_success(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        },
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "ETH",
            "coin_id": "ethereum",
            "exchange": "kraken",
            "exchange_symbol": "ETH/USD",
            "market_cap_rank": 2,
        },
    ]
    _write_universe(tmp_path, rows)

    original_resolve = CCXTMarketProvider.resolve_market_symbol

    def fake_has_market(self, exchange_symbol):
        return False if self.exchange_name == "coinbase" else CCXTMarketProvider.has_market.__wrapped__(self, exchange_symbol)  # type: ignore[attr-defined]

    def fake_resolve(self, base_symbol, preferred_quote="USD", allow_usdt=False):
        if self.exchange_name == "coinbase":
            return None
        return original_resolve(self, base_symbol, preferred_quote=preferred_quote, allow_usdt=allow_usdt)

    monkeypatch.setattr(CCXTMarketProvider, "has_market", lambda self, exchange_symbol: False if self.exchange_name == "coinbase" else exchange_symbol in self.load_markets())
    monkeypatch.setattr(CCXTMarketProvider, "resolve_market_symbol", fake_resolve)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, coverage, manifest = _read_market_outputs(tmp_path)
    assert "ccxt_kucoin" in set(market["source"])
    btc_coverage = coverage[coverage["symbol"] == "BTC"].iloc[0]
    assert btc_coverage["source_used"] == "ccxt_kucoin"
    assert "kucoin" in manifest["exchanges_used"]


def test_gemini_fallback_success(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 1
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USD",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)

    def fake_has_market(self, exchange_symbol):
        return False

    def fake_resolve(self, base_symbol, preferred_quote="USD", allow_usdt=False):
        if self.exchange_name == "gemini":
            return "BTC/USD"
        return None

    monkeypatch.setattr(CCXTMarketProvider, "has_market", fake_has_market)
    monkeypatch.setattr(CCXTMarketProvider, "resolve_market_symbol", fake_resolve)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, coverage, _ = _read_market_outputs(tmp_path)
    assert set(market["source"]) == {"ccxt_gemini"}
    assert coverage.iloc[0]["source_used"] == "ccxt_gemini"


def test_per_asset_timeout_records_asset_timeout(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["per_asset_timeout_seconds"] = 0
    _write_universe(tmp_path, _default_universe_rows())
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    assert (out_dir / "data_quality_daily.md").exists()
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_asset_below_min_history_days_does_not_count_toward_full_ohlcv_assets(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["min_history_days"] = 10
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "BTC",
            "coin_id": "bitcoin",
            "exchange": "coinbase",
            "exchange_symbol": "BTC/USDC",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)
    assert not MarketDataAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "market"
    assert (out_dir / "data_quality_daily.md").exists()
    assert not (out_dir / "market_coverage_report.parquet").exists()


def test_coingecko_close_only_fallback_does_not_fake_ohlc(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 0
    cfg["market_data"]["fallback_provider_priority"] = ["coingecko"]
    rows = [
        {
            "snapshot_date": pd.Timestamp("2024-01-01T00:00:00Z"),
            "is_eligible": True,
            "symbol": "ALGO",
            "coin_id": "algorand",
            "exchange": "coinbase",
            "exchange_symbol": "ALGO/USD",
            "market_cap_rank": 1,
        }
    ]
    _write_universe(tmp_path, rows)

    def fake_has_market(self, exchange_symbol):
        return False

    monkeypatch.setattr(CCXTMarketProvider, "has_market", fake_has_market)
    assert MarketDataAgent(cfg).execute(max_retries=1)
    market, coverage, manifest = _read_market_outputs(tmp_path)
    algo = market[market["symbol"] == "ALGO"]
    assert not algo.empty
    assert algo["is_full_ohlcv"].eq(False).all()
    assert algo["open"].isna().all()
    assert algo["high"].isna().all()
    assert algo["low"].isna().all()
    assert coverage.iloc[0]["data_type"] == "close_volume_only"
    assert manifest["full_ohlcv_assets"] == 0


def test_verify_market_run_counts_only_full_ohlcv_assets(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 2
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            },
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "ALGO",
                "exchange": "coinbase",
                "exchange_symbol": "ALGO/USD",
                "open": None,
                "high": None,
                "low": None,
                "close": 0.2,
                "volume": 1000,
                "source": "coingecko",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "close_volume_only",
                "is_full_ohlcv": False,
                "quote_currency": "USD",
            },
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": "[]",
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            },
            {
                "symbol": "ALGO",
                "coin_id": "algorand",
                "exchange": "coinbase",
                "exchange_symbol": "ALGO/USD",
                "requested": True,
                "fetched": True,
                "source_used": "coingecko",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": "[]",
                "provider_failure_reasons": "{}",
                "data_type": "close_volume_only",
                "is_full_ohlcv": False,
                "quote_currency": "USD",
                "fallback_used": True,
                "passed_qa": True,
            },
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert any("full OHLCV assets below minimum_assets_required" in failure for failure in failures)


def test_verifier_validates_ohlc_only_for_full_ohlcv_rows(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 1
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": None,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["ccxt_coinbase"]',
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert any("full OHLCV rows contain null/non-numeric OHLC values" in failure for failure in failures)


def test_verifier_allows_partial_rows_with_null_ohlc(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["market_data"]["minimum_assets_required"] = 0
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "ALGO",
                "exchange": "coinbase",
                "exchange_symbol": "ALGO/USD",
                "open": None,
                "high": None,
                "low": None,
                "close": 0.2,
                "volume": 1000,
                "source": "coingecko",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "close_volume_only",
                "is_full_ohlcv": False,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "ALGO",
                "coin_id": "algorand",
                "exchange": "coinbase",
                "exchange_symbol": "ALGO/USD",
                "requested": True,
                "fetched": True,
                "source_used": "coingecko",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["coingecko"]',
                "provider_failure_reasons": "{}",
                "data_type": "close_volume_only",
                "is_full_ohlcv": False,
                "quote_currency": "USD",
                "fallback_used": True,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert failures == []


def test_stale_schema_is_rejected_by_verifier(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"date_ts": pd.Timestamp("2024-01-01T00:00:00Z"), "symbol": "BTC"}]).to_parquet(
        out_dir / "market_ohlcv.parquet", index=False
    )
    pd.DataFrame([{"symbol": "BTC"}]).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures = validate_market_outputs(cfg)
    assert any("missing required column" in failure for failure in failures)


def test_verifier_reports_bad_rows_clearly(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date_ts": pd.Timestamp("2024-01-01T00:00:00Z"),
                "symbol": "BTC",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 0,
                "volume": 1,
                "source": "ccxt_coinbase",
                "snapshot_id": "x",
                "fetched_at_utc": "2024-01-01T00:00:00Z",
                "is_forward_filled": False,
                "is_incomplete_dropped": False,
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
            }
        ]
    ).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "BTC",
                "coin_id": "bitcoin",
                "exchange": "coinbase",
                "exchange_symbol": "BTC/USDC",
                "requested": True,
                "fetched": True,
                "source_used": "ccxt_coinbase",
                "row_count": 1,
                "start_date": "2024-01-01",
                "end_date": "2024-01-01",
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "missing_days": 0,
                "forward_filled_days": 0,
                "incomplete_rows_dropped": 0,
                "failure_reason": "",
                "provider_attempts": '["ccxt_coinbase"]',
                "provider_failure_reasons": "{}",
                "data_type": "exchange_ohlcv",
                "is_full_ohlcv": True,
                "quote_currency": "USD",
                "fallback_used": False,
                "passed_qa": True,
            }
        ]
    ).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"output_files": {"market_ohlcv": str(out_dir / "market_ohlcv.parquet")}, "failed_assets": []}, f)
    failures, warnings = inspect_market_outputs(cfg)
    assert any("FAIL: close contains non-positive values" in failure for failure in failures)
    assert any("bad close row counts by symbol" in warning for warning in warnings)


def test_verifier_does_not_falsely_fail_on_valid_10x365_output():
    cfg = load_config()
    cfg = {"_project_root": cfg["_project_root"], **cfg}
    merged = dict(cfg)
    merged["market_data"] = dict(cfg["market_data"])
    merged["market_data"].update(cfg["market_data_10x365"])
    failures = validate_market_outputs(merged)
    assert failures == []
