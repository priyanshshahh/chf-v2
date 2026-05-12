#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402


OBSERVATION_COLUMNS = {
    "date_ts",
    "symbol",
    "metric_name",
    "metric_value",
    "source",
    "provider_asset_id",
    "provider_metric_name",
    "provider_entity_id",
    "data_type",
    "snapshot_id",
    "fetched_at_utc",
    "is_forward_filled",
    "is_incomplete_dropped",
}

WIDE_COLUMNS = {
    "date_ts",
    "symbol",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "snapshot_id",
    "fetched_at_utc",
}

COVERAGE_COLUMNS = {
    "symbol",
    "coin_id",
    "market_cap_rank",
    "requested",
    "fetched_any",
    "coinmetrics_available",
    "defillama_available",
    "etherscan_available",
    "thegraph_available",
    "blockchair_available",
    "dune_available",
    "source_used",
    "metrics_requested",
    "metrics_fetched",
    "attempted_row_count_long",
    "persisted_row_count_long",
    "row_count_long",
    "row_count_wide",
    "start_date",
    "end_date",
    "requested_start_date",
    "requested_end_date",
    "missing_days_by_metric",
    "provider_attempts",
    "provider_failure_reasons",
    "passed_qa",
    "failure_reason",
}

NON_NEGATIVE_METRICS = {
    "adr_active_count",
    "tx_count",
    "realized_cap_usd",
    "fee_total_usd",
    "transfer_value_adjusted_usd",
    "current_supply",
    "market_cap_usd",
    "issuance_total_usd",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "revenue_usd",
    "dex_volume_usd",
    "stablecoin_mcap_usd",
    "pool_tvl_usd",
    "gas_used",
    "transaction_count_proxy",
    "token_transfer_count_proxy",
    "protocol_volume_usd",
}


