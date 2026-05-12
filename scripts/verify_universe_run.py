#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402


def _merge_section(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section or section == "universe":
        return cfg
    merged = dict(cfg)
    universe = dict(cfg.get("universe", {}))
    universe.update(cfg.get(section, {}))
    merged["universe"] = universe
    return merged


def _resolve_output_dir(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get("universe", {}).get("output_dir", "data/raw/universe")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cfg["_project_root"]) / path
    return path


def inspect_universe_outputs(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    ucfg = cfg.get("universe", {})
    out_dir = _resolve_output_dir(cfg)
    universe_path = out_dir / "universe_monthly.parquet"
    membership_path = out_dir / "universe_membership.parquet"
    exclusions_path = out_dir / "exclusions_monthly.parquet"
    coverage_path = out_dir / "universe_coverage_report.parquet"
    manifest_path = out_dir / "universe_manifest.json"
    cmc_mode = bool(ucfg.get("use_cmc_historical_listings", False)) or cfg.get("universe", {}).get("provider_priority", [None])[0] == "coinmarketcap"

    failures: List[str] = []
    warnings: List[str] = []
    required_paths = [universe_path, exclusions_path, coverage_path, manifest_path]
    if cmc_mode:
        required_paths.append(membership_path)
    for path in required_paths:
        if not path.exists():
            failures.append(f"missing file: {path}")
        if "demo" in str(path).lower() and ucfg.get("fail_on_demo_data", True):
            failures.append(f"demo path rejected: {path}")
    if failures:
        return failures, warnings

    con = duckdb.connect(database=":memory:")
    universe = con.execute(f"SELECT * FROM read_parquet('{universe_path}')").df()
    coverage = con.execute(f"SELECT * FROM read_parquet('{coverage_path}')").df()
    con.execute(f"SELECT * FROM read_parquet('{exclusions_path}')").df()
    membership = con.execute(f"SELECT * FROM read_parquet('{membership_path}')").df() if cmc_mode else None

    min_eligible = int(ucfg.get("minimum_eligible_n", 1))
    if universe.empty:
        failures.append("universe_monthly.parquet is empty")
    if coverage.empty:
        failures.append("universe_coverage_report.parquet is empty")
    if not coverage.empty and (coverage["eligible_count"] < min_eligible).any():
        failures.append("eligible_count below minimum_eligible_n")
    for col in ["is_stablecoin", "is_wrapped", "is_bridged", "is_lst", "is_synthetic_pegged"]:
        if col in universe.columns and universe[col].fillna(False).any():
            failures.append(f"eligible row has {col}=true")
    if ucfg.get("require_exchange_tradability", True) and "is_exchange_tradable" in universe:
        if (universe["is_exchange_tradable"] == False).any():  # noqa: E712
            failures.append("eligible row has is_exchange_tradable=false")
    if ucfg.get("require_onchain_coverage", True) and "has_onchain_coverage" in universe:
        if (universe["has_onchain_coverage"] == False).any():  # noqa: E712
            failures.append("eligible row has has_onchain_coverage=false")
    if "market_cap_usd" in universe and (universe["market_cap_usd"] <= 0).any():
        failures.append("eligible row has non-positive market_cap_usd")
    if "snapshot_id" in universe and universe["snapshot_id"].isna().any():
        failures.append("snapshot_id is null")
    dup_cols = ["snapshot_date", "cmc_id"] if cmc_mode and "cmc_id" in universe.columns else ["snapshot_date", "symbol"]
    if universe.duplicated(dup_cols).any():
        failures.append(f"duplicate {' + '.join(dup_cols)} rows")
    if not coverage.empty and not coverage["passed_validation"].fillna(False).all():
        failures.append("coverage report passed_validation is not true")

    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    if not manifest.get("output_files"):
        failures.append("manifest missing output_files")
    if manifest.get("total_eligible_rows", 0) <= 0:
        failures.append("manifest total_eligible_rows <= 0")

    required_manifest_fields = [
        "universe_mode",
        "requested_start_date",
        "requested_end_date",
        "actual_start_date",
        "actual_end_date",
        "historical_snapshots_requested",
        "historical_snapshots_created",
        "historical_snapshot_limitation",
    ]
    for field in required_manifest_fields:
        if field not in manifest:
            failures.append(f"manifest missing {field}")

    if not universe.empty and {"actual_start_date", "actual_end_date"}.issubset(manifest):
        actual_start = pd_timestamp_date(universe["snapshot_date"].min())
        actual_end = pd_timestamp_date(universe["snapshot_date"].max())
        if manifest.get("actual_start_date") != actual_start:
            failures.append(
                f"manifest actual_start_date {manifest.get('actual_start_date')} "
                f"does not match output {actual_start}"
            )
        if manifest.get("actual_end_date") != actual_end:
            failures.append(
                f"manifest actual_end_date {manifest.get('actual_end_date')} "
                f"does not match output {actual_end}"
            )

    requested = int(manifest.get("historical_snapshots_requested") or 0)
    created = int(manifest.get("historical_snapshots_created") or 0)
    mode = manifest.get("universe_mode")
    allow_latest = bool(ucfg.get("allow_latest_snapshot_only", True))
    if requested > created:
        if mode != "latest_snapshot_only":
            failures.append(
                "historical snapshots requested exceeds snapshots created, but "
                "universe_mode is not latest_snapshot_only"
            )
        elif not allow_latest:
            failures.append(
                "latest_snapshot_only output is not allowed by allow_latest_snapshot_only=false"
            )
        else:
            warnings.append(
                "Historical monthly snapshots were requested, but only the latest snapshot "
                "was created; manifest universe_mode=latest_snapshot_only."
            )
            if not manifest.get("historical_snapshot_limitation"):
                failures.append("latest_snapshot_only manifest missing historical_snapshot_limitation")

    if cmc_mode:
        if membership is None or membership.empty:
            failures.append("universe_membership.parquet is empty")
        if manifest.get("universe_mode") != "historical_cmc_monthly":
            failures.append("manifest universe_mode != historical_cmc_monthly")
        if manifest.get("survivor_only_universe") is not False:
            failures.append("manifest survivor_only_universe is not false")
        if int(manifest.get("historical_snapshots_created") or 0) < 24:
            failures.append("historical_snapshots_created < 24 for CMC 3-year monthly mode")
        if "cmc_id" not in universe.columns:
            failures.append("universe_monthly.parquet missing cmc_id")
        else:
            eligible = universe[universe["is_eligible"].fillna(False)]
            if eligible["cmc_id"].isna().any():
                failures.append("eligible universe rows missing cmc_id")
        if not coverage.empty and (coverage["eligible_count"].astype(int) < min_eligible).any():
            failures.append("one or more CMC snapshots fell below minimum_eligible_n")
        for col in ["is_stablecoin", "is_wrapped", "is_synthetic_pegged"]:
            if col in universe.columns and universe.loc[universe["is_eligible"].fillna(False), col].fillna(False).any():
                failures.append(f"eligible CMC row has {col}=true")
    return failures, warnings


def pd_timestamp_date(value: Any) -> str:
    import pandas as pd

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.date().isoformat()


def validate_universe_outputs(cfg: Dict[str, Any]) -> List[str]:
    failures, _warnings = inspect_universe_outputs(cfg)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate UniverseAgent research-mode outputs")
    parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    parser.add_argument("--section", default="universe", help="Config section to merge into universe")
    args = parser.parse_args()

    cfg = load_config(Path(args.config) if args.config else None)
    cfg = _merge_section(cfg, args.section)
    failures, warnings = inspect_universe_outputs(cfg)
    for warning in warnings:
        print(f"Universe validation warning: {warning}")
    if failures:
        print("Universe validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Universe validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
