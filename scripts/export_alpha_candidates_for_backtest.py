#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config


FORBIDDEN_TOKENS = (
    "actual",
    "actual_return",
    "actual_forward_return",
    "actual_rank",
    "label",
    "future",
    "realized",
    "target",
    "y_",
)
PREDICTION_COLUMNS = [
    "date_ts",
    "symbol",
    "model_name",
    "feature_set",
    "horizon_days",
    "fold_id",
    "prediction",
    "prediction_rank",
    "prediction_rank_pct",
    "snapshot_id",
    "run_id",
]


def _slug(row: pd.Series | Dict[str, Any]) -> str:
    model = str(row["model_name"])
    feature_set = str(row["feature_set"])
    label_target = str(row.get("label_target", "signal"))
    horizon = int(row["horizon_days"])
    raw = f"{model}_{feature_set}_{label_target}_{horizon}d"
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_")


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _default_paths(cfg: Dict[str, Any]) -> Dict[str, Path]:
    root = Path(cfg["_project_root"])
    return {
        "leaderboard": root / "data/predictions/alpha_model_leaderboard.parquet",
        "predictions": root / "data/predictions/alpha_research_predictions.parquet",
        "research_manifest": root / "data/research/research_manifest.json",
        "out_predictions": root / "data/predictions/candidate_model_predictions.parquet",
        "out_leaderboard": root / "data/predictions/candidate_model_leaderboard.parquet",
        "out_manifest": root / "data/predictions/candidate_model_manifest.json",
        "by_signal_dir": root / "data/predictions/candidates_by_signal",
    }