def _merge_section(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section or section == "onchain":
        return cfg
    merged = dict(cfg)
    if section in cfg:
        target = "onchain" if section.startswith("onchain") else section
        merged[target] = dict(cfg.get(target, {}))
        merged[target].update(cfg.get(section, {}))
    return merged


def _resolve_output_dir(cfg: Dict[str, Any]) -> Path:
    ocfg = cfg.get("onchain") or cfg.get("on_chain", {})
    output_dir = Path(ocfg.get("output_dir", "data/raw/onchain"))
    if not output_dir.is_absolute():
        output_dir = Path(cfg["_project_root"]) / output_dir
    return output_dir


def _sample_rows(df: pd.DataFrame, mask: pd.Series) -> List[Dict[str, Any]]:
    cols = [
        "date_ts",
        "symbol",
        "metric_name",
        "metric_value",
        "source",
        "provider_asset_id",
        "provider_metric_name",
        "provider_entity_id",
        "data_type",
    ]
    keep = [col for col in cols if col in df.columns]
    return df.loc[mask, keep].head(10).to_dict(orient="records")


def inspect_onchain_outputs(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    ocfg = cfg.get("onchain") or cfg.get("on_chain", {})
    output_dir = _resolve_output_dir(cfg)
    observations_path = output_dir / "onchain_observations.parquet"
    wide_path = output_dir / "onchain_wide.parquet"
    coverage_path = output_dir / "onchain_coverage_report.parquet"
    manifest_path = output_dir / "onchain_manifest.json"
    quality_path = output_dir / "data_quality_onchain.md"
    failures: List[str] = []
    warnings: List[str] = []

    for path in [observations_path, wide_path, coverage_path, manifest_path, quality_path]:
        if not path.exists():
            failures.append(f"missing file: {path}")
        if "demo" in str(path).lower() and ocfg.get("fail_on_demo_data", True):
            failures.append(f"demo path rejected: {path}")
    if failures:
        return failures, warnings

    con = duckdb.connect(database=":memory:")
    observations = con.execute(f"SELECT * FROM read_parquet('{observations_path}')").df()
    wide = con.execute(f"SELECT * FROM read_parquet('{wide_path}')").df()
    coverage = con.execute(f"SELECT * FROM read_parquet('{coverage_path}')").df()

    for col in sorted(OBSERVATION_COLUMNS - set(observations.columns)):
        failures.append(f"FAIL: onchain_observations.parquet missing required column {col}")
    for col in sorted(WIDE_COLUMNS - set(wide.columns)):
        failures.append(f"FAIL: onchain_wide.parquet missing required column {col}")
    for col in sorted(COVERAGE_COLUMNS - set(coverage.columns)):
        failures.append(f"FAIL: onchain_coverage_report.parquet missing required column {col}")
    if failures:
        return failures, warnings

    if observations.empty:
        failures.append("FAIL: onchain_observations.parquet is empty")
    if coverage.empty:
        failures.append("FAIL: onchain_coverage_report.parquet is empty")
    if failures:
        return failures, warnings

    observations["date_ts"] = pd.to_datetime(observations["date_ts"], utc=True, errors="coerce")
    if observations["date_ts"].isna().any():
        failures.append("FAIL: date_ts contains null/non-datetime values")
        warnings.append(f"bad date rows: {_sample_rows(observations, observations['date_ts'].isna())}")
    non_midnight = (observations["date_ts"].dt.hour != 0) | (observations["date_ts"].dt.minute != 0) | (observations["date_ts"].dt.second != 0)
    if non_midnight.any():
        failures.append("FAIL: date_ts contains non-midnight timestamps")
        warnings.append(f"non-midnight rows: {_sample_rows(observations, non_midnight)}")
    today = pd.Timestamp.now(tz="UTC").normalize()
    current_day_mask = observations["date_ts"] >= today
    if current_day_mask.any():
        failures.append("FAIL: current-day incomplete rows detected")
        warnings.append(f"current-day rows: {_sample_rows(observations, current_day_mask)}")

    metric_numeric = pd.to_numeric(observations["metric_value"], errors="coerce")
    metric_null_mask = metric_numeric.isna()
    if metric_null_mask.any():
        failures.append("FAIL: metric_value contains null/non-numeric values")
        warnings.append(f"bad metric_value rows: {_sample_rows(observations, metric_null_mask)}")

    negative_mask = observations["metric_name"].isin(NON_NEGATIVE_METRICS) & (metric_numeric < 0)
    if negative_mask.any():
        failures.append("FAIL: negative values found for non-negative metrics")
        warnings.append(f"negative metric rows: {_sample_rows(observations, negative_mask)}")

    if observations.duplicated(["symbol", "date_ts", "metric_name", "source"]).any():
        failures.append("FAIL: duplicate symbol + date_ts + metric_name + source rows found")

    min_assets = int(ocfg.get("minimum_assets_with_any_onchain", 1))
    min_obs = int(ocfg.get("minimum_total_metric_observations", 1))
    min_defi_assets = int(ocfg.get("minimum_assets_with_defillama", 0))
    min_defi_obs = int(ocfg.get("minimum_defillama_observations", 0))
    persisted_symbols = set(observations["symbol"].astype(str).unique())
    coverage_fetched_symbols = set(coverage.loc[coverage["fetched_any"].fillna(False), "symbol"].astype(str).unique())
    coverage_passed_symbols = set(coverage.loc[coverage["passed_qa"].fillna(False), "symbol"].astype(str).unique())
    assets_with_any = int(len(persisted_symbols))
    assets_with_defillama = int(
        observations.loc[observations["source"].astype(str) == "defillama", "symbol"].astype(str).nunique()
    )
    total_observations = int(len(observations))
    defillama_observations = int((observations["source"].astype(str) == "defillama").sum())
    assets_with_coinmetrics = int(
        observations.loc[observations["source"].astype(str) == "coinmetrics", "symbol"].astype(str).nunique()
    )

    if coverage_fetched_symbols != persisted_symbols:
        failures.append("FAIL: coverage fetched_any symbols do not match persisted observation symbols")
        warnings.append(
            f"coverage fetched_any only={sorted(coverage_fetched_symbols - persisted_symbols)[:10]} "
            f"persisted only={sorted(persisted_symbols - coverage_fetched_symbols)[:10]}"
        )
    if coverage_passed_symbols != persisted_symbols:
        failures.append("FAIL: coverage passed_qa symbols do not match persisted observation symbols")
        warnings.append(
            f"coverage passed_qa only={sorted(coverage_passed_symbols - persisted_symbols)[:10]} "
            f"persisted only={sorted(persisted_symbols - coverage_passed_symbols)[:10]}"
        )
    if assets_with_any < min_assets:
        failures.append(f"FAIL: assets_with_any_onchain below minimum: {assets_with_any} < {min_assets}")
    if total_observations < min_obs:
        failures.append(f"FAIL: total_observations below minimum: {total_observations} < {min_obs}")
    if min_defi_assets > 0 and assets_with_defillama < min_defi_assets:
        failures.append(f"FAIL: assets_with_defillama below minimum: {assets_with_defillama} < {min_defi_assets}")
    if min_defi_obs > 0 and defillama_observations < min_defi_obs:
        failures.append(f"FAIL: defillama_observations below minimum: {defillama_observations} < {min_defi_obs}")

    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    if int(manifest.get("assets_with_any_onchain", -1)) != assets_with_any:
        failures.append("FAIL: manifest assets_with_any_onchain does not match persisted observations")
    if int(manifest.get("total_observations", -1)) != total_observations:
        failures.append("FAIL: manifest total_observations does not match observations parquet")
    if int(manifest.get("assets_with_coinmetrics", -1)) != assets_with_coinmetrics:
        failures.append("FAIL: manifest assets_with_coinmetrics does not match persisted observations")
    if int(manifest.get("assets_with_defillama", -1)) != assets_with_defillama:
        failures.append("FAIL: manifest assets_with_defillama does not match persisted observations")
    if int(manifest.get("defillama_observations", -1)) != defillama_observations:
        failures.append("FAIL: manifest defillama_observations does not match observations parquet")
    if not manifest.get("output_files"):
        failures.append("FAIL: manifest missing output_files")
    if "providers_unavailable" not in manifest:
        failures.append("FAIL: manifest missing providers_unavailable")
    return failures, warnings


def validate_onchain_outputs(cfg: Dict[str, Any]) -> List[str]:
    failures, _warnings = inspect_onchain_outputs(cfg)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate OnChainAgent research-mode outputs")
    parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    parser.add_argument("--section", default="onchain", help="Config section to merge into onchain")
    args = parser.parse_args()
    cfg = load_config(Path(args.config) if args.config else None)
    cfg = _merge_section(cfg, args.section)
    failures, warnings = inspect_onchain_outputs(cfg)
    for warning in warnings:
        print(f"OnChain validation warning: {warning}")
    if failures:
        print("OnChain validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("OnChain validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
