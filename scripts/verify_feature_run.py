#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402
from features.feature_engineering import check_for_prohibited_columns  # noqa: E402


REQUIRED_OUTPUTS = [
    "market_features.parquet",
    "onchain_features.parquet",
    "full_features.parquet",
    "feature_coverage_report.parquet",
    "feature_manifest.json",
    "feature_dictionary.json",
    "data_quality_features.md",
]

METADATA_COLUMNS = {
    "date_ts",
    "symbol",
    "feature_set",
    "feature_version",
    "snapshot_id",
    "run_id",
    "created_at_utc",
    "onchain_lag_days",
}


def _merge_section(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section or section == "features":
        return cfg
    merged = dict(cfg)
    if section in cfg:
        merged["features"] = dict(cfg.get("features", {}))
        merged["features"].update(cfg.get(section, {}))
    return merged


def _output_dir(cfg: Dict[str, Any]) -> Path:
    fcfg = cfg.get("features", {})
    out = Path(fcfg.get("output_dir", "data/features"))
    if not out.is_absolute():
        out = Path(cfg["_project_root"]) / out
    return out


def _read_parquet(path: Path) -> pd.DataFrame:
    con = duckdb.connect(database=":memory:")
    return con.execute(f"SELECT * FROM read_parquet('{path}')").df()


def _feature_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c not in METADATA_COLUMNS]


def _numeric_feature_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in _feature_columns(df) if pd.api.types.is_numeric_dtype(df[c])]


