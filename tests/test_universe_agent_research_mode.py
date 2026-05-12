from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import main as main_module
from agents.market_data_agent import MarketDataAgent
from agents.universe_agent import UniverseAgent
from configs.config import load_config
from providers.http_client import CachedHttpClient
from providers.coinmarketcap import CoinMarketCapProvider
from scripts.verify_universe_run import inspect_universe_outputs, validate_universe_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    universe = dict(cfg["universe"])
    universe.update(cfg["universe_dev"])
    universe["output_dir"] = "data/raw/universe"
    universe["cache_dir"] = "data/cache"
    cfg["universe"] = universe
    return cfg


def _run_agent(tmp_path: Path, overrides: dict | None = None) -> dict:
    cfg = _cfg(tmp_path)
    if overrides:
        cfg["universe"].update(overrides)
    agent = UniverseAgent(cfg)
    assert agent.execute(max_retries=1)
    return cfg


def _cmc_cfg(tmp_path: Path) -> dict:
    cfg = _cfg(tmp_path)
    cfg["universe"].update(
        {
            "use_cmc_historical_listings": True,
            "provider_priority": ["coinmarketcap"],
            "lookback_days": 1095,
            "snapshot_frequency": "MS",
            "start_date": None,
            "end_date": None,
            "candidate_n": 3,
            "final_universe_n": 2,
            "minimum_eligible_n": 2,
            "cache_dir": "data/cache/cmc",
            "live_api_enabled": False,
            "use_fixtures": False,
        }
    )
    return cfg


def _mock_cmc_listings(monkeypatch):
    def fake_fetch(self, snapshot_date, start=1, limit=300, convert="USD", **kwargs):
        snapshot_ts = pd.Timestamp(snapshot_date)
        if snapshot_ts.tzinfo is None:
            snapshot_ts = snapshot_ts.tz_localize("UTC")
        else:
            snapshot_ts = snapshot_ts.tz_convert("UTC")
        return pd.DataFrame(
            [
                {
                    "cmc_id": 1,
                    "provider_asset_id": "1",
                    "coin_id": "bitcoin",
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "slug": "bitcoin",
                    "market_cap_rank": 1,
                    "market_cap_usd": 1_000_000_000 - snapshot_ts.month,
                    "volume_24h_usd": 10_000_000,
                    "price_usd": 40000,
                    "is_active_at_snapshot": True,
                    "raw_category_tags": ["mineable"],
                    "source": "coinmarketcap",
                },
                {
                    "cmc_id": 1027,
                    "provider_asset_id": "1027",
                    "coin_id": "ethereum",
                    "symbol": "ETH",
                    "name": "Ethereum",
                    "slug": "ethereum",
                    "market_cap_rank": 2,
                    "market_cap_usd": 500_000_000 - snapshot_ts.month,
                    "volume_24h_usd": 8_000_000,
                    "price_usd": 2500,
                    "is_active_at_snapshot": True,
                    "raw_category_tags": ["smart-contracts"],
                    "source": "coinmarketcap",
                },
                {
                    "cmc_id": 825,
                    "provider_asset_id": "825",
                    "coin_id": "tether",
                    "symbol": "USDT",
                    "name": "Tether USDt",
                    "slug": "tether",
                    "market_cap_rank": 3,
                    "market_cap_usd": 400_000_000,
                    "volume_24h_usd": 9_000_000,
                    "price_usd": 1.0,
                    "is_active_at_snapshot": True,
                    "raw_category_tags": ["stablecoin"],
                    "source": "coinmarketcap",
                },
            ]
        )

    monkeypatch.setattr(CoinMarketCapProvider, "fetch_historical_listings", fake_fetch)


def _read_outputs(tmp_path: Path):
    out = tmp_path / "data" / "raw" / "universe"
    universe = pd.read_parquet(out / "universe_monthly.parquet")
    exclusions = pd.read_parquet(out / "exclusions_monthly.parquet")
    coverage = pd.read_parquet(out / "universe_coverage_report.parquet")
    return universe, exclusions, coverage


def _read_manifest(tmp_path: Path) -> dict:
    with open(tmp_path / "data" / "raw" / "universe" / "universe_manifest.json", "r") as f:
        return json.load(f)


def test_universe_filters_stablecoins(tmp_path):
    _run_agent(tmp_path)
    universe, exclusions, _ = _read_outputs(tmp_path)
    assert "USDT" not in set(universe["symbol"])
    assert (exclusions[exclusions["symbol"] == "USDT"]["is_stablecoin"]).all()


def test_universe_filters_wrapped_assets(tmp_path):
    _run_agent(tmp_path)
    universe, exclusions, _ = _read_outputs(tmp_path)
    assert "WBTC" not in set(universe["symbol"])
    assert (exclusions[exclusions["symbol"] == "WBTC"]["is_wrapped"]).all()


def test_universe_filters_bridged_assets(tmp_path):
    _run_agent(tmp_path)
    universe, exclusions, _ = _read_outputs(tmp_path)
    assert "AXLUSDC" not in set(universe["symbol"])
    assert (exclusions[exclusions["symbol"] == "AXLUSDC"]["is_bridged"]).all()


