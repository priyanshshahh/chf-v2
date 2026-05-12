from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.feature_agent import FeatureAgent
from configs.config import load_config
from scripts.verify_feature_run import inspect_feature_outputs, validate_feature_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    features = dict(cfg["features"])
    features.update(cfg["features_smoke"])
    features["output_dir"] = "data/features_smoke"
    features["min_market_symbols_required"] = 3
    features["min_onchain_symbols_required"] = 2
    features["min_full_feature_symbols_required"] = 3
    features["min_rows_required"] = 50
    features["pruning"]["enabled"] = True
    cfg["features"] = features
    return cfg


def _write_upstream(
    tmp_path: Path,
    *,
    symbols: list[str],
    onchain_symbols: list[str],
    periods: int = 120,
    start: str = "2026-01-01",
) -> None:
    root = tmp_path / "data" / "raw"
    (root / "market").mkdir(parents=True, exist_ok=True)
    (root / "onchain").mkdir(parents=True, exist_ok=True)
    (root / "universe").mkdir(parents=True, exist_ok=True)

    dates = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    universe_rows = []
    coin_ids = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "XRP": "ripple",
        "AAVE": "aave",
        "UNI": "uniswap",
        "BNB": "binancecoin",
        "GALA": "gala",
    }
    for rank, symbol in enumerate(symbols, start=1):
        universe_rows.append(
            {
                "snapshot_date": pd.Timestamp("2026-04-01T00:00:00Z"),
                "snapshot_id": "u-snap",
                "symbol": symbol,
                "coin_id": coin_ids.get(symbol, symbol.lower()),
                "provider_asset_id": coin_ids.get(symbol, symbol.lower()),
                "name": symbol,
                "market_cap_rank": rank,
                "is_eligible": True,
            }
        )
    pd.DataFrame(universe_rows).to_parquet(root / "universe" / "universe_monthly.parquet", index=False)
    with open(root / "universe" / "universe_manifest.json", "w") as f:
        json.dump({"snapshot_hashes": ["u-snap"], "monthly_snapshot_count": 1}, f)

    market_rows = []
    for idx, symbol in enumerate(symbols):
        base = 100 + idx * 15
        for i, date_ts in enumerate(dates):
            trend = base + i * (1 + idx * 0.02)
            shock = 400 if symbol == "BTC" and i == 70 else 0
            close = trend + shock
            open_ = close * 0.99
            high = close * 1.02
            low = close * 0.98
            volume = 1000 + idx * 100 + (i % 9) * 10
            if symbol == "ETH" and i == 60:
                volume = 1000000
            market_rows.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "exchange": "coinbase",
                    "exchange_symbol": f"{symbol}/USD",
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "source": "coinbase",
                    "snapshot_id": "market-snap",
                    "fetched_at_utc": "2026-04-29T00:00:00+00:00",
                    "is_forward_filled": bool(symbol == "SOL" and i == 40),
                    "is_incomplete_dropped": False,
                    "data_type": "exchange_ohlcv",
                    "is_full_ohlcv": True,
                    "quote_currency": "USD",
                }
            )
    pd.DataFrame(market_rows).to_parquet(root / "market" / "market_ohlcv.parquet", index=False)
    with open(root / "market" / "market_manifest.json", "w") as f:
        json.dump({"snapshot_id": "market-snap", "requested_assets": len(symbols), "fetched_assets": len(symbols), "full_ohlcv_assets": len(symbols)}, f)

    onchain_rows = []
    obs_rows = []
    for idx, symbol in enumerate(onchain_symbols):
        for i, date_ts in enumerate(dates):
            adr = 1000 + idx * 50 + i
            tx = 500 + idx * 20 + i * 0.5
            supply = 1_000_000 + idx * 1000 + i * 5
            mcap = 50_000_000 + idx * 1_000_000 + i * 5000
            mvrv = 1.2 + (i % 15) * 0.01
            chain_tvl = 5_000_000 + idx * 100_000 + i * 2000
            protocol_tvl = chain_tvl * 0.7 if symbol in {"AAVE", "UNI"} else np.nan
            fees = 20_000 + idx * 100 + i * 20 if symbol in {"ETH", "AAVE", "UNI"} else np.nan
            dex_volume = 500_000 + idx * 5000 + i * 100 if symbol in {"ETH", "UNI"} else np.nan
            onchain_rows.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "adr_active_count": adr,
                    "tx_count": tx,
                    "realized_cap_usd": np.nan,
                    "mvrv_current": mvrv,
                    "nvt_adjusted": np.nan,
                    "fee_total_usd": np.nan,
                    "transfer_value_adjusted_usd": np.nan,
                    "current_supply": supply,
                    "market_cap_usd": mcap,
                    "issuance_total_usd": 1000 + i,
                    "chain_tvl_usd": chain_tvl if symbol in {"ETH", "SOL", "BNB", "UNI", "AAVE"} else np.nan,
                    "protocol_tvl_usd": protocol_tvl,
                    "fees_usd": fees,
                    "revenue_usd": np.nan,
                    "dex_volume_usd": dex_volume,
                    "stablecoin_mcap_usd": np.nan,
                    "pool_tvl_usd": np.nan,
                    "pool_apy": np.nan,
                    "gas_used": np.nan,
                    "transaction_count_proxy": np.nan,
                    "token_transfer_count_proxy": np.nan,
                    "protocol_volume_usd": np.nan,
                    "snapshot_id": "onchain-snap",
                    "fetched_at_utc": "2026-04-29T00:00:00+00:00",
                }
            )
            obs_rows.extend(
                [
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": "adr_active_count",
                        "metric_value": adr,
                        "source": "coinmetrics",
                        "provider_asset_id": symbol.lower(),
                        "provider_metric_name": "AdrActCnt",
                        "provider_entity_id": symbol.lower(),
                        "data_type": "asset_metric",
                        "snapshot_id": "onchain-snap",
                        "fetched_at_utc": "2026-04-29T00:00:00+00:00",
                        "is_forward_filled": False,
                        "is_incomplete_dropped": False,
                    },
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": "tx_count",
                        "metric_value": tx,
                        "source": "coinmetrics",
                        "provider_asset_id": symbol.lower(),
                        "provider_metric_name": "TxCnt",
                        "provider_entity_id": symbol.lower(),
                        "data_type": "asset_metric",
                        "snapshot_id": "onchain-snap",
                        "fetched_at_utc": "2026-04-29T00:00:00+00:00",
                        "is_forward_filled": False,
                        "is_incomplete_dropped": False,
                    },
                ]
            )
            if symbol in {"ETH", "SOL", "BNB", "UNI", "AAVE"}:
                obs_rows.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": "chain_tvl_usd",
                        "metric_value": chain_tvl,
                        "source": "defillama",
                        "provider_asset_id": symbol.lower(),
                        "provider_metric_name": "chain_tvl_usd",
                        "provider_entity_id": symbol,
                        "data_type": "chain_tvl",
                        "snapshot_id": "onchain-snap",
                        "fetched_at_utc": "2026-04-29T00:00:00+00:00",
                        "is_forward_filled": False,
                        "is_incomplete_dropped": False,
                    }
                )
    pd.DataFrame(onchain_rows).to_parquet(root / "onchain" / "onchain_wide.parquet", index=False)
    pd.DataFrame(obs_rows).to_parquet(root / "onchain" / "onchain_observations.parquet", index=False)
    with open(root / "onchain" / "onchain_manifest.json", "w") as f:
        json.dump({"snapshot_id": "onchain-snap", "requested_assets": len(symbols), "assets_with_any_onchain": len(onchain_symbols), "assets_with_defillama": len(onchain_symbols)}, f)


