#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config


CRITICAL = [
    "mean_rank_ic",
    "rank_ic_tstat",
    "top_bottom_spread",
]
SIGNAL_ONLY_BACKTEST_METRICS = ["sharpe", "cagr", "total_return", "max_drawdown"]
FORBIDDEN_EXPORT_TOKENS = ("actual", "label", "future", "realized", "target", "y_")


def _merge(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section:
        return cfg
    merged = dict(cfg)
    base_key = "alpha_research" if section.startswith("alpha_research") else section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def _out_dir(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get("alpha_research", {}).get("output_dir", "data/research")
    path = Path(raw)
    return path if path.is_absolute() else Path(cfg["_project_root"]) / path


def validate_alpha_research_outputs(cfg: Dict[str, Any]) -> List[str]:
    root = Path(cfg["_project_root"])
    out = _out_dir(cfg)
    pred_dir = root / "data/predictions"
    files = {
        "research_leaderboard": out / "research_leaderboard.parquet",
        "best_experiments": out / "best_experiments.parquet",
        "regime_report": out / "regime_report.parquet",
        "subperiod_report": out / "subperiod_report.parquet",
        "manifest": out / "research_manifest.json",
        "report": out / "alpha_research_report.md",
        "predictions": pred_dir / "alpha_research_predictions.parquet",
        "fold_metrics": pred_dir / "alpha_fold_metrics.parquet",
    }
    failures: List[str] = []
    for path in files.values():
        if not path.exists():
            failures.append(f"FAIL: missing {path.name}")
    if failures:
        return failures

    leaderboard = pd.read_parquet(files["research_leaderboard"])
    best = pd.read_parquet(files["best_experiments"])
    predictions = pd.read_parquet(files["predictions"])
    folds = pd.read_parquet(files["fold_metrics"])
    with open(files["manifest"], "r") as fh:
        manifest = json.load(fh)
    if leaderboard.empty:
        failures.append("FAIL: research_leaderboard.parquet is empty")
    if best.empty:
        failures.append("FAIL: best_experiments.parquet is empty")
    if predictions.empty:
        failures.append("FAIL: alpha_research_predictions.parquet is empty")
    if folds.empty:
        failures.append("FAIL: alpha_fold_metrics.parquet is empty")
    required = {
        "experiment_id",
        "model_name",
        "feature_set",
        "label_target",
        "horizon_days",
        "alpha_signal_status",
        "signal_status",
        "signal_gate_passed",
        "candidate_for_backtest",
        "backtest_source",
        "metric_status",
        "alpha_backtest_status",
        "final_alpha_status",
        "failure_reason",
    }
    missing = required - set(leaderboard.columns)
    for col in sorted(missing):
        failures.append(f"FAIL: research_leaderboard missing {col}")
    if failures:
        return failures
    if leaderboard["experiment_id"].duplicated().any():
        failures.append("FAIL: duplicate experiment IDs")
    for col in CRITICAL:
        if col in leaderboard.columns:
            vals = pd.to_numeric(leaderboard[col], errors="coerce")
            if vals.notna().any() and (~np.isfinite(vals.dropna())).any():
                failures.append(f"FAIL: non-finite critical metric {col}")
    valid_final_status = {"passed", "failed"}
    valid_signal_status = {"passed_signal_screen", "failed_signal_screen"}
    if not set(leaderboard["alpha_signal_status"].dropna().astype(str)).issubset(valid_signal_status):
        failures.append("FAIL: invalid status values in alpha_signal_status")
    if not set(leaderboard["signal_status"].dropna().astype(str)).issubset(valid_signal_status):
        failures.append("FAIL: invalid status values in signal_status")
    if not set(leaderboard["final_alpha_status"].dropna().astype(str)).issubset(valid_final_status):
        failures.append("FAIL: invalid status values in final_alpha_status")
    valid_backtest_status = {"not_run", "passed", "failed"}
    for col in ["alpha_backtest_status"]:
        if not set(leaderboard[col].dropna().astype(str)).issubset(valid_backtest_status):
            failures.append(f"FAIL: invalid status values in {col}")
    bad_alpha = leaderboard[(leaderboard["final_alpha_status"] == "passed") & ~((leaderboard["backtest_source"] == "backtest_agent") & (leaderboard["metric_status"] == "backtest_verified"))]
    if not bad_alpha.empty:
        failures.append("FAIL: final_alpha_status passed without BacktestAgent verified metrics")
    signal_only = leaderboard["metric_status"] == "signal_only"
    for col in SIGNAL_ONLY_BACKTEST_METRICS:
        if col in leaderboard.columns and leaderboard.loc[signal_only, col].notna().any():
            failures.append(f"FAIL: signal_only row has non-null {col}")
    if bool(manifest.get("canonical_outputs_mutated", False)):
        failures.append("FAIL: manifest reports canonical outputs were mutated")
    export_path = pred_dir / "model_predictions.parquet"
    if manifest.get("export_candidate_to_predictions", False) and export_path.exists():
        exported = pd.read_parquet(export_path)
        bad_cols = [c for c in exported.columns if any(tok in c.lower() for tok in FORBIDDEN_EXPORT_TOKENS)]
        if bad_cols:
            failures.append(f"FAIL: model_predictions export contains forbidden columns: {bad_cols}")
    configured = cfg.get("alpha_research", {})
    requested_models = set(configured.get("models", []))
    if requested_models:
        seen = set(leaderboard["model_name"].astype(str)) | {str(x.get("model_name")) for x in manifest.get("skipped_experiments", [])}
        missing_models = requested_models - seen
        if missing_models:
            failures.append(f"FAIL: configured models not run or skipped: {sorted(missing_models)}")
    if int(manifest.get("experiments_run", -1)) != len(leaderboard):
        failures.append("FAIL: manifest experiments_run does not match leaderboard")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    parser.add_argument("--section", default="alpha_research")
    args = parser.parse_args()
    cfg = _merge(load_config(Path(args.config)), args.section)
    failures = validate_alpha_research_outputs(cfg)
    if failures:
        print("Alpha research validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Alpha research validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