def test_universe_filters_lst_assets(tmp_path):
    _run_agent(tmp_path)
    universe, exclusions, _ = _read_outputs(tmp_path)
    assert "STETH" not in set(universe["symbol"])
    assert (exclusions[exclusions["symbol"] == "STETH"]["is_lst"]).all()


def test_universe_keeps_top_n_after_exclusions(tmp_path):
    _run_agent(tmp_path, {"final_universe_n": 5, "minimum_eligible_n": 5})
    universe, _, _ = _read_outputs(tmp_path)
    assert len(universe) == 5
    assert universe["market_cap_usd"].is_monotonic_decreasing


def test_universe_fails_when_eligible_count_too_low(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["universe"]["minimum_eligible_n"] = 999
    agent = UniverseAgent(cfg)
    assert not agent.execute(max_retries=1)


def test_universe_writes_parquet_and_duckdb_reads_back(tmp_path):
    cfg = _run_agent(tmp_path)
    failures = validate_universe_outputs(cfg)
    assert failures == []


def test_latest_snapshot_only_mode_is_explicit(tmp_path):
    _run_agent(
        tmp_path,
        {
            "start_date": "2021-01-01",
            "end_date": None,
            "allow_latest_snapshot_only": True,
        },
    )
    manifest = _read_manifest(tmp_path)
    assert manifest["universe_mode"] == "latest_snapshot_only"
    assert manifest["requested_start_date"] == "2021-01-01"
    assert manifest["historical_snapshots_requested"] > manifest["historical_snapshots_created"]
    assert manifest["historical_snapshot_limitation"]


def test_strict_historical_mode_fails_if_only_latest_snapshot_possible(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["universe"]["start_date"] = "2021-01-01"
    cfg["universe"]["end_date"] = None
    cfg["universe"]["allow_latest_snapshot_only"] = False
    agent = UniverseAgent(cfg)
    assert not agent.execute(max_retries=1)


def test_verify_fails_when_latest_only_disallowed_by_config(tmp_path):
    cfg = _run_agent(
        tmp_path,
        {
            "start_date": "2021-01-01",
            "end_date": None,
            "allow_latest_snapshot_only": True,
        },
    )
    cfg["universe"]["allow_latest_snapshot_only"] = False
    failures, warnings = inspect_universe_outputs(cfg)
    assert any("latest_snapshot_only output is not allowed" in f for f in failures)
    assert warnings == []


def test_manifest_actual_dates_match_output_parquet(tmp_path):
    _run_agent(tmp_path, {"start_date": "2021-01-01", "end_date": None})
    universe, _, _ = _read_outputs(tmp_path)
    manifest = _read_manifest(tmp_path)
    actual_start = pd.Timestamp(universe["snapshot_date"].min()).tz_convert("UTC").date().isoformat()
    actual_end = pd.Timestamp(universe["snapshot_date"].max()).tz_convert("UTC").date().isoformat()
    assert manifest["actual_start_date"] == actual_start
    assert manifest["actual_end_date"] == actual_end


def test_universe_no_duplicate_symbol_per_snapshot(tmp_path):
    _run_agent(tmp_path)
    universe, _, _ = _read_outputs(tmp_path)
    assert not universe.duplicated(["snapshot_date", "symbol"]).any()


def test_universe_snapshot_hash_is_deterministic(tmp_path):
    _run_agent(tmp_path)
    first, _, _ = _read_outputs(tmp_path)
    _run_agent(tmp_path)
    second, _, _ = _read_outputs(tmp_path)
    assert set(first["snapshot_id"]) == set(second["snapshot_id"])


def test_universe_uses_cache_before_api(tmp_path):
    client = CachedHttpClient(tmp_path / "cache")
    path = client.cache_path("coingecko", "cached")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[{"ok": true}]')
    assert client.get_json("coingecko", "https://example.invalid", {}, "cached", live_api_enabled=False)
    assert client.cache_hit_count == 1
    assert dict(client.api_call_count_by_provider) == {}


def test_no_live_api_when_live_api_disabled(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["universe"]["use_fixtures"] = False
    cfg["universe"]["live_api_enabled"] = False
    agent = UniverseAgent(cfg)
    assert not agent.execute(max_retries=1)


def test_main_exits_nonzero_on_universe_failure(monkeypatch):
    class FailingUniverseAgent:
        output_paths = {}

        def __init__(self, cfg):
            self.cfg = cfg

        def execute(self, max_retries=1):
            return False

    import agents.universe_agent as universe_module

    monkeypatch.setattr(main_module, "_get_cfg", lambda args=None: {"_project_root": ".", "paths": {}, "universe": {}})
    monkeypatch.setattr(universe_module, "UniverseAgent", FailingUniverseAgent)
    with pytest.raises(SystemExit) as exc:
        main_module.cmd_universe(SimpleNamespace(config=None, section=None))
    assert exc.value.code == 1


def test_market_data_agent_rejects_missing_universe_in_research_mode(tmp_path):
    cfg = _cfg(tmp_path)
    agent = MarketDataAgent(cfg)
    with pytest.raises(FileNotFoundError):
        agent.prepare()


def test_no_hardcoded_default_symbols_in_research_mode(tmp_path):
    cfg = _cfg(tmp_path)
    agent = MarketDataAgent(cfg)
    with pytest.raises(FileNotFoundError):
        agent._load_symbols_from_universe()
    assert agent.symbols == []


def test_universe_cmc_builds_multiple_historical_snapshots(tmp_path, monkeypatch):
    _mock_cmc_listings(monkeypatch)
    cfg = _cmc_cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    universe, _, coverage = _read_outputs(tmp_path)
    assert universe["snapshot_date"].nunique() >= 24
    assert coverage["snapshot_date"].nunique() >= 24


def test_universe_cmc_uses_cmc_id_as_stable_key(tmp_path, monkeypatch):
    _mock_cmc_listings(monkeypatch)
    cfg = _cmc_cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    universe, _, _ = _read_outputs(tmp_path)
    assert "cmc_id" in universe.columns
    assert universe["cmc_id"].notna().all()


def test_universe_cmc_not_survivor_only(tmp_path, monkeypatch):
    _mock_cmc_listings(monkeypatch)
    cfg = _cmc_cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    manifest = _read_manifest(tmp_path)
    assert manifest["universe_mode"] == "historical_cmc_monthly"
    assert manifest["survivor_only_universe"] is False


def test_universe_cmc_writes_membership_file(tmp_path, monkeypatch):
    _mock_cmc_listings(monkeypatch)
    cfg = _cmc_cfg(tmp_path)
    assert UniverseAgent(cfg).execute(max_retries=1)
    membership_path = tmp_path / "data" / "raw" / "universe" / "universe_membership.parquet"
    assert membership_path.exists()
    membership = pd.read_parquet(membership_path)
    assert not membership.empty


def test_verify_universe_rejects_single_latest_snapshot_in_cmc_mode(tmp_path, monkeypatch):
    _mock_cmc_listings(monkeypatch)
    cfg = _cmc_cfg(tmp_path)
    cfg["universe"]["start_date"] = "2026-04-01"
    cfg["universe"]["end_date"] = "2026-04-01"
    assert UniverseAgent(cfg).execute(max_retries=1)
    failures, _warnings = inspect_universe_outputs(cfg)
    assert any("historical_snapshots_created < 24" in f for f in failures)


def test_verify_universe_rejects_missing_cmc_id(tmp_path):
    cfg = _cmc_cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "universe"
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = pd.Timestamp("2024-01-01T00:00:00Z")
    base = pd.DataFrame(
        [
            {
                "snapshot_date": snapshot,
                "snapshot_year": 2024,
                "snapshot_month": 1,
                "snapshot_id": "abc",
                "provider": "coinmarketcap",
                "provider_asset_id": "1",
                "cmc_id": None,
                "coin_id": "bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "slug": "bitcoin",
                "market_cap_rank": 1,
                "market_cap_usd": 1.0,
                "volume_24h_usd": 1.0,
                "price_usd": 1.0,
                "is_active_at_snapshot": True,
                "is_stablecoin": False,
                "is_wrapped": False,
                "is_bridged": False,
                "is_lst": False,
                "is_synthetic_pegged": False,
                "is_mature_365d": None,
                "is_exchange_tradable": None,
                "exchange": "",
                "exchange_symbol": "",
                "has_onchain_coverage": None,
                "onchain_coverage_source": "",
                "is_eligible": True,
                "exclusion_reason": "",
                "source": "coinmarketcap",
                "created_at_utc": "2026-04-30T00:00:00+00:00",
            }
        ]
    )
    base.to_parquet(out_dir / "universe_monthly.parquet", index=False)
    base.assign(snapshot_month="2024-01").reindex(columns=[
        "snapshot_date","snapshot_month","cmc_id","symbol","name","slug","market_cap_rank","market_cap_usd","is_eligible","exclusion_reason","source"
    ]).to_parquet(out_dir / "universe_membership.parquet", index=False)
    pd.DataFrame([{"snapshot_date": snapshot, "eligible_count": 50, "passed_validation": True}]).to_parquet(out_dir / "universe_coverage_report.parquet", index=False)
    pd.DataFrame(columns=list(base.columns)+["exclusion_stage","exclusion_rule","raw_category_tags"]).to_parquet(out_dir / "exclusions_monthly.parquet", index=False)
    with open(out_dir / "universe_manifest.json", "w") as fh:
        json.dump(
            {
                "output_files": {"universe": str(out_dir / "universe_monthly.parquet")},
                "total_eligible_rows": 1,
                "universe_mode": "historical_cmc_monthly",
                "survivor_only_universe": False,
                "requested_start_date": "2024-01-01",
                "requested_end_date": "2024-01-01",
                "actual_start_date": "2024-01-01",
                "actual_end_date": "2024-01-01",
                "historical_snapshots_requested": 24,
                "historical_snapshots_created": 24,
                "historical_snapshot_limitation": "",
            },
            fh,
        )
    failures, _warnings = inspect_universe_outputs(cfg)
    assert any("cmc_id" in f for f in failures)