def _read_outputs(tmp_path: Path, out_subdir: str = "data/features_smoke"):
    out_dir = tmp_path / out_subdir
    market = pd.read_parquet(out_dir / "market_features.parquet")
    onchain = pd.read_parquet(out_dir / "onchain_features.parquet")
    full = pd.read_parquet(out_dir / "full_features.parquet")
    coverage = pd.read_parquet(out_dir / "feature_coverage_report.parquet")
    with open(out_dir / "feature_manifest.json", "r") as f:
        manifest = json.load(f)
    with open(out_dir / "feature_dictionary.json", "r") as f:
        dictionary = json.load(f)
    with open(out_dir / "feature_keep_list.json", "r") as f:
        keep = json.load(f)
    return market, onchain, full, coverage, manifest, dictionary, keep


def test_feature_agent_requires_canonical_market_file(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        FeatureAgent(cfg).prepare()


def test_feature_agent_does_not_use_old_per_symbol_market_pattern(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    old_dir = tmp_path / "data" / "raw" / "market"
    pd.DataFrame([{"bad": 1}]).to_parquet(old_dir / "BTC_ohlcv.parquet", index=False)
    assert FeatureAgent(cfg).execute(max_retries=1)


def test_feature_agent_loads_onchain_wide_not_old_onchain_files(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    old_dir = tmp_path / "data" / "raw" / "onchain"
    pd.DataFrame([{"bad": 1}]).to_parquet(old_dir / "BTC_onchain.parquet", index=False)
    assert FeatureAgent(cfg).execute(max_retries=1)


def test_market_features_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "market_features.parquet").exists()


def test_onchain_features_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "onchain_features.parquet").exists()


def test_full_features_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "full_features.parquet").exists()


