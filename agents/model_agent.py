from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from agents.base import AgentBase
from features.feature_engineering import ALLOWED_PROHIBITED_EXACT
from models.walk_forward import generate_purged_walk_forward_splits, summarize_predictions


PROHIBITED_TOKENS = ("target", "label", "future", "forward", "fwd", "lead", "next_return", "ret_fwd", "y_")
MODEL_METADATA_COLUMNS = {
    "date_ts",
    "symbol",
    "snapshot_id",
    "run_id",
    "created_at_utc",
    "snapshot_id_label",
    "run_id_label",
    "created_at_utc_label",
    "feature_set",
    "feature_version",
}
ONCHAIN_HINTS = (
    "onchain",
    "coinmetrics",
    "defillama",
    "missing_",
    "adr_",
    "tx_count",
    "mvrv",
    "chain_tvl",
    "protocol_tvl",
    "fees_",
    "dex_volume",
    "current_supply",
    "issuance",
    "market_cap_usd",
    "realized_cap",
    "nvt_",
)

DIAGNOSTIC_FEATURE_COLUMNS = {
    "onchain_available",
    "coinmetrics_available",
    "defillama_available",
    "onchain_feature_count_non_null",
    "market_data_available",
    "market_history_days_available",
    "is_forward_filled_market",
    "onchain_lag_days",
}


class ModelAgentError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _prohibited_feature_columns(columns: List[str]) -> List[str]:
    bad: List[str] = []
    for col in columns:
        if col in ALLOWED_PROHIBITED_EXACT:
            continue
        lower = col.lower()
        if lower.startswith("y_") or any(token in lower for token in PROHIBITED_TOKENS if token != "y_"):
            bad.append(col)
    return bad


class BaselineCrossSectionalMean:
    def fit(self, X: pd.DataFrame, y: pd.Series, symbols: pd.Series) -> "BaselineCrossSectionalMean":
        train = pd.DataFrame({"symbol": symbols.values, "y": y.values})
        self.symbol_mean_ = train.groupby("symbol")["y"].mean().to_dict()
        self.global_mean_ = float(train["y"].mean()) if len(train) else 0.0
        return self

    def predict(self, X: pd.DataFrame, symbols: pd.Series) -> np.ndarray:
        return np.array([self.symbol_mean_.get(sym, self.global_mean_) for sym in symbols], dtype=float)


