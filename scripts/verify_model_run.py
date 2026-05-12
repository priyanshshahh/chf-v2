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


def _merge(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section:
        return cfg
    merged = dict(cfg)
    if section.startswith("modeling"):
        base_key = "modeling"
    else:
        base_key = section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def validate_model_outputs(cfg: Dict[str, Any]) -> List[str]:
    root = Path(cfg["_project_root"])
    pred_dir = root / "data/predictions"
    failures: List[str] = []
    preds_path = pred_dir / "model_predictions.parquet"
    folds_path = pred_dir / "fold_metrics.parquet"
    leaderboard_path = pred_dir / "model_leaderboard.parquet"
    manifest_path = pred_dir / "model_manifest.json"
    for path in [preds_path, folds_path, leaderboard_path, manifest_path]:
        if not path.exists():
            failures.append(f"FAIL: missing {path.name}")
    if failures:
        return failures
    preds = pd.read_parquet(preds_path)
    folds = pd.read_parquet(folds_path)
    board = pd.read_parquet(leaderboard_path)
    manifest = json.load(open(manifest_path))
    if preds.empty:
        failures.append("FAIL: model_predictions.parquet is empty")
    if folds.empty:
        failures.append("FAIL: fold_metrics.parquet is empty")
    if board.empty:
        failures.append("FAIL: model_leaderboard.parquet is empty")
    req_pred_cols = ["date_ts","symbol","model_name","feature_set","horizon_days","fold_id","prediction","actual_forward_return","prediction_rank","prediction_rank_pct","actual_rank","actual_rank_pct","is_top_5","is_top_10","is_top_20","is_bottom_10","train_start","train_end","test_start","test_end","snapshot_id","run_id"]
    for col in req_pred_cols:
        if col not in preds.columns:
            failures.append(f"FAIL: model_predictions.parquet missing required column {col}")
    if failures:
        return failures
    if preds.duplicated(["model_name","feature_set","horizon_days","symbol","date_ts"]).any():
        failures.append("FAIL: duplicate model_name + feature_set + horizon_days + symbol + date_ts rows")
    if not np.isfinite(pd.to_numeric(preds["prediction"], errors="coerce")).all():
        failures.append("FAIL: predictions are not finite")
    if not np.isfinite(pd.to_numeric(preds["actual_forward_return"], errors="coerce")).all():
        failures.append("FAIL: actual_forward_return is not finite")
    if ((pd.to_datetime(preds["train_end"], utc=True) >= pd.to_datetime(preds["test_start"], utc=True))).any():
        failures.append("FAIL: fold train_end >= test_start")
    if "train_end_purged" in folds.columns and "test_start" in folds.columns:
        if (pd.to_datetime(folds["train_end_purged"], utc=True) >= pd.to_datetime(folds["test_start"], utc=True)).any():
            failures.append("FAIL: purged train end overlaps test start")
    if "rank_ic_mean" not in board.columns:
        failures.append("FAIL: leaderboard missing rank_ic_mean")
    if "selected_for_backtest" not in board.columns:
        failures.append("FAIL: leaderboard missing selected_for_backtest")
    for col in ["signal_status", "signal_gate_passed", "candidate_for_backtest", "signal_gate_failure_reason", "alpha_status"]:
        if col not in board.columns:
            failures.append(f"FAIL: leaderboard missing {col}")
    if failures:
        return failures
    board_candidates = board["candidate_for_backtest"].fillna(False).astype(bool)
    board_signal_passed = board["signal_gate_passed"].fillna(False).astype(bool)
    board_selected = board["selected_for_backtest"].fillna(False).astype(bool)
    if not board_selected.any():
        explicit_no_candidate = (
            not board_candidates.any()
            and not board_signal_passed.any()
            and manifest.get("alpha_status") == "not_evaluated_by_backtest"
            and manifest.get("any_candidate_for_backtest") is False
            and manifest.get("backtest_ready") is False
            and manifest.get("research_status") == "no_candidate_signal_passed"
            and bool(manifest.get("no_candidate_reason"))
        )
        if not explicit_no_candidate:
            failures.append("FAIL: leaderboard has no selected model and manifest does not explicitly document no-candidate state")
    if board_candidates.any() and not board_selected.any():
        failures.append("FAIL: candidate_for_backtest rows exist but no selected_for_backtest row was chosen")
    if board_selected.any() and manifest.get("backtest_ready") is False:
        failures.append("FAIL: selected model exists but manifest backtest_ready is false")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    parser.add_argument("--section", default="modeling")
    args = parser.parse_args()
    cfg = _merge(load_config(Path(args.config)), args.section)
    failures = validate_model_outputs(cfg)
    if failures:
        print("Model validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Model validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
