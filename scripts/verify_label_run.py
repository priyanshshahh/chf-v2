#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config


PROHIBITED_TOKENS = ("target", "label", "future", "forward", "fwd", "lead", "next_return", "ret_fwd", "y_")
ALLOWED_EXACT = {"is_forward_filled_market"}


def _merge_config_section(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section:
        return cfg
    if section not in cfg:
        raise KeyError(f"Config section not found: {section}")
    merged = dict(cfg)
    if section.startswith("labels"):
        base_key = "labels"
    else:
        base_key = section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _fail_missing_columns(df: pd.DataFrame, required: Iterable[str], name: str) -> List[str]:
    failures = []
    for col in required:
        if col not in df.columns:
            failures.append(f"FAIL: {name} missing required column {col}")
    return failures


def _bad_rows_sample(df: pd.DataFrame, mask: pd.Series) -> str:
    cols = [c for c in ["date_ts", "future_date_ts", "symbol", "horizon_days", "close_t", "close_t_plus_h", "label_fwd_logret"] if c in df.columns]
    return df.loc[mask, cols].head(10).to_string(index=False)


def inspect_label_outputs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(cfg["_project_root"])
    lcfg = cfg["labels"]
    out_dir = _resolve(root, lcfg.get("output_dir", "data/labels"))
    result: Dict[str, Any] = {"output_dir": out_dir}
    result["label_files"] = {int(h): out_dir / f"labels_{int(h)}d.parquet" for h in lcfg.get("horizons", [7, 14, 30])}
    result["label_matrix"] = out_dir / "label_matrix.parquet"
    result["modeling_dataset"] = out_dir / "modeling_dataset.parquet"
    result["modeling_dataset_unpruned"] = out_dir / "modeling_dataset_unpruned.parquet"
    result["coverage"] = out_dir / "label_coverage_report.parquet"
    result["manifest"] = out_dir / "label_manifest.json"
    result["quality"] = out_dir / "data_quality_labels.md"
    return result


def validate_label_outputs(cfg: Dict[str, Any]) -> List[str]:
    lcfg = cfg["labels"]
    info = inspect_label_outputs(cfg)
    failures: List[str] = []
    output_dir = info["output_dir"]

    for horizon, path in info["label_files"].items():
        if not path.exists():
            failures.append(f"FAIL: missing labels_{horizon}d.parquet")
    for key in ["label_matrix", "modeling_dataset", "coverage", "manifest", "quality"]:
        if not info[key].exists():
            failures.append(f"FAIL: missing {info[key].name}")
    if failures:
        return failures

    labels = {h: pd.read_parquet(path) for h, path in info["label_files"].items()}
    matrix = pd.read_parquet(info["label_matrix"])
    modeling = pd.read_parquet(info["modeling_dataset"])
    coverage = pd.read_parquet(info["coverage"])
    with open(info["manifest"], "r") as fh:
        manifest = json.load(fh)

    for horizon, df in labels.items():
        failures.extend(_fail_missing_columns(df, [
            "date_ts", "symbol", "horizon_days", "future_date_ts", "close_t", "close_t_plus_h",
            "label_fwd_logret", "label_simple_return", "label_direction", "label_rank_pct",
            "label_quantile_bucket", "is_complete", "snapshot_id", "run_id", "created_at_utc",
        ], f"labels_{horizon}d.parquet"))
    failures.extend(_fail_missing_columns(matrix, [
        "date_ts", "symbol", "label_fwd_logret_7d", "label_fwd_logret_14d", "label_fwd_logret_30d",
        "label_simple_return_7d", "label_simple_return_14d", "label_simple_return_30d",
        "label_direction_7d", "label_direction_14d", "label_direction_30d",
        "label_rank_pct_7d", "label_rank_pct_14d", "label_rank_pct_30d",
        "label_quantile_bucket_7d", "label_quantile_bucket_14d", "label_quantile_bucket_30d",
        "max_horizon_complete", "snapshot_id", "run_id", "created_at_utc",
    ], "label_matrix.parquet"))
    failures.extend(_fail_missing_columns(modeling, ["date_ts", "symbol"], "modeling_dataset.parquet"))
    failures.extend(_fail_missing_columns(coverage, [
        "horizon_days", "total_candidate_rows", "valid_label_rows", "dropped_incomplete_rows",
        "dropped_bad_price_rows", "dropped_missing_feature_rows", "symbols_with_labels",
        "null_label_count", "infinite_label_count", "passed_qa", "failure_reason"
    ], "label_coverage_report.parquet"))
    if failures:
        return failures

    if matrix.empty:
        failures.append("FAIL: label_matrix.parquet is empty")
    if modeling.empty:
        failures.append("FAIL: modeling_dataset.parquet is empty")

    for name, df in [("label_matrix", matrix), ("modeling_dataset", modeling)]:
        if df.duplicated(["symbol", "date_ts"]).any():
            failures.append(f"FAIL: {name} contains duplicate symbol + date_ts rows")
        dt = _utc(df["date_ts"])
        if dt.isna().any():
            failures.append(f"FAIL: {name} contains invalid date_ts")
        elif not ((dt.dt.hour == 0) & (dt.dt.minute == 0) & (dt.dt.second == 0)).all():
            failures.append(f"FAIL: {name} date_ts not normalized to UTC midnight")

    feature_input = _resolve(Path(cfg["_project_root"]), lcfg["input_features_path"])
    feature_df = pd.read_parquet(feature_input)
    bad_feature_cols = []
    for c in feature_df.columns:
        if c in ALLOWED_EXACT:
            continue
        lower = c.lower()
        if lower.startswith("y_") or any(token in lower for token in PROHIBITED_TOKENS if token != "y_"):
            bad_feature_cols.append(c)
    if bad_feature_cols:
        failures.append(f"FAIL: feature input contains prohibited leakage columns {bad_feature_cols}")

    if any(col.startswith("label_") for col in feature_df.columns):
        failures.append("FAIL: label columns were written into data/features files")

    for horizon, df in labels.items():
        if df.empty:
            failures.append(f"FAIL: labels_{horizon}d.parquet is empty")
            continue
        if df.duplicated(["symbol", "date_ts"]).any():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains duplicate symbol + date_ts rows")
        date_ts = _utc(df["date_ts"])
        future_ts = _utc(df["future_date_ts"])
        if date_ts.isna().any() or future_ts.isna().any():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains invalid timestamps")
        if not ((date_ts.dt.hour == 0) & (date_ts.dt.minute == 0) & (date_ts.dt.second == 0)).all():
            failures.append(f"FAIL: labels_{horizon}d.parquet date_ts not normalized to UTC midnight")
        if not ((future_ts.dt.hour == 0) & (future_ts.dt.minute == 0) & (future_ts.dt.second == 0)).all():
            failures.append(f"FAIL: labels_{horizon}d.parquet future_date_ts not normalized to UTC midnight")
        if not (future_ts > date_ts).all():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains future_date_ts <= date_ts")
        close_t = pd.to_numeric(df["close_t"], errors="coerce")
        close_h = pd.to_numeric(df["close_t_plus_h"], errors="coerce")
        label = pd.to_numeric(df["label_fwd_logret"], errors="coerce")
        if label.isna().any():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains null/non-numeric label_fwd_logret\n{_bad_rows_sample(df, label.isna())}")
        if (~np.isfinite(label)).any():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains infinite label_fwd_logret")
        bad_price_mask = close_t.isna() | close_h.isna() | (close_t <= 0) | (close_h <= 0)
        if bad_price_mask.any():
            failures.append(f"FAIL: labels_{horizon}d.parquet contains non-positive or invalid prices\n{_bad_rows_sample(df, bad_price_mask)}")
        sample = df.head(min(25, len(df))).copy()
        recomputed = np.log(pd.to_numeric(sample["close_t_plus_h"], errors="coerce") / pd.to_numeric(sample["close_t"], errors="coerce"))
        if not np.allclose(recomputed.to_numpy(), pd.to_numeric(sample["label_fwd_logret"], errors="coerce").to_numpy(), equal_nan=False, atol=1e-12):
            failures.append(f"FAIL: labels_{horizon}d.parquet formula mismatch on sampled rows")
        if len(df) < int(lcfg.get("min_rows_per_horizon_required", 1)):
            failures.append(f"FAIL: labels_{horizon}d.parquet row count below configured minimum")

    for col in [f"label_fwd_logret_{h}d" for h in lcfg.get("horizons", [7, 14, 30])]:
        if col not in matrix.columns:
            failures.append(f"FAIL: label_matrix.parquet missing horizon column {col}")
    if len(matrix) < int(lcfg.get("min_common_rows_all_horizons", 1)):
        failures.append("FAIL: label_matrix.parquet common rows below configured minimum")
    if matrix["symbol"].nunique() < int(lcfg.get("min_symbols_required", 1)):
        failures.append("FAIL: label_matrix.parquet symbol count below configured minimum")

    if len(modeling) < int(lcfg.get("min_label_rows_required", 1)):
        failures.append("FAIL: modeling_dataset.parquet row count below configured minimum")
    label_cols = [c for c in matrix.columns if c.startswith("label_")]
    missing_labels = [c for c in label_cols if c not in modeling.columns]
    if missing_labels:
        failures.append(f"FAIL: modeling_dataset.parquet missing label columns {missing_labels}")
    feature_cols = [c for c in modeling.columns if c not in {"date_ts", "symbol", "snapshot_id", "run_id", "created_at_utc"} and not c.startswith("label_") and c != "max_horizon_complete"]
    if not feature_cols:
        failures.append("FAIL: modeling_dataset.parquet does not contain feature columns")

    if set(coverage["horizon_days"].astype(int)) != set(int(h) for h in lcfg.get("horizons", [7, 14, 30])):
        failures.append("FAIL: label_coverage_report.parquet horizon set does not match config")
    actual_counts = {int(h): len(df) for h, df in labels.items()}
    for _, row in coverage.iterrows():
        horizon = int(row["horizon_days"])
        if int(row["valid_label_rows"]) != actual_counts.get(horizon, -1):
            failures.append(f"FAIL: coverage valid_label_rows mismatch for horizon {horizon}")

    if int(manifest.get("label_matrix_rows", -1)) != len(matrix):
        failures.append("FAIL: manifest label_matrix_rows does not match label_matrix.parquet")
    if int(manifest.get("modeling_dataset_rows", -1)) != len(modeling):
        failures.append("FAIL: manifest modeling_dataset_rows does not match modeling_dataset.parquet")
    if int(manifest.get("label_matrix_symbols", -1)) != matrix["symbol"].nunique():
        failures.append("FAIL: manifest label_matrix_symbols does not match label_matrix.parquet")
    if int(manifest.get("modeling_dataset_symbols", -1)) != modeling["symbol"].nunique():
        failures.append("FAIL: manifest modeling_dataset_symbols does not match modeling_dataset.parquet")
    manifest_rows_by_h = {int(k): int(v) for k, v in (manifest.get("label_rows_by_horizon") or {}).items()}
    if manifest_rows_by_h != actual_counts:
        failures.append("FAIL: manifest label_rows_by_horizon does not match actual outputs")
    if int(manifest.get("recommended_embargo_days", -1)) != max(int(h) for h in lcfg.get("horizons", [7, 14, 30])):
        failures.append("FAIL: recommended_embargo_days does not equal max configured horizon")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify canonical label outputs")
    parser.add_argument("--config", type=str, default="configs/run_config.yaml")
    parser.add_argument("--section", type=str, default="labels")
    args = parser.parse_args()

    cfg = _merge_config_section(load_config(Path(args.config)), args.section)
    failures = validate_label_outputs(cfg)
    if failures:
        print("Label validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Label validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