def _candidate_rows(leaderboard: pd.DataFrame) -> pd.DataFrame:
    if "candidate_for_backtest" in leaderboard.columns:
        candidates = leaderboard[leaderboard["candidate_for_backtest"].fillna(False).astype(bool)].copy()
    else:
        gate = leaderboard.get("signal_gate_passed", pd.Series(False, index=leaderboard.index)).fillna(False).astype(bool)
        candidates = leaderboard[gate].copy()
    if candidates.empty:
        raise SystemExit("No candidate_for_backtest=true alpha research rows found; refusing to export diagnostic predictions.")
    required = {"experiment_id", "model_name", "feature_set", "label_target", "horizon_days"}
    missing = required - set(candidates.columns)
    if missing:
        raise SystemExit(f"Candidate leaderboard missing required columns: {sorted(missing)}")
    return candidates.sort_values(
        ["final_research_score", "mean_rank_ic", "rank_ic_tstat", "top_bottom_spread"],
        ascending=[False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _portfolio_safe_predictions(predictions: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    pred = predictions[predictions["experiment_id"].isin(candidates["experiment_id"])].copy()
    if pred.empty:
        raise SystemExit("Candidate export would be empty; no alpha research predictions matched candidate experiment IDs.")
    bad_cols = [c for c in pred.columns if any(tok in c.lower() for tok in FORBIDDEN_TOKENS)]
    pred = pred.drop(columns=bad_cols, errors="ignore")
    required = {"date_ts", "symbol", "model_name", "feature_set", "horizon_days", "fold_id", "prediction"}
    missing = required - set(pred.columns)
    if missing:
        raise SystemExit(f"Candidate predictions missing required safe columns after filtering: {sorted(missing)}")
    pred["date_ts"] = pd.to_datetime(pred["date_ts"], utc=True).dt.normalize()
    pred["prediction"] = pd.to_numeric(pred["prediction"], errors="coerce")
    if pred["prediction"].isna().any() or (~np.isfinite(pred["prediction"])).any():
        raise SystemExit("Candidate predictions contain non-finite prediction values.")
    pred["prediction_rank"] = pred.groupby(["model_name", "feature_set", "horizon_days", "fold_id", "date_ts"])["prediction"].rank(
        method="first",
        ascending=False,
    )
    pred["prediction_rank_pct"] = pred.groupby(["model_name", "feature_set", "horizon_days", "fold_id", "date_ts"])["prediction"].rank(
        method="average",
        pct=True,
    )
    for col in ["snapshot_id", "run_id"]:
        if col not in pred.columns:
            pred[col] = "alpha_candidate_export"
    pred = pred[PREDICTION_COLUMNS].sort_values(
        ["model_name", "feature_set", "horizon_days", "fold_id", "date_ts", "prediction_rank", "symbol"]
    ).reset_index(drop=True)
    dup_key = ["model_name", "feature_set", "horizon_days", "fold_id", "date_ts", "symbol"]
    if pred.duplicated(dup_key).any():
        raise SystemExit(f"Candidate predictions contain duplicate rows under key {dup_key}.")
    combo_key = ["model_name", "feature_set", "horizon_days", "date_ts", "symbol"]
    duplicate_oos_rows = int(pred.duplicated(combo_key).sum())
    if duplicate_oos_rows:
        pred = pred.drop_duplicates(combo_key, keep="first").reset_index(drop=True)
        pred["prediction_rank"] = pred.groupby(["model_name", "feature_set", "horizon_days", "date_ts"])["prediction"].rank(
            method="first",
            ascending=False,
        )
        pred["prediction_rank_pct"] = pred.groupby(["model_name", "feature_set", "horizon_days", "date_ts"])["prediction"].rank(
            method="average",
            pct=True,
        )
        pred.attrs["dropped_duplicate_oos_prediction_rows"] = duplicate_oos_rows
    else:
        pred.attrs["dropped_duplicate_oos_prediction_rows"] = 0
    if pred.duplicated(combo_key).any():
        raise SystemExit(f"Candidate predictions still contain duplicate rows under key {combo_key}.")
    bad_after = [c for c in pred.columns if any(tok in c.lower() for tok in FORBIDDEN_TOKENS)]
    if bad_after:
        raise SystemExit(f"Portfolio-safe export still contains forbidden columns: {bad_after}")
    return pred


def _candidate_leaderboard(candidates: pd.DataFrame, pred: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    pred_counts = pred.groupby(["model_name", "feature_set", "horizon_days"]).size().to_dict()
    for _, row in candidates.iterrows():
        key = (row["model_name"], row["feature_set"], int(row["horizon_days"]))
        rows.append(
            {
                "experiment_id": row["experiment_id"],
                "model_name": row["model_name"],
                "feature_set": row["feature_set"],
                "label_target": row.get("label_target"),
                "horizon_days": int(row["horizon_days"]),
                "rank_ic_mean": float(row.get("mean_rank_ic", np.nan)),
                "rank_ic_tstat": float(row.get("rank_ic_tstat", np.nan)),
                "top_bottom_10_spread": float(row.get("top_bottom_spread", row.get("mean_top_bottom_spread", np.nan))),
                "prediction_rows": int(pred_counts.get(key, 0)),
                "fold_count": int(row.get("n_folds", row.get("fold_count", 0))),
                "signal_gate_passed": True,
                "candidate_for_backtest": True,
                "alpha_status": "not_evaluated_by_backtest",
                "backtest_ready": True,
                "selected_for_backtest": False,
                "alpha_verified": False,
                "failure_reason": "backtest_required",
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        raise SystemExit("Candidate leaderboard export is empty.")
    if int((out["candidate_for_backtest"] == True).sum()) != len(out):
        raise SystemExit("Candidate leaderboard contains non-candidate rows.")
    return out


def export_candidates(cfg: Dict[str, Any]) -> Dict[str, Any]:
    paths = _default_paths(cfg)
    for name in ["leaderboard", "predictions", "research_manifest"]:
        if not paths[name].exists():
            raise SystemExit(f"Missing required alpha research input: {paths[name]}")
    leaderboard = pd.read_parquet(paths["leaderboard"])
    predictions = pd.read_parquet(paths["predictions"])
    with open(paths["research_manifest"], "r") as fh:
        research_manifest = json.load(fh)
    candidates = _candidate_rows(leaderboard)
    safe_pred = _portfolio_safe_predictions(predictions, candidates)
    safe_board = _candidate_leaderboard(candidates, safe_pred)
    if len(safe_board) != 3:
        raise SystemExit(f"Expected exactly 3 candidate combos from Phase 3; found {len(safe_board)}.")
    paths["out_predictions"].parent.mkdir(parents=True, exist_ok=True)
    paths["by_signal_dir"].mkdir(parents=True, exist_ok=True)
    safe_pred.to_parquet(paths["out_predictions"], index=False)
    safe_board.to_parquet(paths["out_leaderboard"], index=False)
    by_signal_outputs = []
    for _, candidate in safe_board.iterrows():
        slug = _slug(candidate)
        pred_one = safe_pred[
            (safe_pred["model_name"] == candidate["model_name"])
            & (safe_pred["feature_set"] == candidate["feature_set"])
            & (safe_pred["horizon_days"].astype(int) == int(candidate["horizon_days"]))
        ].copy()
        board_one = safe_board[safe_board["experiment_id"] == candidate["experiment_id"]].copy()
        if pred_one.empty or board_one.empty:
            raise SystemExit(f"Candidate {slug} produced empty by-signal export.")
        pred_path = paths["by_signal_dir"] / f"{slug}_predictions.parquet"
        board_path = paths["by_signal_dir"] / f"{slug}_leaderboard.parquet"
        manifest_path = paths["by_signal_dir"] / f"{slug}_manifest.json"
        pred_one.to_parquet(pred_path, index=False)
        board_one.to_parquet(board_path, index=False)
        one_manifest = {
            "run_id": f"candidate_export_{slug}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "candidate": board_one.iloc[0].to_dict(),
            "prediction_rows": int(len(pred_one)),
            "alpha_verified": False,
            "backtest_required": True,
            "portfolio_safe": True,
            "canonical_model_predictions_overwritten": False,
            "output_files": {
                "candidate_predictions": str(pred_path),
                "candidate_leaderboard": str(board_path),
                "candidate_manifest": str(manifest_path),
            },
            "limitations": [
                "Single-candidate export is signal-screen output only; BacktestAgent verification is required before any alpha claim.",
                "Current universe is latest-survivor baseline and may overstate historical tradability.",
            ],
        }
        with open(manifest_path, "w") as fh:
            json.dump(one_manifest, fh, indent=2, default=str)
        by_signal_outputs.append(
            {
                "slug": slug,
                "prediction_rows": int(len(pred_one)),
                "prediction_path": str(pred_path),
                "leaderboard_path": str(board_path),
                "manifest_path": str(manifest_path),
            }
        )
    manifest = {
        "run_id": f"candidate_export_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_alpha_research_predictions": str(paths["predictions"]),
        "source_alpha_model_leaderboard": str(paths["leaderboard"]),
        "source_research_manifest": str(paths["research_manifest"]),
        "candidate_count": int(len(safe_board)),
        "prediction_rows": int(len(safe_pred)),
        "dropped_duplicate_oos_prediction_rows": int(safe_pred.attrs.get("dropped_duplicate_oos_prediction_rows", 0)),
        "candidate_experiments": safe_board.to_dict(orient="records"),
        "alpha_verified": False,
        "backtest_required": True,
        "portfolio_safe": True,
        "forbidden_columns_removed": True,
        "canonical_model_predictions_overwritten": False,
        "source_research_summary": {
            "experiments_run": research_manifest.get("experiments_run"),
            "experiments_skipped": research_manifest.get("experiments_skipped"),
            "signal_only": research_manifest.get("signal_only"),
            "canonical_outputs_mutated": research_manifest.get("canonical_outputs_mutated"),
        },
        "limitations": [
            "Candidate export is signal-screen output only; BacktestAgent verification is required before any alpha claim.",
            "Current universe is latest-survivor baseline and may overstate historical tradability.",
        ],
        "output_files": {
            "candidate_predictions": str(paths["out_predictions"]),
            "candidate_leaderboard": str(paths["out_leaderboard"]),
            "candidate_manifest": str(paths["out_manifest"]),
        },
        "by_signal_outputs": by_signal_outputs,
    }
    with open(paths["out_manifest"], "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    manifest = export_candidates(cfg)
    print(
        "Candidate export: PASS "
        f"candidates={manifest['candidate_count']} prediction_rows={manifest['prediction_rows']} "
        f"alpha_verified={manifest['alpha_verified']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