def inspect_feature_outputs(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    fcfg = cfg.get("features", {})
    out_dir = _output_dir(cfg)
    failures: List[str] = []
    warnings: List[str] = []

    for filename in REQUIRED_OUTPUTS:
        path = out_dir / filename
        if not path.exists():
            failures.append(f"missing file: {path}")
    if fcfg.get("pruning", {}).get("enabled", True):
        keep_path = out_dir / "feature_keep_list.json"
        if not keep_path.exists():
            failures.append(f"missing file: {keep_path}")
    if failures:
        return failures, warnings

    market = _read_parquet(out_dir / "market_features.parquet")
    onchain = _read_parquet(out_dir / "onchain_features.parquet")
    full = _read_parquet(out_dir / "full_features.parquet")
    coverage = _read_parquet(out_dir / "feature_coverage_report.parquet")
    pruned_path = out_dir / "full_features_pruned.parquet"
    pruned = _read_parquet(pruned_path) if pruned_path.exists() else None
    with open(out_dir / "feature_manifest.json", "r") as f:
        manifest = json.load(f)
    with open(out_dir / "feature_dictionary.json", "r") as f:
        dictionary = json.load(f)
    keep_info = None
    keep_path = out_dir / "feature_keep_list.json"
    if keep_path.exists():
        with open(keep_path, "r") as f:
            keep_info = json.load(f)

    for label, df in [("market_features", market), ("onchain_features", onchain), ("full_features", full)]:
        if df.empty:
            failures.append(f"{label} is empty")
            continue
        dt = pd.to_datetime(df["date_ts"], utc=True, errors="coerce")
        if dt.isna().any():
            failures.append(f"{label} has non-UTC/unparseable date_ts values")
        if df.duplicated(["symbol", "date_ts"]).any():
            failures.append(f"{label} has duplicate symbol + date_ts rows")
        bad_cols = check_for_prohibited_columns(df.columns)
        if bad_cols:
            failures.append(f"{label} contains prohibited columns: {bad_cols}")
        numeric_cols = _numeric_feature_columns(df)
        if numeric_cols:
            numeric = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
            inf_mask = np.isinf(df[numeric_cols].to_numpy(dtype="float64", copy=True))
            if inf_mask.any():
                failures.append(f"{label} contains infinite numeric values")
            all_null = [c for c in numeric_cols if numeric[c].isna().all()]
            if all_null:
                failures.append(f"{label} has all-null feature columns: {all_null[:10]}")

    if "onchain_available" not in onchain.columns:
        failures.append("onchain_features missing onchain_available")
    if not any(col.startswith("missing_") for col in onchain.columns):
        failures.append("onchain_features missing missingness indicator columns")

    market_symbols = int(market["symbol"].nunique()) if not market.empty else 0
    onchain_symbols = int(onchain.loc[onchain.get("onchain_available", False) == True, "symbol"].nunique()) if not onchain.empty and "onchain_available" in onchain.columns else 0  # noqa: E712
    full_symbols = int(full["symbol"].nunique()) if not full.empty else 0
    full_rows = int(len(full))
    if market_symbols < int(fcfg.get("min_market_symbols_required", 0)):
        failures.append(f"market symbol count below minimum: {market_symbols} < {int(fcfg.get('min_market_symbols_required', 0))}")
    if onchain_symbols < int(fcfg.get("min_onchain_symbols_required", 0)):
        failures.append(f"onchain symbol count below minimum: {onchain_symbols} < {int(fcfg.get('min_onchain_symbols_required', 0))}")
    if full_symbols < int(fcfg.get("min_full_feature_symbols_required", 0)):
        failures.append(f"full symbol count below minimum: {full_symbols} < {int(fcfg.get('min_full_feature_symbols_required', 0))}")
    if full_rows < int(fcfg.get("min_rows_required", 0)):
        failures.append(f"full row count below minimum: {full_rows} < {int(fcfg.get('min_rows_required', 0))}")
    if len(full) < len(market):
        failures.append("full_features row count is below market_features row count")

    coverage_names = set(coverage["feature_name"].astype(str))
    expected_market = set(_feature_columns(market))
    expected_onchain = set(_feature_columns(onchain))
    expected_full = set(_feature_columns(full))
    actual_market_cov = set(coverage.loc[coverage["feature_set"] == "market", "feature_name"].astype(str))
    actual_onchain_cov = set(coverage.loc[coverage["feature_set"] == "onchain", "feature_name"].astype(str))
    actual_full_cov = set(coverage.loc[coverage["feature_set"] == "full", "feature_name"].astype(str))
    if expected_market != actual_market_cov:
        failures.append("coverage report market feature names do not match market_features columns")
    if expected_onchain != actual_onchain_cov:
        failures.append("coverage report onchain feature names do not match onchain_features columns")
    if expected_full != actual_full_cov:
        failures.append("coverage report full feature names do not match full_features columns")

    if manifest.get("market_rows") != len(market):
        failures.append("manifest market_rows does not match market_features")
    if manifest.get("onchain_rows") != len(onchain):
        failures.append("manifest onchain_rows does not match onchain_features")
    if manifest.get("full_rows") != len(full):
        failures.append("manifest full_rows does not match full_features")
    if manifest.get("market_symbols") != market_symbols:
        failures.append("manifest market_symbols does not match market_features")
    if manifest.get("onchain_symbols") != onchain_symbols:
        failures.append("manifest onchain_symbols does not match onchain_features coverage")
    if manifest.get("full_symbols") != full_symbols:
        failures.append("manifest full_symbols does not match full_features")

    dict_names = set(dictionary.keys())
    if expected_full - dict_names:
        failures.append(f"feature_dictionary missing full feature definitions: {sorted(expected_full - dict_names)[:10]}")

    if keep_info is not None:
        kept = set(keep_info.get("kept_features", []))
        if pruned is not None:
            pruned_features = set(_feature_columns(pruned))
            if not pruned_features.issubset(expected_full):
                failures.append("pruned feature columns are not a subset of full feature columns")
            if not pruned_features.issuperset(kept):
                warnings.append("pruned output does not contain every kept feature from keep list")

    return failures, warnings


def validate_feature_outputs(cfg: Dict[str, Any]) -> List[str]:
    failures, _ = inspect_feature_outputs(cfg)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate FeatureAgent research outputs")
    parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    parser.add_argument("--section", default="features", help="Config section to merge into features")
    args = parser.parse_args()
    cfg = load_config(Path(args.config) if args.config else None)
    cfg = _merge_section(cfg, args.section)
    failures, warnings = inspect_feature_outputs(cfg)
    for warning in warnings:
        print(f"Feature validation warning: {warning}")
    if failures:
        print("Feature validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Feature validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
