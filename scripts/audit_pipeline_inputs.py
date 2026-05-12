#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402


READINESS_DIR = PROJECT_ROOT / "data" / "readiness"
DOC_PATH = PROJECT_ROOT / "docs" / "API_DATA_READINESS_AUDIT.md"


ARTIFACTS = [
    "data/raw/universe/universe_monthly.parquet",
    "data/raw/universe/universe_membership.parquet",
    "data/raw/universe/universe_manifest.json",
    "data/raw/market/market_ohlcv.parquet",
    "data/raw/market/market_coverage_report.parquet",
    "data/raw/market/market_manifest.json",
    "data/raw/onchain/onchain_observations.parquet",
    "data/raw/onchain/onchain_wide.parquet",
    "data/raw/onchain/onchain_manifest.json",
    "data/features/full_features.parquet",
    "data/features/full_features_pruned.parquet",
    "data/features/feature_manifest.json",
    "data/labels/label_matrix.parquet",
    "data/labels/modeling_dataset.parquet",
    "data/labels/label_manifest.json",
    "data/predictions/model_predictions.parquet",
    "data/predictions/model_leaderboard.parquet",
    "data/allocations/allocations_from_predictions.parquet",
    "data/backtests/backtest_summary.parquet",
    "data/backtests/benchmark_summary.parquet",
    "data/backtests/alpha_report.json",
]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except Exception as exc:
        return {"_read_error": str(exc)}


def _dup_subset(path: Path, cols: List[str]) -> Optional[List[str]]:
    name = path.name
    if "universe_monthly" in name or "universe_membership" in name:
        return [c for c in ["snapshot_date", "cmc_id"] if c in cols] or [c for c in ["snapshot_date", "symbol"] if c in cols]
    if any(x in name for x in ["market_ohlcv", "onchain_wide", "full_features", "full_features_pruned", "label_matrix", "modeling_dataset"]):
        return [c for c in ["date_ts", "symbol"] if c in cols]
    if "onchain_observations" in name:
        return [c for c in ["date_ts", "symbol", "metric_name", "source"] if c in cols]
    if "model_predictions" in name:
        return [c for c in ["date_ts", "symbol", "model_name", "feature_set", "horizon_days"] if c in cols]
    if "allocations_from_predictions" in name:
        return [c for c in ["date_ts", "symbol", "strategy_name"] if c in cols]
    return None