def test_feature_manifest_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "feature_manifest.json").exists()


def test_feature_coverage_report_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "feature_coverage_report.parquet").exists()


def test_feature_dictionary_written(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "features_smoke" / "feature_dictionary.json").exists()


def test_no_duplicate_symbol_date(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    market, onchain, full, _, _, _, _ = _read_outputs(tmp_path)
    assert not market.duplicated(["symbol", "date_ts"]).any()
    assert not onchain.duplicated(["symbol", "date_ts"]).any()
    assert not full.duplicated(["symbol", "date_ts"]).any()


def test_no_target_or_label_columns(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    _, _, full, _, _, _, _ = _read_outputs(tmp_path)
    lower_cols = [c.lower() for c in full.columns if c != "is_forward_filled_market"]
    assert not any(any(token in col for token in ["target", "label", "forward", "future", "lead"]) for col in lower_cols)


def test_onchain_features_are_lagged_before_join(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    _, onchain, _, _, _, _, _ = _read_outputs(tmp_path)
    eth = onchain[onchain["symbol"] == "ETH"].sort_values("date_ts").reset_index(drop=True)
    raw = pd.read_parquet(tmp_path / "data" / "raw" / "onchain" / "onchain_wide.parquet")
    raw = raw[raw["symbol"] == "ETH"].sort_values("date_ts").reset_index(drop=True)
    assert eth.loc[1, "chain_tvl_usd"] == pytest.approx(raw.loc[0, "chain_tvl_usd"])


def test_cross_sectional_zscore_groups_by_date_only(tmp_path):
    cfg = _cfg(tmp_path)
    symbols = [f"S{i}" for i in range(12)]
    _write_upstream(tmp_path, symbols=symbols, onchain_symbols=symbols[:5])
    assert FeatureAgent(cfg).execute(max_retries=1)
    market, _, _, _, _, _, _ = _read_outputs(tmp_path)
    sample_date = market["date_ts"].iloc[50]
    same_day = market[market["date_ts"] == sample_date]
    col = "log_ret_7d"
    cs_col = "log_ret_7d_cs_z"
    expected = (same_day[col] - same_day[col].mean()) / same_day[col].std()
    merged = same_day[["symbol", cs_col]].merge(expected.rename("expected"), left_index=True, right_index=True)
    assert np.allclose(merged[cs_col].fillna(0), merged["expected"].fillna(0), atol=1e-8)


def test_winsorization_is_cross_sectional_by_date_not_global(tmp_path):
    cfg = _cfg(tmp_path)
    symbols = [f"S{i}" for i in range(20)]
    _write_upstream(tmp_path, symbols=symbols, onchain_symbols=symbols[:5], periods=100)
    assert FeatureAgent(cfg).execute(max_retries=1)
    market, _, _, _, _, _, _ = _read_outputs(tmp_path)
    sample_date = market["date_ts"].iloc[70]
    same_day = market[market["date_ts"] == sample_date]["log_dollar_volume"]
    global_series = market["log_dollar_volume"]
    same_day_p99 = same_day.quantile(0.99)
    global_p99 = global_series.quantile(0.99)
    sample_row = market[(market["symbol"] == symbols[0]) & (market["date_ts"] == sample_date)].iloc[0]
    assert sample_row["log_dollar_volume"] <= same_day_p99 + 1e-8
    assert same_day_p99 <= global_p99 + 1e-8


def test_missing_onchain_does_not_drop_market_rows(tmp_path):
    cfg = _cfg(tmp_path)
    symbols = ["BTC", "ETH", "SOL", "XRP"]
    _write_upstream(tmp_path, symbols=symbols, onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    market, _, full, _, _, _, _ = _read_outputs(tmp_path)
    assert len(full) == len(market)


def test_onchain_missingness_indicators_exist(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    _, onchain, _, _, _, _, _ = _read_outputs(tmp_path)
    expected = {"missing_adr_active_count", "missing_tx_count", "missing_chain_tvl_usd"}
    assert expected.issubset(onchain.columns)


def test_no_infinite_numeric_values(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    _, _, full, _, _, _, _ = _read_outputs(tmp_path)
    numeric = full.select_dtypes(include=[np.number])
    assert np.isfinite(numeric.to_numpy(dtype=float, copy=True)[~np.isnan(numeric.to_numpy(dtype=float, copy=True))]).all()


def test_all_null_feature_fails_verifier(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    out = tmp_path / "data" / "features_smoke"
    full = pd.read_parquet(out / "full_features.parquet")
    full["all_null_test_feature"] = np.nan
    full.to_parquet(out / "full_features.parquet", index=False)
    failures = validate_feature_outputs(cfg)
    assert any("all-null feature columns" in f for f in failures)


def test_low_market_coverage_fails(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["features"]["min_market_symbols_required"] = 10
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL"], onchain_symbols=["BTC", "ETH"])
    assert not FeatureAgent(cfg).execute(max_retries=1)


def test_low_onchain_coverage_fails_if_config_requires(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["features"]["min_onchain_symbols_required"] = 4
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert not FeatureAgent(cfg).execute(max_retries=1)


def test_verify_feature_run_passes_valid_fixture(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    assert validate_feature_outputs(cfg) == []


def test_verify_feature_run_rejects_target_leakage(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    out = tmp_path / "data" / "features_smoke"
    full = pd.read_parquet(out / "full_features.parquet")
    full["target_return_7d"] = 1.0
    full.to_parquet(out / "full_features.parquet", index=False)
    failures = validate_feature_outputs(cfg)
    assert any("prohibited columns" in f for f in failures)


def test_verify_feature_run_rejects_duplicate_symbol_date(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    out = tmp_path / "data" / "features_smoke"
    market = pd.read_parquet(out / "market_features.parquet")
    market = pd.concat([market, market.iloc[[0]]], ignore_index=True)
    market.to_parquet(out / "market_features.parquet", index=False)
    failures = validate_feature_outputs(cfg)
    assert any("duplicate symbol + date_ts rows" in f for f in failures)


def test_pruned_features_are_subset_of_full_features(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    out = tmp_path / "data" / "features_smoke"
    full = pd.read_parquet(out / "full_features.parquet")
    pruned = pd.read_parquet(out / "full_features_pruned.parquet")
    assert set(pruned.columns).issubset(set(full.columns) | {"onchain_lag_days"})


def test_manifest_counts_match_output_files(tmp_path):
    cfg = _cfg(tmp_path)
    _write_upstream(tmp_path, symbols=["BTC", "ETH", "SOL", "XRP"], onchain_symbols=["BTC", "ETH"])
    assert FeatureAgent(cfg).execute(max_retries=1)
    market, onchain, full, _, manifest, _, keep = _read_outputs(tmp_path)
    assert manifest["market_rows"] == len(market)
    assert manifest["onchain_rows"] == len(onchain)
    assert manifest["full_rows"] == len(full)
    assert manifest["final_kept_feature_count"] == len(keep["kept_features"])