class ModelAgent(AgentBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None, horizon: Optional[int] = None, model_names: Optional[List[str]] = None):
        super().__init__(config)
        self._project_root = Path(self.cfg["_project_root"])
        self._model_cfg = self.cfg.get("modeling", {})
        self.override_horizon = horizon
        self.override_models = model_names
        self._dataset: Optional[pd.DataFrame] = None
        self._feature_keep_list: List[str] = []
        self._feature_manifest: Dict[str, Any] = {}
        self._label_manifest: Dict[str, Any] = {}
        self._warnings: List[str] = []
        self._failures: List[Dict[str, Any]] = []

    def prepare(self) -> None:
        cfg = self._model_cfg = self.cfg.get("modeling", {})
        input_path = _resolve(self._project_root, cfg.get("input_path", "data/labels/modeling_dataset.parquet"))
        label_manifest_path = self._project_root / "data/labels/label_manifest.json"
        feature_manifest_path = self._project_root / "data/features/feature_manifest.json"
        keep_path = self._project_root / "data/features/feature_keep_list.json"
        for path in [input_path, label_manifest_path, feature_manifest_path]:
            if not path.exists():
                raise FileNotFoundError(f"Required modeling input missing: {path}")
        self._dataset = pd.read_parquet(input_path)
        self._dataset["date_ts"] = pd.to_datetime(self._dataset["date_ts"], utc=True, errors="coerce").dt.normalize()
        self._dataset = self._dataset.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        max_symbols = cfg.get("max_symbols")
        if max_symbols:
            keep_symbols = sorted(self._dataset["symbol"].drop_duplicates())[: int(max_symbols)]
            self._dataset = self._dataset[self._dataset["symbol"].isin(keep_symbols)].copy()
        if self._dataset.empty:
            raise ModelAgentError("Canonical modeling_dataset.parquet is empty")
        if self._dataset.duplicated(["date_ts", "symbol"]).any():
            raise ModelAgentError("Canonical modeling_dataset.parquet contains duplicate symbol + date_ts rows")
        bad = _prohibited_feature_columns([c for c in self._dataset.columns if c not in MODEL_METADATA_COLUMNS and not c.startswith("label_")])
        if bad and cfg.get("fail_on_leakage", True):
            raise ModelAgentError(f"Modeling dataset contains prohibited feature columns: {bad}")
        with open(label_manifest_path, "r") as fh:
            self._label_manifest = json.load(fh)
        with open(feature_manifest_path, "r") as fh:
            self._feature_manifest = json.load(fh)
        if keep_path.exists():
            keep = json.load(open(keep_path))
            self._feature_keep_list = keep.get("kept_features", []) or keep.get("keep_list", []) or []
        self.logger.info("ModelAgent prepared | rows=%s symbols=%s", len(self._dataset), self._dataset["symbol"].nunique())

    def run(self) -> Dict[str, Any]:
        cfg = self._model_cfg
        self.generate_snapshot_id("modeling_research")
        requested_models = self.override_models or cfg.get("model_names", ["baseline_cross_sectional_mean", "random_forest", "lightgbm"])
        requested_horizons = [self.override_horizon] if self.override_horizon else cfg.get("horizons", [7, 14, 30])
        requested_feature_sets = cfg.get("feature_sets", ["market_only", "market_plus_onchain"])
        all_predictions: List[pd.DataFrame] = []
        fold_metrics_rows: List[Dict[str, Any]] = []
        leaderboard_rows: List[Dict[str, Any]] = []
        feature_importance_rows: List[Dict[str, Any]] = []
        completed_runs: List[Dict[str, Any]] = []

        for horizon in requested_horizons:
            label_col = f"label_fwd_logret_{int(horizon)}d"
            if label_col not in self._dataset.columns:
                self._failures.append({"model_name": "*", "feature_set": "*", "horizon_days": horizon, "failure_reason": f"missing_{label_col}"})
                continue
            panel = self._dataset.dropna(subset=[label_col]).copy()
            if panel.empty:
                self._failures.append({"model_name": "*", "feature_set": "*", "horizon_days": horizon, "failure_reason": "empty_panel_for_horizon"})
                continue
            for feature_set in requested_feature_sets:
                feature_cols = self._select_feature_columns(panel, feature_set)
                if len(feature_cols) < 1:
                    self._failures.append({"model_name": "*", "feature_set": feature_set, "horizon_days": horizon, "failure_reason": "too_few_valid_features"})
                    continue
                for model_name in requested_models:
                    try:
                        preds, folds, leaderboard_row, fi_rows = self._train_combination(
                            panel=panel,
                            label_col=label_col,
                            horizon=int(horizon),
                            feature_set=feature_set,
                            model_name=model_name,
                            feature_cols=feature_cols,
                        )
                        if preds.empty:
                            raise ModelAgentError("empty_oos_predictions")
                        all_predictions.append(preds)
                        fold_metrics_rows.extend(folds)
                        leaderboard_rows.append(leaderboard_row)
                        feature_importance_rows.extend(fi_rows)
                        completed_runs.append({"model_name": model_name, "feature_set": feature_set, "horizon_days": int(horizon)})
                    except Exception as exc:
                        self._failures.append(
                            {"model_name": model_name, "feature_set": feature_set, "horizon_days": int(horizon), "failure_reason": str(exc)}
                        )

        predictions = pd.concat(all_predictions, ignore_index=True) if all_predictions else pd.DataFrame()
        fold_metrics = pd.DataFrame(fold_metrics_rows)
        leaderboard = pd.DataFrame(leaderboard_rows)
        feature_importance = pd.DataFrame(feature_importance_rows)

        if predictions.empty and cfg.get("fail_on_empty_output", True):
            raise ModelAgentError("No valid out-of-sample predictions were produced")
        if fold_metrics.empty and cfg.get("fail_on_no_valid_folds", True):
            raise ModelAgentError("No valid walk-forward folds were produced")

        leaderboard = self._apply_selection_logic(leaderboard)
        manifest = self._build_manifest(predictions, fold_metrics, leaderboard, completed_runs, requested_models, requested_horizons, requested_feature_sets)

        self.metrics["prediction_rows"] = int(len(predictions))
        self.metrics["fold_count"] = int(fold_metrics["fold_id"].nunique()) if not fold_metrics.empty else 0
        self.metrics["completed_runs"] = int(len(completed_runs))
        if not leaderboard.empty:
            selected = leaderboard[leaderboard["selected_for_backtest"]]
            if not selected.empty:
                top = selected.iloc[0]
                self.metrics["best_rank_ic"] = float(top["rank_ic_mean"])

        return {
            "predictions": predictions,
            "fold_metrics": fold_metrics,
            "leaderboard": leaderboard,
            "feature_importance": feature_importance,
            "manifest": manifest,
            "data_quality_md": self._build_quality_report(predictions, fold_metrics, leaderboard),
        }

    def _select_feature_columns(self, panel: pd.DataFrame, feature_set: str) -> List[str]:
        candidates = []
        for col in panel.columns:
            if col in MODEL_METADATA_COLUMNS or col.startswith("label_") or col == "max_horizon_complete":
                continue
            if pd.api.types.is_numeric_dtype(panel[col]):
                candidates.append(col)
        if self._feature_keep_list and self._model_cfg.get("use_pruned_features", True):
            keep = set(self._feature_keep_list)
            candidates = [c for c in candidates if c in keep]
        bad = _prohibited_feature_columns(candidates)
        candidates = [c for c in candidates if c not in bad]
        if not self._model_cfg.get("allow_diagnostic_features", False):
            candidates = [c for c in candidates if c not in DIAGNOSTIC_FEATURE_COLUMNS]

        def is_onchain(col: str) -> bool:
            lower = col.lower()
            return any(hint in lower for hint in ONCHAIN_HINTS)

        def is_market(col: str) -> bool:
            return not is_onchain(col)

        if feature_set == "market_only":
            selected = [c for c in candidates if is_market(c)]
        elif feature_set == "onchain_only":
            selected = [c for c in candidates if is_onchain(c)]
        else:
            selected = candidates
        return sorted(selected)

    def _build_model(self, model_name: str):
        cfg = self._model_cfg
        seed = int(cfg.get("random_seed", 42))
        if model_name == "baseline_cross_sectional_mean":
            return BaselineCrossSectionalMean()
        if model_name == "random_forest":
            from sklearn.ensemble import RandomForestRegressor

            rf = cfg.get("random_forest", {})
            return RandomForestRegressor(
                n_estimators=int(rf.get("n_estimators", 300)),
                max_depth=int(rf.get("max_depth", 6)),
                min_samples_leaf=int(rf.get("min_samples_leaf", 20)),
                max_features=rf.get("max_features", 0.5),
                n_jobs=int(rf.get("n_jobs", -1)),
                random_state=seed,
            )
        if model_name == "lightgbm":
            try:
                import lightgbm as lgb
            except Exception:
                raise ModelAgentError("lightgbm_unavailable")
            lcfg = cfg.get("lightgbm", {})
            return lgb.LGBMRegressor(
                n_estimators=int(lcfg.get("n_estimators", 500)),
                learning_rate=float(lcfg.get("learning_rate", 0.03)),
                max_depth=int(lcfg.get("max_depth", 5)),
                num_leaves=int(lcfg.get("num_leaves", 31)),
                min_child_samples=int(lcfg.get("min_child_samples", 30)),
                subsample=float(lcfg.get("subsample", 0.8)),
                colsample_bytree=float(lcfg.get("colsample_bytree", 0.8)),
                reg_alpha=float(lcfg.get("reg_alpha", 0.1)),
                reg_lambda=float(lcfg.get("reg_lambda", 1.0)),
                objective=lcfg.get("objective", "regression"),
                n_jobs=int(lcfg.get("n_jobs", -1)),
                verbose=int(lcfg.get("verbose", -1)),
                random_state=seed,
            )
        raise ModelAgentError(f"unknown_model:{model_name}")

    def _train_combination(self, *, panel: pd.DataFrame, label_col: str, horizon: int, feature_set: str, model_name: str, feature_cols: List[str]):
        wf = self._model_cfg.get("walk_forward", {})
        embargo_days = int(max(wf.get("embargo_days", 30), self._label_manifest.get("recommended_embargo_days", 30), horizon))
        purge_days = int(wf.get("purge_days") or horizon)
        splits = list(
            generate_purged_walk_forward_splits(
                panel,
                date_col="date_ts",
                symbol_col="symbol",
                horizon_days=horizon,
                initial_train_days=int(wf.get("initial_train_days", 504)),
                test_days=int(wf.get("test_days", 30)),
                step_days=int(wf.get("step_days", 30)),
                purge_days=purge_days,
                embargo_days=embargo_days,
                min_train_rows=int(wf.get("min_train_rows", 1000)),
                min_test_rows=int(wf.get("min_test_rows", 100)),
                min_test_symbols=int(wf.get("min_test_symbols", self._model_cfg.get("min_test_symbols_per_date", 10))),
            )
        )
        if not splits:
            raise ModelAgentError("no_valid_folds")

        prediction_frames: List[pd.DataFrame] = []
        fold_rows: List[Dict[str, Any]] = []
        importances: List[np.ndarray] = []
        used_folds = 0

        for split in splits:
            train = panel.iloc[split.train_idx].copy()
            test = panel.iloc[split.test_idx].copy()
            y_train = pd.to_numeric(train[label_col], errors="coerce")
            y_test = pd.to_numeric(test[label_col], errors="coerce")
            valid_train = np.isfinite(y_train.to_numpy(dtype="float64", na_value=np.nan))
            valid_test = np.isfinite(y_test.to_numpy(dtype="float64", na_value=np.nan))
            dropped_train_labels = int((~valid_train).sum())
            dropped_test_labels = int((~valid_test).sum())
            train = train.loc[valid_train].copy()
            test = test.loc[valid_test].copy()
            y_train = y_train.loc[valid_train]
            y_test = y_test.loc[valid_test]
            if len(train) < int(wf.get("min_train_rows", 1000)):
                raise ModelAgentError(f"too_few_valid_training_labels_after_drop:{len(train)}")
            if len(test) < int(wf.get("min_test_rows", 100)):
                raise ModelAgentError(f"too_few_valid_test_labels_after_drop:{len(test)}")
            X_train_raw = train[feature_cols].replace([np.inf, -np.inf], np.nan)
            X_test_raw = test[feature_cols].replace([np.inf, -np.inf], np.nan)
            if self._model_cfg.get("feature_imputation", "train_median") != "train_median":
                raise ModelAgentError("unsupported_feature_imputation_policy")
            medians = X_train_raw.median(numeric_only=True).replace([np.inf, -np.inf], np.nan)
            X_train = X_train_raw.fillna(medians).fillna(0.0)
            X_test = X_test_raw.fillna(medians).fillna(0.0)
            model = self._build_model(model_name)
            if model_name == "baseline_cross_sectional_mean":
                model.fit(X_train, y_train, train["symbol"])
                pred = model.predict(X_test, test["symbol"])
            else:
                model.fit(X_train, y_train)
                pred = np.asarray(model.predict(X_test), dtype=float)
                if hasattr(model, "feature_importances_"):
                    importances.append(np.asarray(model.feature_importances_, dtype=float))

            fold_pred = test[["date_ts", "symbol"]].copy()
            fold_pred["model_name"] = model_name
            fold_pred["feature_set"] = feature_set
            fold_pred["horizon_days"] = horizon
            fold_pred["fold_id"] = split.fold_id
            fold_pred["prediction"] = pred
            fold_pred["actual_forward_return"] = y_test.to_numpy()
            fold_pred["train_start"] = split.train_start
            fold_pred["train_end"] = split.train_end_purged
            fold_pred["test_start"] = split.test_start
            fold_pred["test_end"] = split.test_end
            fold_pred["snapshot_id"] = self.snapshot_id
            fold_pred["run_id"] = self.run_id
            fold_pred["prediction_rank"] = fold_pred.groupby("date_ts")["prediction"].rank(method="first", ascending=False)
            fold_pred["prediction_rank_pct"] = fold_pred.groupby("date_ts")["prediction"].rank(method="average", pct=True, ascending=True)
            fold_pred["actual_rank"] = fold_pred.groupby("date_ts")["actual_forward_return"].rank(method="first", ascending=False)
            fold_pred["actual_rank_pct"] = fold_pred.groupby("date_ts")["actual_forward_return"].rank(method="average", pct=True, ascending=True)
            fold_pred["is_top_5"] = fold_pred["prediction_rank"] <= 5
            fold_pred["is_top_10"] = fold_pred["prediction_rank"] <= 10
            fold_pred["is_top_20"] = fold_pred["prediction_rank"] <= 20
            fold_pred["is_bottom_10"] = fold_pred["prediction_rank"] > (fold_pred.groupby("date_ts")["prediction_rank"].transform("max") - 10)
            min_assets_per_date = int(self._model_cfg.get("min_assets_per_prediction_date", 20))
            date_counts = fold_pred.groupby("date_ts")["symbol"].transform("nunique")
            fold_pred = fold_pred[date_counts >= min_assets_per_date].copy()
            if fold_pred.empty:
                continue
            prediction_frames.append(fold_pred)

            fold_summary = summarize_predictions(fold_pred, n_features=len(feature_cols))
            fold_rows.append(
                {
                    "model_name": model_name,
                    "feature_set": feature_set,
                    "horizon_days": horizon,
                    "fold_id": split.fold_id,
                    "train_start": split.train_start,
                    "train_end_raw": split.train_end_raw,
                    "train_end_purged": split.train_end_purged,
                    "embargo_start": split.embargo_start,
                    "embargo_end": split.embargo_end,
                    "test_start": split.test_start,
                    "test_end": split.test_end,
                    "train_rows": split.train_rows,
                    "test_rows": split.test_rows,
                    "train_symbols": split.train_symbols,
                    "test_symbols": split.test_symbols,
                    "purge_days": split.purge_days,
                    "embargo_days": split.embargo_days,
                    "n_features": len(feature_cols),
                    "dropped_non_finite_train_labels": dropped_train_labels,
                    "dropped_non_finite_test_labels": dropped_test_labels,
                    **fold_summary,
                }
            )
            used_folds += 1

        predictions = pd.concat(prediction_frames, ignore_index=True) if prediction_frames else pd.DataFrame()
        if predictions.empty:
            raise ModelAgentError("empty_oos_predictions")
        leaderboard_row = summarize_predictions(predictions, n_features=len(feature_cols))
        leaderboard_row.update(
            {
                "model_name": model_name,
                "feature_set": feature_set,
                "horizon_days": horizon,
                "failure_reason": None,
                "selected_for_backtest": False,
                "fold_count": int(used_folds),
                "signal_status": "failed_signal_screen",
                "signal_gate_passed": False,
                "signal_gate_failure_reason": "",
                "candidate_for_backtest": False,
                "alpha_status": "not_evaluated_by_backtest",
                "missing_feature_fraction": float(panel[feature_cols].isna().mean().mean()) if feature_cols else 1.0,
            }
        )
        fi_rows = []
        if importances:
            mean_importance = np.mean(np.vstack(importances), axis=0)
            for feat, importance in zip(feature_cols, mean_importance):
                fi_rows.append(
                    {
                        "model_name": model_name,
                        "feature_set": feature_set,
                        "horizon_days": horizon,
                        "feature_name": feat,
                        "importance": float(importance),
                        "snapshot_id": self.snapshot_id,
                        "run_id": self.run_id,
                    }
                )
        return predictions, fold_rows, leaderboard_row, fi_rows

    def _apply_selection_logic(self, leaderboard: pd.DataFrame) -> pd.DataFrame:
        if leaderboard.empty:
            return leaderboard
        board = leaderboard.copy()
        board["failure_reason"] = board["failure_reason"].where(board["failure_reason"].notna(), None)
        board["selected_for_backtest"] = False
        board["signal_gate_passed"] = False
        board["candidate_for_backtest"] = False
        board["alpha_status"] = "not_evaluated_by_backtest"
        board["composite_score"] = (
            pd.to_numeric(board["rank_ic_mean"], errors="coerce").fillna(-999)
            + pd.to_numeric(board["top_bottom_10_spread"], errors="coerce").fillna(-999)
            + pd.to_numeric(board["top_10_hit_rate"], errors="coerce").fillna(-999)
        )
        gate = self._model_cfg.get("signal_gate", {})
        failures: List[str] = []
        allow_baseline = bool(self._model_cfg.get("allow_baseline_candidate", False))
        def _num(value: Any, default: float = 0.0) -> float:
            parsed = pd.to_numeric(value, errors="coerce")
            return default if pd.isna(parsed) else float(parsed)
        for idx, row in board.iterrows():
            row_failures: List[str] = []
            if pd.notna(row.get("failure_reason")):
                row_failures.append(str(row.get("failure_reason")))
            if row.get("model_name") == "baseline_cross_sectional_mean" and not allow_baseline:
                row_failures.append("diagnostic_baseline_only")
            if _num(row.get("rank_ic_mean")) < float(gate.get("min_rank_ic_mean", 0.01)):
                row_failures.append("rank_ic_mean_below_gate")
            if _num(row.get("rank_ic_tstat")) < float(gate.get("min_rank_ic_tstat", 1.5)):
                row_failures.append("rank_ic_tstat_below_gate")
            if _num(row.get("top_bottom_10_spread")) < float(gate.get("min_top_bottom_10_spread", 0.0)):
                row_failures.append("top_bottom_spread_below_gate")
            if _num(row.get("prediction_coverage")) < float(gate.get("min_prediction_coverage", 0.80)):
                row_failures.append("prediction_coverage_below_gate")
            if int(row.get("fold_count", 0) or 0) < int(gate.get("min_folds", 3)):
                row_failures.append("fold_count_below_gate")
            passed = not row_failures
            board.loc[idx, "signal_gate_passed"] = passed
            board.loc[idx, "candidate_for_backtest"] = passed
            board.loc[idx, "signal_status"] = "passed_signal_screen" if passed else "failed_signal_screen"
            board.loc[idx, "signal_gate_failure_reason"] = ";".join(row_failures)
            if row.get("model_name") == "baseline_cross_sectional_mean":
                self._warnings.append("baseline_cross_sectional_mean is a symbol historical mean diagnostic baseline, not a cross-sectional alpha proof.")
        eligible = board[board["candidate_for_backtest"] == True]  # noqa: E712
        if not eligible.empty:
            idx = eligible.sort_values(["composite_score", "rank_ic_mean"], ascending=False).index[0]
            board.loc[idx, "selected_for_backtest"] = True
        return board.sort_values(["selected_for_backtest", "composite_score"], ascending=[False, False]).reset_index(drop=True)

    def _build_manifest(self, predictions, fold_metrics, leaderboard, completed_runs, requested_models, requested_horizons, requested_feature_sets):
        selected = leaderboard[leaderboard["selected_for_backtest"]] if not leaderboard.empty else pd.DataFrame()
        top = selected.iloc[0] if not selected.empty else None
        alpha_status = "not_evaluated_by_backtest"
        any_signal_gate_passed = bool(leaderboard.get("signal_gate_passed", pd.Series(dtype=bool)).fillna(False).astype(bool).any()) if not leaderboard.empty else False
        any_candidate_for_backtest = bool(leaderboard.get("candidate_for_backtest", pd.Series(dtype=bool)).fillna(False).astype(bool).any()) if not leaderboard.empty else False
        backtest_ready = bool(top is not None and any_candidate_for_backtest)
        no_candidate_reason = None
        research_status = "candidate_signal_ready_for_backtest" if backtest_ready else "no_candidate_signal_passed"
        if not backtest_ready:
            reasons: List[str] = []
            if leaderboard.empty:
                reasons.append("leaderboard_empty")
            elif "signal_gate_failure_reason" in leaderboard.columns:
                reasons = sorted(
                    {
                        reason
                        for text in leaderboard["signal_gate_failure_reason"].dropna().astype(str)
                        for reason in text.split(";")
                        if reason
                    }
                )
            no_candidate_reason = ";".join(reasons) if reasons else "no_leaderboard_row_passed_signal_gate"
        return {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": _utcnow_iso(),
            "modeling_dataset_path": self._model_cfg.get("input_path", "data/labels/modeling_dataset.parquet"),
            "label_manifest_path": "data/labels/label_manifest.json",
            "feature_manifest_path": "data/features/feature_manifest.json",
            "requested_models": requested_models,
            "requested_horizons": requested_horizons,
            "requested_feature_sets": requested_feature_sets,
            "completed_runs": completed_runs,
            "failed_runs": self._failures,
            "selected_model": None if top is None else top["model_name"],
            "selected_feature_set": None if top is None else top["feature_set"],
            "selected_horizon_days": None if top is None else int(top["horizon_days"]),
            "alpha_status": alpha_status,
            "any_signal_gate_passed": any_signal_gate_passed,
            "any_candidate_for_backtest": any_candidate_for_backtest,
            "no_candidate_reason": no_candidate_reason,
            "backtest_ready": backtest_ready,
            "research_status": research_status,
            "best_rank_ic": None if top is None else float(top["rank_ic_mean"]),
            "best_rank_ic_tstat": None if top is None else float(top["rank_ic_tstat"]),
            "best_top_bottom_spread": None if top is None else float(top["top_bottom_10_spread"]),
            "prediction_rows": int(len(predictions)),
            "fold_count": int(fold_metrics["fold_id"].nunique()) if not fold_metrics.empty else 0,
            "embargo_days": int(max(self._model_cfg.get("walk_forward", {}).get("embargo_days", 30), self._label_manifest.get("recommended_embargo_days", 30))),
            "purge_days": None,
            "warnings": self._warnings,
            "limitations": [
                "Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled."
            ],
            "output_files": {
                "predictions": str(self._project_root / "data/predictions/model_predictions.parquet"),
                "fold_metrics": str(self._project_root / "data/predictions/fold_metrics.parquet"),
                "leaderboard": str(self._project_root / "data/predictions/model_leaderboard.parquet"),
                "manifest": str(self._project_root / "data/predictions/model_manifest.json"),
                "feature_importance": str(self._project_root / "data/predictions/feature_importance.parquet"),
                "quality": str(self._project_root / "data/predictions/data_quality_model.md"),
            },
        }

    def _build_quality_report(self, predictions, fold_metrics, leaderboard) -> str:
        lines = [
            "# Data Quality Model",
            "",
            f"- Prediction rows: {len(predictions)}",
            f"- Fold rows: {len(fold_metrics)}",
            f"- Leaderboard rows: {len(leaderboard)}",
            f"- Failed combinations: {len(self._failures)}",
            "",
            "## Limitations",
            "- Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
            "",
        ]
        return "\n".join(lines)

    def persist(self, result: Dict[str, Any]) -> None:
        pred_dir = self.get_path("predictions")
        pred_dir.mkdir(parents=True, exist_ok=True)
        predictions = result["predictions"]
        fold_metrics = result["fold_metrics"]
        leaderboard = result["leaderboard"]
        feature_importance = result["feature_importance"]
        manifest = result["manifest"]

        if predictions.empty and self._model_cfg.get("fail_on_empty_output", True):
            raise ModelAgentError("No predictions to persist")

        predictions_path = pred_dir / "model_predictions.parquet"
        predictions.to_parquet(predictions_path, index=False)
        self.output_paths["model_predictions"] = str(predictions_path)

        fold_path = pred_dir / "fold_metrics.parquet"
        fold_metrics.to_parquet(fold_path, index=False)
        self.output_paths["fold_metrics"] = str(fold_path)

        leaderboard_path = pred_dir / "model_leaderboard.parquet"
        leaderboard.to_parquet(leaderboard_path, index=False)
        self.output_paths["model_leaderboard"] = str(leaderboard_path)

        fi_path = pred_dir / "feature_importance.parquet"
        feature_importance.to_parquet(fi_path, index=False)
        self.output_paths["feature_importance"] = str(fi_path)

        manifest_path = pred_dir / "model_manifest.json"
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        self.output_paths["model_manifest"] = str(manifest_path)

        quality_path = pred_dir / "data_quality_model.md"
        quality_path.write_text(result["data_quality_md"])
        self.output_paths["data_quality_model"] = str(quality_path)
