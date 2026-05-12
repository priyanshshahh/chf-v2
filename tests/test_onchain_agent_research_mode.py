from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pandas as pd
import pytest

import agents.onchain_agent as onchain_module
import main as main_module
from agents.onchain_agent import OnChainAgent
from configs.config import load_config
from providers.coinmetrics import CoinMetricsProvider
from providers.defillama import DeFiLlamaProvider
from scripts.verify_onchain_run import inspect_onchain_outputs, validate_onchain_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    onchain = dict(cfg["onchain"])
    onchain.update(cfg["onchain_dev"])
    onchain["minimum_assets_with_any_onchain"] = 1
    onchain["minimum_total_metric_observations"] = 10
    onchain["minimum_assets_with_defillama"] = 0
    onchain["minimum_defillama_observations"] = 0
    onchain["min_history_days"] = 10
    onchain["max_assets"] = 10
    onchain["coinmetrics"]["enabled"] = True
    onchain["defillama"]["enabled"] = True
    onchain["etherscan"]["enabled"] = False
    onchain["thegraph"]["enabled"] = False
    onchain["blockchair"]["enabled"] = False
    onchain["dune"]["enabled"] = False
    cfg["onchain"] = onchain
    return cfg


def _patch_now(monkeypatch) -> None:
    fixed_now = pd.Timestamp("2026-04-28T12:00:00Z")
    monkeypatch.setattr(OnChainAgent, "_now_utc", lambda self: fixed_now)


def _write_universe(tmp_path: Path, symbols: list[str]) -> None:
    out_dir = tmp_path / "data" / "raw" / "universe"
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = pd.Timestamp("2026-04-01T00:00:00Z")
    coin_ids = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "BNB": "binancecoin",
        "XRP": "ripple",
        "UNI": "uniswap",
        "AAVE": "aave",
    }
    rows = []
    for rank, symbol in enumerate(symbols, start=1):
        rows.append(
            {
                "snapshot_date": latest,
                "is_eligible": True,
                "symbol": symbol,
                "name": symbol.title(),
                "coin_id": coin_ids.get(symbol, symbol.lower()),
                "provider_asset_id": coin_ids.get(symbol, symbol.lower()),
                "market_cap_rank": rank,
                "snapshot_id": "u-snap",
            }
        )
    pd.DataFrame(rows).to_parquet(out_dir / "universe_monthly.parquet", index=False)


def _write_market(tmp_path: Path, symbols: list[str], start: str = "2026-03-15", end: str = "2026-04-27") -> None:
    out_dir = tmp_path / "data" / "raw" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    cov_rows = [{"symbol": s, "passed_qa": True, "is_full_ohlcv": True} for s in symbols]
    pd.DataFrame(cov_rows).to_parquet(out_dir / "market_coverage_report.parquet", index=False)
    dates = pd.date_range(start, end, freq="D", tz="UTC")
    market_rows = [{"symbol": s, "date_ts": d} for s in symbols for d in dates]
    pd.DataFrame(market_rows).to_parquet(out_dir / "market_ohlcv.parquet", index=False)
    with open(out_dir / "market_manifest.json", "w") as f:
        json.dump({"snapshot_id": "market-snap-1"}, f)


def _read_outputs(tmp_path: Path, output_subdir: str = "data/raw/onchain_dev"):
    out_dir = tmp_path / output_subdir
    obs = pd.read_parquet(out_dir / "onchain_observations.parquet")
    wide = pd.read_parquet(out_dir / "onchain_wide.parquet")
    cov = pd.read_parquet(out_dir / "onchain_coverage_report.parquet")
    with open(out_dir / "onchain_manifest.json", "r") as f:
        manifest = json.load(f)
    return obs, wide, cov, manifest


