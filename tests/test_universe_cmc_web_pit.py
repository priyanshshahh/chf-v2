"""
Research-integrity tests for the unified UniverseAgent's survivorship-free
point-in-time source (`source: cmc_web_pit`), classification fixes, and the
extended verifier. Hermetic: a crafted CMC-web fixture parquet is written to a
temp dir; the on-chain gate (which would call CoinMetrics) is disabled so the
tests run fully offline.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest

from agents.universe_agent import UniverseAgent
from configs.config import load_config
from scripts.verify_universe_run import inspect_universe_outputs


# ---------------------------------------------------------------- fixtures
def _row(cmc_id, rank, symbol, name, mcap, added, tags, vol=5_000_000, pairs=25):
    return {
        "cmc_id": cmc_id, "rank": rank, "symbol": symbol, "name": name, "slug": symbol.lower(),
        "market_cap_usd": float(mcap), "price_usd": 1.0, "volume_24h_usd": float(vol),
        "circulating_supply": 1.0, "total_supply": 1.0, "max_supply": 1.0,
        "num_market_pairs": pairs, "date_added": pd.Timestamp(added, tz="UTC"),
        "raw_category_tags": list(tags), "source": "coinmarketcap_web_historical",
    }


def _write_cmc_web_fixture(tmp_path: Path) -> Path:
    """3 monthly snapshots. GONE drops out after month 2 (survivorship probe);
    YOUNG is <365d old in early months (maturity probe); USDT/STETH are excludable."""
    OLD = "2019-01-01"
    rows = []
    for snap in ["2024-01-01", "2024-02-01", "2024-03-01"]:
        base = [
            _row(1, 1, "BTC", "Bitcoin", 9e11, OLD, ["pow", "store-of-value"]),
            _row(2, 2, "ETH", "Ethereum", 4e11, OLD, ["pos", "smart-contracts"]),
            _row(10, 3, "ADA", "Cardano", 3e10, OLD, ["pos", "staking", "platform"]),  # NOT lst
            _row(11, 4, "LINK", "Chainlink", 2e10, OLD, ["defi", "oracles", "tokenized-stock"]),  # NOT synth
            _row(50, 5, "SOLX", "SolX", 1e10, OLD, ["platform"]),
            _row(825, 6, "USDT", "Tether USDt", 8e10, OLD, ["stablecoin", "usd-stablecoin"]),  # excluded
            _row(8085, 7, "STETH", "Lido Staked ETH", 2e10, OLD, ["liquid-staking-derivatives"]),  # excluded
            _row(900, 8, "YOUNG", "Young Coin", 5e9, "2023-12-15", ["platform"]),  # <365d in Jan/Feb 2024
        ]
        if snap in ("2024-01-01", "2024-02-01"):
            base.append(_row(777, 9, "GONE", "Gone Coin", 4e9, OLD, ["platform"]))  # delists after month 2
        for r in base:
            r = dict(r)
            r["snapshot_date"] = pd.Timestamp(snap, tz="UTC")
            rows.append(r)
    df = pd.DataFrame(rows)
    out = tmp_path / "data" / "external" / "cmc_web"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "cmc_web_listings_historical.parquet"
    df.to_parquet(path, index=False)
    return path


def _cfg(tmp_path: Path, **over) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    u = dict(cfg["universe"])
    u.update({
        "source": "cmc_web_pit",
        "live_api_enabled": False,
        "start_date": "2024-01-01", "end_date": "2024-03-01",
        "candidate_n": 50, "final_universe_n": 8, "minimum_eligible_n": 2,
        "require_365d_maturity": True,
        "require_exchange_tradability": True,
        "require_onchain_coverage": False,   # avoid network in tests
        "require_min_volume": True, "min_daily_volume_usd": 1_000_000,
        "min_market_pairs_for_tradability": 1,
        "min_snapshots_required": 3,
        "output_dir": "data/raw/universe", "cache_dir": "data/cache",
        "cmc_web_dataset_path": str(_write_cmc_web_fixture(tmp_path)),
    })
    u.update(over)
    cfg["universe"] = u
    return cfg


def _read(tmp_path: Path):
    out = tmp_path / "data" / "raw" / "universe"
    return (pd.read_parquet(out / "universe_monthly.parquet"),
            pd.read_parquet(out / "exclusions_monthly.parquet"))


# ------------------------------------------------------------------ tests
def test_cmc_web_pit_runs_and_verifies(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    failures, _ = inspect_universe_outputs(cfg)
    assert failures == [], failures


def test_survivorship_bias_free(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, _ = _read(tmp_path)
    uni["snapshot_date"] = pd.to_datetime(uni["snapshot_date"], utc=True)
    jan = set(uni[uni["snapshot_date"] == "2024-01-01"]["symbol"])
    mar = set(uni[uni["snapshot_date"] == "2024-03-01"]["symbol"])
    # GONE was ranked in Jan but delisted by Mar: it MUST remain in the Jan snapshot.
    assert "GONE" in jan, "survivorship bias: a since-delisted coin is missing from its past snapshot"
    assert "GONE" not in mar


def test_manifest_is_survivorship_free(tmp_path):
    cfg = _cfg(tmp_path)
    agent = UniverseAgent(cfg)
    assert agent.execute(max_retries=1)
    assert agent.survivor_only_universe is False
    assert agent._uses_cmc_id is True
    assert agent.universe_mode == "historical_cmc_web_monthly"


def test_pit_maturity_excludes_young_coin(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, exc = _read(tmp_path)
    uni["snapshot_date"] = pd.to_datetime(uni["snapshot_date"], utc=True)
    jan = uni[uni["snapshot_date"] == "2024-01-01"]
    # YOUNG (listed 2023-12-15) is < 365 days old on 2024-01-01 -> excluded, not eligible.
    assert "YOUNG" not in set(jan["symbol"])
    young_exc = exc[exc["symbol"] == "YOUNG"]
    assert (young_exc["exclusion_reason"] == "maturity_unverified").any()


def test_excludes_stablecoin_and_lst_with_correct_flags(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, exc = _read(tmp_path)
    assert "USDT" not in set(uni["symbol"]) and "STETH" not in set(uni["symbol"])
    assert exc[exc["symbol"] == "USDT"]["is_stablecoin"].all()
    assert exc[exc["symbol"] == "STETH"]["is_lst"].all()


def test_majors_not_misclassified(tmp_path):
    """ADA (tagged 'staking') and LINK (stray 'tokenized-stock') must NOT be excluded."""
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, _ = _read(tmp_path)
    eligible = set(uni["symbol"])
    assert "ADA" in eligible, "ADA wrongly excluded (staking tag != LST)"
    assert "LINK" in eligible, "LINK wrongly excluded (stray tokenized-stock tag)"


def test_deterministic_snapshot_hash(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    h1 = pd.read_parquet(tmp_path / "data/raw/universe/universe_monthly.parquet")[["snapshot_date", "snapshot_id"]].drop_duplicates()
    assert UniverseAgent(cfg).execute(max_retries=1)
    h2 = pd.read_parquet(tmp_path / "data/raw/universe/universe_monthly.parquet")[["snapshot_date", "snapshot_id"]].drop_duplicates()
    merged = h1.merge(h2, on="snapshot_date", suffixes=("_a", "_b"))
    assert (merged["snapshot_id_a"] == merged["snapshot_id_b"]).all(), "snapshot_id is not reproducible"


def test_no_duplicate_symbol_or_cmc_id_per_snapshot(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, _ = _read(tmp_path)
    assert not uni.duplicated(["snapshot_date", "symbol"]).any()
    assert not uni.duplicated(["snapshot_date", "cmc_id"]).any()
    assert uni["cmc_id"].notna().all()


def test_no_future_snapshots(tmp_path):
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    uni, _ = _read(tmp_path)
    now = pd.Timestamp.now(tz="UTC")
    assert (pd.to_datetime(uni["snapshot_date"], utc=True) <= now).all()


def test_verifier_rejects_tampered_survivor_flag(tmp_path):
    import json
    cfg = _cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    man_path = tmp_path / "data/raw/universe/universe_manifest.json"
    man = json.load(open(man_path))
    man["survivor_only_universe"] = True  # tamper
    json.dump(man, open(man_path, "w"))
    failures, _ = inspect_universe_outputs(cfg)
    assert any("survivor_only" in f for f in failures)


# ----- classification unit tests (pin the Task #3 bug fixes) -----
@pytest.fixture
def classifier(tmp_path):
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    agent = UniverseAgent(cfg)
    agent.prepare()
    return agent


@pytest.mark.parametrize("symbol,name,tags,expected", [
    ("USDT", "Tether USDt", ["stablecoin", "usd-stablecoin"], {"is_stablecoin"}),
    ("STETH", "Lido Staked ETH", ["liquid-staking-derivatives"], {"is_lst"}),
    ("WBTC", "Wrapped Bitcoin", ["defi"], {"is_wrapped"}),
    ("PAXG", "PAX Gold", ["tokenized-gold"], {"is_synthetic_pegged"}),
    ("ADA", "Cardano", ["pos", "staking", "platform"], set()),         # staking != LST
    ("NEAR", "Near", ["staking", "platform"], set()),                  # staking != LST
    ("HYPE", "Hyperliquid", ["derivatives", "defi"], set()),           # derivatives != synthetic
    ("LINK", "Chainlink", ["oracles", "tokenized-stock"], set()),      # stray tag != synthetic
    ("BTC", "Bitcoin", ["pow"], set()),
])
def test_classification_flags(classifier, symbol, name, tags, expected):
    row = pd.Series({"symbol": symbol, "name": name, "raw_category_tags": tags})
    flags = classifier._classification_flags(row)
    hot = {k for k, v in flags.items() if v}
    assert hot == expected, f"{symbol}: got {hot}, expected {expected}"