def _audit_parquet(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {"present": True, "path": str(path), "file_type": "parquet"}
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        info["read_error"] = str(exc)
        return info
    info["rows"] = int(len(df))
    info["columns"] = list(df.columns)
    if "symbol" in df.columns:
        info["unique_symbols"] = int(df["symbol"].nunique(dropna=True))
    if "cmc_id" in df.columns:
        info["non_null_cmc_id_rows"] = int(df["cmc_id"].notna().sum())
    for date_col in ["date_ts", "snapshot_date", "future_date_ts"]:
        if date_col in df.columns:
            vals = pd.to_datetime(df[date_col], utc=True, errors="coerce")
            info[f"{date_col}_null_count"] = int(vals.isna().sum())
            if vals.notna().any():
                info[f"{date_col}_min"] = str(vals.min())
                info[f"{date_col}_max"] = str(vals.max())
    subset = _dup_subset(path, list(df.columns))
    if subset and len(subset) >= 2:
        info["duplicate_key_subset"] = subset
        info["duplicate_key_rows"] = int(df.duplicated(subset=subset).sum())
    null_counts = df.isna().sum()
    important_nulls = {col: int(null_counts[col]) for col in ["date_ts", "snapshot_date", "symbol", "cmc_id", "close", "prediction", "weight"] if col in df.columns and int(null_counts[col]) > 0}
    info["important_nulls"] = important_nulls
    return info


def _audit_json(path: Path) -> Dict[str, Any]:
    payload = _read_json(path)
    info: Dict[str, Any] = {"present": True, "path": str(path), "file_type": "json"}
    info["read_error"] = payload.get("_read_error")
    for key in [
        "run_id",
        "snapshot_id",
        "created_at_utc",
        "universe_mode",
        "survivor_only_universe",
        "survivorship_bias_disclosed",
        "requested_assets",
        "fetched_assets",
        "assets_with_any_onchain",
        "market_rows",
        "feature_rows",
        "label_matrix_rows",
        "prediction_rows",
        "allocation_rows",
        "alpha_verified",
        "alpha_status",
    ]:
        if key in payload:
            info[key] = payload.get(key)
    if "survivor_only_universe" in payload:
        info["historical_membership_mode"] = "latest_survivor_baseline" if payload.get("survivor_only_universe") else "point_in_time_or_non_survivor"
    return info


def _audit_path(root: Path, rel: str) -> Dict[str, Any]:
    path = root / rel
    if not path.exists():
        return {"present": False, "path": str(path)}
    if path.suffix == ".parquet":
        return _audit_parquet(path)
    if path.suffix == ".json":
        return _audit_json(path)
    return {"present": True, "path": str(path), "file_type": path.suffix.lstrip(".")}


def _looks_stale(entries: Dict[str, Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    manifest = entries.get("data/raw/universe/universe_manifest.json", {})
    if manifest.get("present") and manifest.get("survivor_only_universe") is True:
        warnings.append("Universe manifest indicates latest-survivor baseline; point-in-time historical membership is not active.")
    if not entries.get("data/raw/universe/universe_membership.parquet", {}).get("present"):
        warnings.append("universe_membership.parquet missing; historical point-in-time membership cannot be applied downstream.")
    for rel, info in entries.items():
        if info.get("present") and info.get("duplicate_key_rows", 0):
            warnings.append(f"{rel} has duplicate key rows: {info['duplicate_key_rows']}")
        if not info.get("present"):
            warnings.append(f"{rel} missing")
    return warnings


def _upsert_doc_section(title: str, body: str) -> None:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    start = f"<!-- {title}:START -->"
    end = f"<!-- {title}:END -->"
    existing = DOC_PATH.read_text() if DOC_PATH.exists() else "# API/Data Readiness Audit\n\n"
    section = f"{start}\n{body.rstrip()}\n{end}\n"
    if start in existing and end in existing:
        before = existing.split(start)[0]
        after = existing.split(end, 1)[1]
        DOC_PATH.write_text(before + section + after.lstrip("\n"))
    else:
        DOC_PATH.write_text(existing.rstrip() + "\n\n" + section)


def _markdown(results: Dict[str, Any]) -> str:
    lines = [
        "## Pipeline Input Readiness",
        "",
        f"- Created at UTC: `{results['created_at_utc']}`",
        f"- Config: `{results['config_path']}`",
        "",
        "### Artifact Summary",
        "| Artifact | Present | Rows | Symbols | Date/Snapshot Range | Duplicate Keys | Notes |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for rel, info in results["artifacts"].items():
        rows = info.get("rows", "")
        syms = info.get("unique_symbols", "")
        start = info.get("date_ts_min") or info.get("snapshot_date_min") or ""
        end = info.get("date_ts_max") or info.get("snapshot_date_max") or ""
        rng = f"{start} to {end}" if start or end else ""
        notes = info.get("historical_membership_mode") or info.get("read_error") or ""
        lines.append(f"| `{rel}` | `{info.get('present')}` | `{rows}` | `{syms}` | `{rng}` | `{info.get('duplicate_key_rows', '')}` | {notes} |")
    lines.extend(["", "### Warnings"])
    if results["warnings"]:
        lines.extend([f"- {w}" for w in results["warnings"]])
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    root = Path(cfg["_project_root"])
    artifacts = {rel: _audit_path(root, rel) for rel in ARTIFACTS}
    results = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(args.config)),
        "artifacts": artifacts,
        "warnings": _looks_stale(artifacts),
    }
    READINESS_DIR.mkdir(parents=True, exist_ok=True)
    (READINESS_DIR / "pipeline_input_audit.json").write_text(json.dumps(results, indent=2, default=str))
    _upsert_doc_section("PIPELINE_INPUT_READINESS", _markdown(results))
    print(f"Pipeline input audit complete. Missing artifacts={sum(1 for v in artifacts.values() if not v.get('present'))}. Report: {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