def test_coinmetrics_current_working_path_still_passes(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["BTC", "ETH"])
    _write_market(tmp_path, ["BTC", "ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, cov, _ = _read_outputs(tmp_path)
    assert "coinmetrics" in set(obs["source"])
    assert bool(cov["coinmetrics_available"].all()) is True


def test_defillama_sol_chain_tvl_persists(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 10
    _write_universe(tmp_path, ["SOL"])
    _write_market(tmp_path, ["SOL"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, cov, manifest = _read_outputs(tmp_path)
    assert "chain_tvl_usd" in set(obs["metric_name"])
    assert "defillama" in set(obs["source"])
    assert bool(cov.iloc[0]["defillama_available"]) is True
    assert manifest["assets_with_defillama"] > 0


def test_defillama_bnb_bsc_chain_tvl_persists(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["BNB"])
    _write_market(tmp_path, ["BNB"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, _ = _read_outputs(tmp_path)
    bnb = obs[obs["symbol"] == "BNB"]
    assert "chain_tvl_usd" in set(bnb["metric_name"])


def test_defillama_eth_can_coexist_with_coinmetrics_eth_metrics(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 10
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, manifest = _read_outputs(tmp_path)
    eth = obs[obs["symbol"] == "ETH"]
    assert {"coinmetrics", "defillama"}.issubset(set(eth["source"]))
    assert manifest["assets_with_coinmetrics"] >= 1
    assert manifest["assets_with_defillama"] >= 1


def test_chain_aliases_are_exact_and_controlled_no_fuzzy_match(tmp_path):
    provider = DeFiLlamaProvider(
        OnChainAgent(_cfg(tmp_path)).http,
        {"enabled": True, "use_fixtures": True, "live_api_enabled": False, "force_refresh": False},
        fixture_dir=Path("tests/fixtures/onchain"),
    )
    provider._chains = [{"name": "Solana Classic", "tokenSymbol": "NOTSOL", "gecko_id": "not-sol"}]
    mapping = provider.resolve_mapping("SOL", "solana", "Solana")
    assert mapping.chain_slug is None


def test_protocol_alias_uni_uniswap_persists_protocol_tvl(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["UNI"])
    _write_market(tmp_path, ["UNI"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, _ = _read_outputs(tmp_path)
    uni = obs[obs["symbol"] == "UNI"]
    assert "protocol_tvl_usd" in set(uni["metric_name"])


def test_protocol_alias_aave_persists_protocol_tvl(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["AAVE"])
    _write_market(tmp_path, ["AAVE"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, _ = _read_outputs(tmp_path)
    aave = obs[obs["symbol"] == "AAVE"]
    assert "protocol_tvl_usd" in set(aave["metric_name"])


def test_missing_defillama_mapping_records_reason(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["XRP"])
    _write_market(tmp_path, ["XRP"])
    assert not OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    assert (out_dir / "data_quality_onchain.md").exists()
    assert not (out_dir / "onchain_observations.parquet").exists()
    assert not (out_dir / "onchain_coverage_report.parquet").exists()


def test_negative_tvl_rejected(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])

    def fake_fetch(*args, **kwargs):
        return type("Result", (), {
            "mapping": None,
            "fetched_metrics": ["chain_tvl_usd"],
            "failure_reason": "",
            "observations": pd.DataFrame(
                [{
                    "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                    "symbol": "ETH",
                    "metric_name": "chain_tvl_usd",
                    "metric_value": -1.0,
                    "source": "defillama",
                    "provider_asset_id": "Ethereum",
                    "provider_metric_name": "chain_tvl_usd",
                    "provider_entity_id": "Ethereum",
                    "data_type": "chain_tvl",
                }]
            ),
        })()

    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", fake_fetch)
    assert not OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    assert (out_dir / "data_quality_onchain.md").exists()
    assert not (out_dir / "onchain_observations.parquet").exists()
    assert not (out_dir / "onchain_coverage_report.parquet").exists()


def test_current_day_defillama_row_dropped(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])

    def fake_fetch(*args, **kwargs):
        return type("Result", (), {
            "mapping": None,
            "fetched_metrics": ["chain_tvl_usd"],
            "failure_reason": "",
            "observations": pd.DataFrame(
                [
                    {
                        "date_ts": pd.Timestamp("2026-04-27T12:00:00Z"),
                        "symbol": "ETH",
                        "metric_name": "chain_tvl_usd",
                        "metric_value": 1.0,
                        "source": "defillama",
                        "provider_asset_id": "Ethereum",
                        "provider_metric_name": "chain_tvl_usd",
                        "provider_entity_id": "Ethereum",
                        "data_type": "chain_tvl",
                    },
                    {
                        "date_ts": pd.Timestamp("2026-04-28T12:00:00Z"),
                        "symbol": "ETH",
                        "metric_name": "chain_tvl_usd",
                        "metric_value": 2.0,
                        "source": "defillama",
                        "provider_asset_id": "Ethereum",
                        "provider_metric_name": "chain_tvl_usd",
                        "provider_entity_id": "Ethereum",
                        "data_type": "chain_tvl",
                    },
                ]
            ),
        })()

    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", fake_fetch)
    cfg["onchain"]["min_history_days"] = 1
    cfg["onchain"]["minimum_total_metric_observations"] = 1
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 1
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, _ = _read_outputs(tmp_path)
    assert pd.Timestamp("2026-04-28T00:00:00Z") not in set(pd.to_datetime(obs["date_ts"], utc=True))


def test_etherscan_missing_key_recorded_not_fatal(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["etherscan"]["enabled"] = True
    cfg["onchain"]["use_fixtures"] = False
    cfg["onchain"]["coinmetrics"]["use_fixtures"] = False
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])

    def fake_cm(*args, **kwargs):
        return type("Result", (), {
            "asset_id": "btc",
            "available_metrics": ["tx_count"],
            "failure_reason": "",
            "observations": pd.DataFrame([{
                "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                "symbol": "BTC",
                "metric_name": "tx_count",
                "metric_value": 1.0,
                "source": "coinmetrics",
                "provider_asset_id": "btc",
                "provider_metric_name": "TxCnt",
                "provider_entity_id": "btc",
                "data_type": "asset_metric",
            }]),
        })()

    monkeypatch.setattr(CoinMetricsProvider, "get_asset_metrics_cached", fake_cm)
    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", lambda *a, **k: type("R", (), {"mapping": None, "observations": pd.DataFrame(), "fetched_metrics": [], "failure_reason": "no_defillama_chain_mapping"})())
    cfg["onchain"]["min_history_days"] = 1
    cfg["onchain"]["minimum_total_metric_observations"] = 1
    assert OnChainAgent(cfg).execute(max_retries=1)
    _, _, _, manifest = _read_outputs(tmp_path)
    assert "etherscan" in manifest["providers_unavailable"]


def test_etherscan_fixture_rows_persist_when_key_config_mocked(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "x")
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["defillama"]["enabled"] = False
    cfg["onchain"]["etherscan"]["enabled"] = True
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, cov, manifest = _read_outputs(tmp_path)
    assert "etherscan" in set(obs["source"])
    assert bool(cov.iloc[0]["etherscan_available"]) is True
    assert manifest["assets_with_etherscan"] >= 1


def test_thegraph_missing_key_config_recorded_not_fatal(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    monkeypatch.delenv("GRAPH_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["use_fixtures"] = False
    cfg["onchain"]["thegraph"]["enabled"] = True
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["defillama"]["enabled"] = False
    _write_universe(tmp_path, ["UNI"])
    _write_market(tmp_path, ["UNI"])
    assert not OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    assert (out_dir / "data_quality_onchain.md").exists()
    assert not (out_dir / "onchain_manifest.json").exists()


def test_thegraph_fixture_parser_persists_when_configured(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["defillama"]["enabled"] = False
    cfg["onchain"]["thegraph"]["enabled"] = True
    cfg["onchain"]["thegraph"]["configured_subgraphs"] = {
        "UNI": {
            "endpoint": "https://example.com/subgraph",
            "root_field": "dailySnapshots",
            "query": "query Test { dailySnapshots { date volumeUSD tvlUSD feesUSD } }",
            "metric_fields": {
                "protocol_volume_usd": "volumeUSD",
                "protocol_tvl_usd": "tvlUSD",
                "protocol_fees_usd": "feesUSD",
            },
        }
    }
    _write_universe(tmp_path, ["UNI"])
    _write_market(tmp_path, ["UNI"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, cov, manifest = _read_outputs(tmp_path)
    assert "thegraph" in set(obs["source"])
    assert bool(cov.iloc[0]["thegraph_available"]) is True
    assert manifest["assets_with_thegraph"] >= 1


def test_blockchair_disabled_by_default_and_does_not_run(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    _, _, cov, manifest = _read_outputs(tmp_path)
    assert bool(cov.iloc[0]["blockchair_available"]) is False
    assert manifest["assets_with_blockchair"] == 0


def test_dune_disabled_by_default_and_does_not_run(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    _, _, cov, manifest = _read_outputs(tmp_path)
    assert bool(cov.iloc[0]["dune_available"]) is False
    assert manifest["assets_with_dune"] == 0


def test_coverage_report_includes_every_provider_availability_column(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    _, _, cov, _ = _read_outputs(tmp_path)
    expected = {
        "coinmetrics_available",
        "defillama_available",
        "etherscan_available",
        "thegraph_available",
        "blockchair_available",
        "dune_available",
    }
    assert expected.issubset(cov.columns)


def test_manifest_includes_provider_counts_and_unavailable_providers(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["thegraph"]["enabled"] = True
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    _, _, _, manifest = _read_outputs(tmp_path)
    assert "providers_unavailable" in manifest
    assert "assets_with_coinmetrics" in manifest
    assert "assets_with_defillama" in manifest


def test_verifier_fails_if_defillama_minimum_required_but_no_defillama_rows(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["defillama"]["enabled"] = False
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 1
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])
    assert not OnChainAgent(cfg).execute(max_retries=1)
    failures = validate_onchain_outputs(cfg)
    assert failures


def test_verifier_passes_when_coinmetrics_and_defillama_rows_exist(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 10
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    assert validate_onchain_outputs(cfg) == []


def test_below_min_history_asset_is_not_fetched_any_or_counted(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["min_history_days"] = 30
    cfg["onchain"]["minimum_assets_with_any_onchain"] = 0
    cfg["onchain"]["minimum_total_metric_observations"] = 0
    _write_universe(tmp_path, ["GALA"])
    _write_market(tmp_path, ["GALA"])

    def fake_fetch(*args, **kwargs):
        return type("Result", (), {
            "mapping": None,
            "fetched_metrics": ["chain_tvl_usd"],
            "failure_reason": "",
            "observations": pd.DataFrame(
                [
                    {
                        "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                        "symbol": "GALA",
                        "metric_name": "chain_tvl_usd",
                        "metric_value": 10.0,
                        "source": "defillama",
                        "provider_asset_id": "Gala",
                        "provider_metric_name": "chain_tvl_usd",
                        "provider_entity_id": "Gala",
                        "data_type": "chain_tvl",
                    },
                    {
                        "date_ts": pd.Timestamp("2026-04-21T00:00:00Z"),
                        "symbol": "GALA",
                        "metric_name": "chain_tvl_usd",
                        "metric_value": 11.0,
                        "source": "defillama",
                        "provider_asset_id": "Gala",
                        "provider_metric_name": "chain_tvl_usd",
                        "provider_entity_id": "Gala",
                        "data_type": "chain_tvl",
                    },
                ]
            ),
        })()

    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", fake_fetch)
    assert not OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    assert (out_dir / "data_quality_onchain.md").exists()
    assert not (out_dir / "onchain_observations.parquet").exists()
    assert not (out_dir / "onchain_coverage_report.parquet").exists()


def test_manifest_counts_are_based_on_persisted_observations_only(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["coinmetrics"]["enabled"] = False
    cfg["onchain"]["min_history_days"] = 2
    cfg["onchain"]["minimum_assets_with_any_onchain"] = 1
    cfg["onchain"]["minimum_total_metric_observations"] = 2
    cfg["onchain"]["minimum_assets_with_defillama"] = 1
    cfg["onchain"]["minimum_defillama_observations"] = 2
    _write_universe(tmp_path, ["ETH", "GALA"])
    _write_market(tmp_path, ["ETH", "GALA"])

    def fake_fetch(self, *, symbol, **kwargs):
        if symbol == "ETH":
            rows = [
                {
                    "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                    "symbol": "ETH",
                    "metric_name": "chain_tvl_usd",
                    "metric_value": 1.0,
                    "source": "defillama",
                    "provider_asset_id": "Ethereum",
                    "provider_metric_name": "chain_tvl_usd",
                    "provider_entity_id": "Ethereum",
                    "data_type": "chain_tvl",
                },
                {
                    "date_ts": pd.Timestamp("2026-04-21T00:00:00Z"),
                    "symbol": "ETH",
                    "metric_name": "chain_tvl_usd",
                    "metric_value": 2.0,
                    "source": "defillama",
                    "provider_asset_id": "Ethereum",
                    "provider_metric_name": "chain_tvl_usd",
                    "provider_entity_id": "Ethereum",
                    "data_type": "chain_tvl",
                },
            ]
        else:
            rows = [
                {
                    "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                    "symbol": "GALA",
                    "metric_name": "chain_tvl_usd",
                    "metric_value": 3.0,
                    "source": "defillama",
                    "provider_asset_id": "Gala",
                    "provider_metric_name": "chain_tvl_usd",
                    "provider_entity_id": "Gala",
                    "data_type": "chain_tvl",
                }
            ]
        return type("Result", (), {
            "mapping": None,
            "fetched_metrics": ["chain_tvl_usd"],
            "failure_reason": "",
            "observations": pd.DataFrame(rows),
        })()

    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", fake_fetch)
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, cov, manifest = _read_outputs(tmp_path)
    assert set(obs["symbol"]) == {"ETH"}
    assert set(cov.loc[cov["fetched_any"], "symbol"]) == {"ETH"}
    assert set(cov.loc[cov["passed_qa"], "symbol"]) == {"ETH"}
    assert manifest["assets_with_any_onchain"] == 1
    assert manifest["assets_with_defillama"] == 1
    assert manifest["total_observations"] == len(obs)


def test_verifier_fails_on_fetched_any_persisted_symbol_mismatch(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    cov = pd.read_parquet(out_dir / "onchain_coverage_report.parquet")
    cov.loc[:, "fetched_any"] = True
    cov = pd.concat([cov, cov.iloc[[0]].assign(symbol="GALA", passed_qa=False, fetched_any=True)], ignore_index=True)
    cov.to_parquet(out_dir / "onchain_coverage_report.parquet", index=False)
    failures = validate_onchain_outputs(cfg)
    assert any("coverage fetched_any symbols do not match persisted observation symbols" in f for f in failures)


def test_verifier_fails_on_manifest_observation_count_mismatch(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    _write_universe(tmp_path, ["ETH"])
    _write_market(tmp_path, ["ETH"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    manifest_path = out_dir / "onchain_manifest.json"
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    manifest["total_observations"] = manifest["total_observations"] + 1
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)
    failures = validate_onchain_outputs(cfg)
    assert any("manifest total_observations does not match observations parquet" in f for f in failures)


def test_provider_failure_reasons_combine_coinmetrics_and_defillama(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["minimum_assets_with_any_onchain"] = 0
    cfg["onchain"]["minimum_total_metric_observations"] = 0
    _write_universe(tmp_path, ["XRP"])
    _write_market(tmp_path, ["XRP"])

    def fake_cm(*args, **kwargs):
        return type("Result", (), {
            "asset_id": "xrp",
            "available_metrics": [],
            "failure_reason": "no_supported_coinmetrics_metrics",
            "observations": pd.DataFrame(),
        })()

    def fake_dl(*args, **kwargs):
        return type("Result", (), {
            "mapping": None,
            "fetched_metrics": [],
            "failure_reason": "no_defillama_chain_mapping",
            "observations": pd.DataFrame(),
        })()

    monkeypatch.setattr(CoinMetricsProvider, "get_asset_metrics_cached", fake_cm)
    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", fake_dl)
    assert not OnChainAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    assert (out_dir / "data_quality_onchain.md").exists()
    assert not (out_dir / "onchain_coverage_report.parquet").exists()


def test_no_fake_rows_created_when_providers_unavailable(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("GRAPH_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["etherscan"]["enabled"] = True
    cfg["onchain"]["thegraph"]["enabled"] = True
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])
    assert OnChainAgent(cfg).execute(max_retries=1)
    obs, _, _, _ = _read_outputs(tmp_path)
    assert "etherscan" not in set(obs["source"])
    assert "thegraph" not in set(obs["source"])


def test_optional_providers_do_not_cause_failure_when_missing_keys(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("GRAPH_API_KEY", raising=False)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["use_fixtures"] = False
    cfg["onchain"]["etherscan"]["enabled"] = True
    cfg["onchain"]["thegraph"]["enabled"] = True
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])

    def fake_cm(*args, **kwargs):
        return type("Result", (), {
            "asset_id": "btc",
            "available_metrics": ["tx_count"],
            "failure_reason": "",
            "observations": pd.DataFrame([{
                "date_ts": pd.Timestamp("2026-04-20T00:00:00Z"),
                "symbol": "BTC",
                "metric_name": "tx_count",
                "metric_value": 1.0,
                "source": "coinmetrics",
                "provider_asset_id": "btc",
                "provider_metric_name": "TxCnt",
                "provider_entity_id": "btc",
                "data_type": "asset_metric",
            }]),
        })()

    monkeypatch.setattr(CoinMetricsProvider, "get_asset_metrics_cached", fake_cm)
    monkeypatch.setattr(DeFiLlamaProvider, "fetch_symbol_metrics", lambda *a, **k: type("R", (), {"mapping": None, "observations": pd.DataFrame(), "fetched_metrics": [], "failure_reason": "no_defillama_chain_mapping"})())
    cfg["onchain"]["min_history_days"] = 1
    cfg["onchain"]["minimum_total_metric_observations"] = 1
    assert OnChainAgent(cfg).execute(max_retries=1)


def test_verifier_fails_gracefully_on_missing_columns(tmp_path):
    cfg = _cfg(tmp_path)
    out_dir = tmp_path / "data" / "raw" / "onchain_dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"symbol": "BTC"}]).to_parquet(out_dir / "onchain_observations.parquet", index=False)
    pd.DataFrame([{"symbol": "BTC"}]).to_parquet(out_dir / "onchain_wide.parquet", index=False)
    pd.DataFrame([{"symbol": "BTC"}]).to_parquet(out_dir / "onchain_coverage_report.parquet", index=False)
    with open(out_dir / "onchain_manifest.json", "w") as f:
        json.dump({"output_files": {}}, f)
    (out_dir / "data_quality_onchain.md").write_text("x")
    failures, _ = inspect_onchain_outputs(cfg)
    assert any("missing required column" in failure for failure in failures)


def test_main_onchain_exits_nonzero_on_failure(tmp_path, monkeypatch):
    _patch_now(monkeypatch)
    cfg = _cfg(tmp_path)
    cfg["onchain"]["live_api_enabled"] = False
    cfg["onchain"]["use_fixtures"] = False
    _write_universe(tmp_path, ["BTC"])
    _write_market(tmp_path, ["BTC"])
    args = type("Args", (), {"config": None, "section": None})
    monkeypatch.setattr(main_module, "_command_cfg", lambda _args=None: cfg)
    with pytest.raises(SystemExit) as exc:
        main_module.cmd_onchain(args)
    assert exc.value.code == 1


def test_no_hardcoded_fallback_symbols():
    source = inspect.getsource(onchain_module)
    assert '["BTC", "ETH", "SOL", "ADA", "AVAX"]' not in source
