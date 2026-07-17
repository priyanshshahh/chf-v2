from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from agents.base import AgentBase
from features.feature_engineering import ALLOWED_PROHIBITED_EXACT
from features.feature_source_map import FEATURE_SOURCE_MAP_FILENAME, load_feature_source_map
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
        self._feature_source_map: Dict[str, str] = {}
        self._optuna_results: Dict[str, Dict[str, Any]] = {}
        self._stacking_results: Dict[str, Dict[str, Any]] = {}
        self._meta_labeling_results: Dict[str, Dict[str, Any]] = {}
        self._regime_results: Dict[str, Dict[str, Any]] = {}
        self._regime_map: Optional[Dict[Any, str]] = None
        self._advanced_cfg_loaded: bool = False
        self._advanced_cfg_path: Optional[str] = None
        self._feature_manifest: Dict[str, Any] = {}
        self._label_manifest: Dict[str, Any] = {}
        self._warnings: List[str] = []
        self._failures: List[Dict[str, Any]] = []

    def _content_hash(self) -> str:
        """Deterministic 16-hex fingerprint of the modeling dataset (date_ts, symbol, labels +
        features). Mirrors the other agents' content hashing — the blueprint's SHA-256
        reproducibility guarantee, so an identical dataset yields an identical run fingerprint."""
        if self._dataset is None or self._dataset.empty:
            return ""
        cols = [c for c in self._dataset.columns if c not in {"snapshot_id", "run_id", "created_at_utc",
                "snapshot_id_label", "run_id_label", "created_at_utc_label"}]
        sub = self._dataset[sorted(cols)].copy()
        if "date_ts" in sub.columns:
            sub["date_ts"] = pd.to_datetime(sub["date_ts"], utc=True)
        sort_keys = [k for k in ["symbol", "date_ts"] if k in sub.columns]
        if sort_keys:
            sub = sub.sort_values(sort_keys)
        import hashlib
        return hashlib.sha256(sub.to_json(orient="records", date_format="iso").encode("utf-8")).hexdigest()[:16]

    def _log_to_mlflow(self, manifest: Dict[str, Any]) -> None:
        """Log the model run to MLflow (params/metrics/tags/hash + artifacts). Non-fatal, gated
        by mlflow.log_model_run."""
        mlcfg = self.cfg.get("mlflow", {}) or {}
        if not mlcfg.get("log_model_run", True):
            return
        try:
            import mlflow
        except Exception as exc:  # pragma: no cover
            self.logger.warning(f"mlflow not available, skipping model logging: {exc}")
            return
        try:
            uri = str(mlcfg.get("tracking_uri", "mlruns"))
            if "://" not in uri and not Path(uri).is_absolute():
                uri = str(self._project_root / uri)
            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(mlcfg.get("experiment_name", "CHF_experiments"))
            with mlflow.start_run(run_name=f"model_{self.run_id}"):
                mlflow.set_tags({"agent": "ModelAgent", "run_id": self.run_id,
                                 "snapshot_id": self.snapshot_id or "",
                                 "data_content_hash": manifest.get("data_content_hash", ""),
                                 "research_status": manifest.get("research_status", ""),
                                 "alpha_status": manifest.get("alpha_status", "")})
                mlflow.log_params({k: manifest.get(k) for k in
                                   ["selected_model", "selected_feature_set", "selected_horizon_days", "embargo_days"]
                                   if manifest.get(k) is not None})
                metrics = {k: float(self.metrics.get(k, 0) or 0) for k in
                           ["prediction_rows", "fold_count", "completed_runs"]}
                if manifest.get("best_rank_ic") is not None:
                    metrics["best_rank_ic"] = float(manifest["best_rank_ic"])
                if manifest.get("best_rank_ic_tstat") is not None:
                    metrics["best_rank_ic_tstat"] = float(manifest["best_rank_ic_tstat"])
                mlflow.log_metrics(metrics)
                if mlcfg.get("log_artifacts", True):
                    for key in ["model_manifest", "model_leaderboard", "data_quality_model"]:
                        art = self.output_paths.get(key)
                        try:
                            if art and Path(art).exists():
                                mlflow.log_artifact(str(art))
                        except Exception:  # pragma: no cover
                            pass
            self.metrics["mlflow_logged"] = 1.0
        except Exception as exc:  # pragma: no cover
            self.logger.warning(f"mlflow model logging failed (non-fatal): {exc}")

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
        # Explicit per-column source tags emitted by FeatureAgent (or the backfill script).
        # When present, feature-set membership comes from this map; the legacy
        # ONCHAIN_HINTS substring heuristic is only a fallback for unmapped columns.
        source_map_path = _resolve(
            self._project_root,
            cfg.get("feature_source_map_path", f"data/features/{FEATURE_SOURCE_MAP_FILENAME}"),
        )
        self._feature_source_map = load_feature_source_map(source_map_path) or {}
        if self._feature_source_map:
            self.logger.info("Loaded explicit feature source map (%s columns) from %s", len(self._feature_source_map), source_map_path)
        else:
            self.logger.info("No feature source map at %s; falling back to ONCHAIN_HINTS substring classification", source_map_path)
        self._maybe_load_advanced_config()
        self.logger.info("ModelAgent prepared | rows=%s symbols=%s", len(self._dataset), self._dataset["symbol"].nunique())

    def _maybe_load_advanced_config(self) -> None:
        """Optionally merge ``configs/modeling_advanced.yaml`` into ``self._model_cfg``.

        LEAKAGE / REPRODUCIBILITY: this is default-OFF. The advanced config is loaded
        ONLY when the operator opts in via the ``CHF_MODELING_ADVANCED`` environment
        variable (truthy) or the ``modeling.load_advanced_config`` config flag, so the
        canonical ``main.py model`` run is byte-identical and reproducible without it.
        The file's ``modeling_advanced`` block (extra model names, xgboost/catboost
        params, stacking / regime_conditional / meta_labeling knobs) is shallow-merged
        onto ``self._model_cfg``; CLI ``--models`` overrides always win. Tests exercise
        the same knobs by setting ``cfg['modeling']`` directly, so they never touch this
        file (their tmp project root has no such file)."""
        import os

        opt_in = str(os.getenv("CHF_MODELING_ADVANCED", "")).strip().lower() in {"1", "true", "yes", "on"}
        opt_in = opt_in or bool(self._model_cfg.get("load_advanced_config", False))
        if not opt_in:
            return
        raw = os.getenv("CHF_MODELING_ADVANCED_CONFIG") or self._model_cfg.get(
            "advanced_config_path", "configs/modeling_advanced.yaml"
        )
        adv_path = _resolve(self._project_root, raw)
        if not adv_path.exists():
            self._warnings.append(f"advanced_config_not_found:{adv_path}")
            return
        try:
            import yaml

            with open(adv_path, "r") as fh:
                doc = yaml.safe_load(fh) or {}
        except Exception as exc:  # pragma: no cover - defensive
            self._warnings.append(f"advanced_config_load_failed:{exc}")
            return
        block = dict(doc.get("modeling_advanced", {}) or {})
        if not bool(block.get("enabled", False)):
            self._warnings.append(f"advanced_config_present_but_disabled:{adv_path}")
            return
        extra = list(block.pop("extra_model_names", []) or [])
        for key, value in block.items():
            if key == "enabled":
                continue
            self._model_cfg[key] = value
        # CLI --models wins; otherwise append the advanced models (deduped, order-stable).
        if not self.override_models:
            base_models = list(self._model_cfg.get("model_names", []))
            for name in extra:
                if name not in base_models:
                    base_models.append(name)
            self._model_cfg["model_names"] = base_models
        self._advanced_cfg_loaded = True
        self._advanced_cfg_path = str(adv_path)
        self.logger.info(
            "Loaded advanced modeling config from %s | model_names=%s", adv_path, self._model_cfg.get("model_names")
        )

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
            # Explicit source tag (FeatureAgent / backfill) wins; legacy substring
            # hints only classify columns the map does not know about.
            tagged = self._feature_source_map.get(col)
            if tagged is not None:
                return tagged == "onchain"
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

    def _build_model(self, model_name: str, param_overrides: Optional[Dict[str, Any]] = None):
        """Build the estimator for ``model_name`` from static config hyperparameters,
        optionally overridden per-key by ``param_overrides`` (e.g. Optuna best params)."""
        cfg = self._model_cfg
        seed = int(cfg.get("random_seed", 42))
        overrides = param_overrides or {}
        if model_name == "baseline_cross_sectional_mean":
            return BaselineCrossSectionalMean()
        if model_name == "linear_ridge":
            from sklearn.linear_model import Ridge

            ridge = {**cfg.get("linear_ridge", {}), **overrides}
            return Ridge(
                alpha=float(ridge.get("alpha", 10.0)),
                solver=ridge.get("solver", "lsqr"),
            )
        if model_name == "random_forest":
            from sklearn.ensemble import RandomForestRegressor

            rf = {**cfg.get("random_forest", {}), **overrides}
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
            lcfg = {**cfg.get("lightgbm", {}), **overrides}
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
        if model_name == "xgboost":
            try:
                import xgboost as xgb
            except Exception:
                raise ModelAgentError("xgboost_unavailable")
            xcfg = {**cfg.get("xgboost", {}), **overrides}
            return xgb.XGBRegressor(
                n_estimators=int(xcfg.get("n_estimators", 400)),
                learning_rate=float(xcfg.get("learning_rate", 0.03)),
                max_depth=int(xcfg.get("max_depth", 5)),
                min_child_weight=float(xcfg.get("min_child_weight", 5.0)),
                subsample=float(xcfg.get("subsample", 0.8)),
                colsample_bytree=float(xcfg.get("colsample_bytree", 0.8)),
                reg_alpha=float(xcfg.get("reg_alpha", 0.1)),
                reg_lambda=float(xcfg.get("reg_lambda", 1.0)),
                gamma=float(xcfg.get("gamma", 0.0)),
                objective=xcfg.get("objective", "reg:squarederror"),
                tree_method=xcfg.get("tree_method", "hist"),
                n_jobs=int(xcfg.get("n_jobs", -1)),
                verbosity=int(xcfg.get("verbosity", 0)),
                random_state=seed,
            )
        if model_name == "catboost":
            try:
                from catboost import CatBoostRegressor
            except Exception:
                raise ModelAgentError("catboost_unavailable")
            ccfg = {**cfg.get("catboost", {}), **overrides}
            return CatBoostRegressor(
                iterations=int(ccfg.get("iterations", ccfg.get("n_estimators", 400))),
                learning_rate=float(ccfg.get("learning_rate", 0.03)),
                depth=int(ccfg.get("depth", ccfg.get("max_depth", 6))),
                l2_leaf_reg=float(ccfg.get("l2_leaf_reg", 3.0)),
                subsample=float(ccfg.get("subsample", 0.8)),
                loss_function=ccfg.get("loss_function", "RMSE"),
                random_seed=seed,
                thread_count=int(ccfg.get("thread_count", -1)),
                allow_writing_files=False,
                verbose=0,
            )
        raise ModelAgentError(f"unknown_model:{model_name}")

    def _compute_shap_importance(self, model, X: pd.DataFrame) -> Optional[np.ndarray]:
        """Mean(|SHAP value|) per feature via TreeExplainer — the blueprint's model-explainability
        requirement. Deterministic (first-N rows, no sampling RNG), non-fatal, tree-models only.
        Returns a per-feature vector aligned to ``X.columns`` (mean absolute SHAP across rows), or
        ``None`` when disabled / unavailable / not applicable to the model type."""
        if not self._model_cfg.get("compute_shap", True):
            return None
        if X is None or X.empty:
            return None
        try:
            import shap
        except Exception:  # pragma: no cover - shap optional
            return None
        try:
            max_samples = int(self._model_cfg.get("shap_max_samples", 2000))
            Xs = X.iloc[:max_samples] if 0 < max_samples < len(X) else X
            explainer = shap.TreeExplainer(model)
            values = explainer.shap_values(Xs, check_additivity=False)
            if isinstance(values, list):  # multi-output explainers return a list
                values = values[0]
            arr = np.asarray(values, dtype=float)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            if arr.shape[1] != X.shape[1]:
                return None
            return np.abs(arr).mean(axis=0)
        except Exception as exc:  # pragma: no cover - explainer can reject some model types
            self.logger.warning(f"SHAP computation skipped (non-fatal) for {type(model).__name__}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Optuna hyperparameter search (NEXT_STEPS item [3])
    # ------------------------------------------------------------------
    def _optuna_cfg(self) -> Dict[str, Any]:
        return self._model_cfg.get("optuna", {}) or {}

    def _optuna_enabled_for(self, model_name: str) -> bool:
        ocfg = self._optuna_cfg()
        if not bool(ocfg.get("enabled", False)):
            return False
        return model_name in list(ocfg.get("models", ["lightgbm", "random_forest"]))

    def _suggest_optuna_params(self, trial, model_name: str) -> Dict[str, Any]:
        """Bounded, deterministic-order search spaces around the static defaults."""
        if model_name == "random_forest":
            return {
                "n_estimators": trial.suggest_int("n_estimators", 50, 400),
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "min_samples_leaf": trial.suggest_int("min_samples_leaf", 5, 50),
                "max_features": trial.suggest_float("max_features", 0.3, 1.0),
            }
        if model_name == "lightgbm":
            return {
                "n_estimators": trial.suggest_int("n_estimators", 50, 500),
                "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
                "max_depth": trial.suggest_int("max_depth", 3, 8),
                "num_leaves": trial.suggest_int("num_leaves", 15, 127),
                "min_child_samples": trial.suggest_int("min_child_samples", 10, 60),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
            }
        raise ModelAgentError(f"optuna_unsupported_model:{model_name}")

    def _tune_hyperparameters_optuna(
        self,
        *,
        panel: pd.DataFrame,
        label_col: str,
        horizon: int,
        feature_set: str,
        model_name: str,
        feature_cols: List[str],
        outer_splits: List[Any],
        wf: Dict[str, Any],
        purge_days: int,
        embargo_days: int,
    ) -> Optional[Dict[str, Any]]:
        """Leakage-safe Optuna study for one (model, feature_set, horizon) combination.

        LEAKAGE SAFETY: hyperparameters are tuned using ONLY training-window data.
        The tuning panel is sliced to dates <= the earliest outer fold's *purged*
        train end (``min(split.train_end_purged)``), which by construction of the
        purged walk-forward precedes every outer test window. An inner purged +
        embargoed walk-forward (same ``purge_days`` / ``embargo_days`` as the outer
        folds, via ``models.walk_forward.generate_purged_walk_forward_splits``) is
        then built inside that slice, and each trial is scored by the configured
        objective (default: mean rank-IC) on the inner validation folds. Outer
        test-window rows are therefore never seen during tuning; this is enforced
        with an explicit date assertion below, and the tuning boundary dates are
        recorded in the manifest under ``optuna_best_params``.

        Deterministic: TPE sampler seeded from ``modeling.random_seed``, and the
        underlying estimators reuse the same seed via ``_build_model``.

        Returns the best params dict (config overrides), or ``None`` when tuning
        is not possible (optuna missing / no inner folds) — the static config
        hyperparameters are then used unchanged (non-fatal degradation).
        """
        ocfg = self._optuna_cfg()
        combo_key = f"{model_name}|{feature_set}|{int(horizon)}"
        try:
            import optuna
        except Exception as exc:  # pragma: no cover - optuna is in requirements
            self._warnings.append(f"optuna_unavailable_for:{combo_key}:{exc}")
            return None

        seed = int(self._model_cfg.get("random_seed", 42))
        objective_metric = str(ocfg.get("objective_metric", "rank_ic_mean"))
        n_trials = int(ocfg.get("n_trials", 25))

        # --- slice to training-window data only (never outer test rows) ---
        first_test_start = min(split.test_start for split in outer_splits)
        tuning_end = min(split.train_end_purged for split in outer_splits)
        dates = pd.to_datetime(panel["date_ts"], utc=True)
        tuning_panel = panel[dates <= tuning_end].copy().reset_index(drop=True)
        if tuning_panel.empty:
            self._warnings.append(f"optuna_empty_tuning_window:{combo_key}")
            return None
        tuning_max_date = pd.to_datetime(tuning_panel["date_ts"], utc=True).max()
        if tuning_max_date >= first_test_start:
            raise ModelAgentError(
                f"optuna_tuning_window_overlaps_test:{combo_key}:{tuning_max_date}>={first_test_start}"
            )

        inner_wf = {**wf, **(ocfg.get("inner_walk_forward", {}) or {})}
        inner_initial = int(
            inner_wf.get(
                "inner_initial_train_days",
                max(int(inner_wf.get("initial_train_days", 504)) // 2, 30),
            )
        )
        inner_splits = list(
            generate_purged_walk_forward_splits(
                tuning_panel,
                date_col="date_ts",
                symbol_col="symbol",
                horizon_days=int(horizon),
                initial_train_days=inner_initial,
                test_days=int(inner_wf.get("test_days", 30)),
                step_days=int(inner_wf.get("step_days", 30)),
                purge_days=purge_days,
                embargo_days=embargo_days,
                min_train_rows=int(inner_wf.get("min_train_rows", 1000)),
                min_test_rows=int(inner_wf.get("min_test_rows", 100)),
                min_test_symbols=int(inner_wf.get("min_test_symbols", self._model_cfg.get("min_test_symbols_per_date", 10))),
            )
        )
        if not inner_splits:
            self._warnings.append(f"optuna_no_inner_folds:{combo_key}")
            return None

        def objective(trial) -> float:
            params = self._suggest_optuna_params(trial, model_name)
            frames: List[pd.DataFrame] = []
            for split in inner_splits:
                train = tuning_panel.iloc[split.train_idx]
                test = tuning_panel.iloc[split.test_idx]
                y_train = pd.to_numeric(train[label_col], errors="coerce")
                y_test = pd.to_numeric(test[label_col], errors="coerce")
                valid_train = np.isfinite(y_train.to_numpy(dtype="float64", na_value=np.nan))
                valid_test = np.isfinite(y_test.to_numpy(dtype="float64", na_value=np.nan))
                train = train.loc[valid_train]
                test = test.loc[valid_test]
                y_train = y_train.loc[valid_train]
                y_test = y_test.loc[valid_test]
                if train.empty or test.empty:
                    continue
                X_train_raw = train[feature_cols].replace([np.inf, -np.inf], np.nan)
                X_test_raw = test[feature_cols].replace([np.inf, -np.inf], np.nan)
                medians = X_train_raw.median(numeric_only=True).replace([np.inf, -np.inf], np.nan)
                X_train = X_train_raw.fillna(medians).fillna(0.0)
                X_test = X_test_raw.fillna(medians).fillna(0.0)
                model = self._build_model(model_name, params)
                model.fit(X_train, y_train)
                fold_pred = test[["date_ts", "symbol"]].copy()
                fold_pred["fold_id"] = split.fold_id
                fold_pred["prediction"] = np.asarray(model.predict(X_test), dtype=float)
                fold_pred["actual_forward_return"] = y_test.to_numpy()
                frames.append(fold_pred)
            if not frames:
                return -999.0
            pooled = pd.concat(frames, ignore_index=True)
            summary = summarize_predictions(pooled, n_features=len(feature_cols))
            value = summary.get(objective_metric)
            value = float(value) if value is not None and np.isfinite(value) else -999.0
            return value

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        sampler = optuna.samplers.TPESampler(seed=seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        best_params = dict(study.best_params)
        self._optuna_results[combo_key] = {
            "model_name": model_name,
            "feature_set": feature_set,
            "horizon_days": int(horizon),
            "best_params": best_params,
            "best_value": float(study.best_value),
            "objective_metric": objective_metric,
            "n_trials": n_trials,
            "inner_fold_count": len(inner_splits),
            "inner_initial_train_days": inner_initial,
            "purge_days": int(purge_days),
            "embargo_days": int(embargo_days),
            "tuning_data_max_date": tuning_max_date.isoformat(),
            "outer_first_test_start": pd.Timestamp(first_test_start).isoformat(),
            "sampler": "TPESampler",
            "seed": seed,
        }
        self.logger.info(
            "Optuna best for %s: value=%.6f params=%s (tuning<=%s < first test %s)",
            combo_key, float(study.best_value), best_params, tuning_max_date.date(), pd.Timestamp(first_test_start).date(),
        )
        return best_params

    # ------------------------------------------------------------------
    # Advanced modeling: stacking / regime-conditional / meta-labeling
    # (all config-driven, default OFF; strictly walk-forward-safe)
    # ------------------------------------------------------------------
    def _model_available(self, model_name: str) -> bool:
        try:
            self._build_model(model_name)
            return True
        except ModelAgentError:
            return False
        except Exception:  # pragma: no cover - defensive
            return True

    def _meta_cfg(self) -> Dict[str, Any]:
        mc = self._model_cfg.get("meta_labeling", {})
        return mc if isinstance(mc, dict) else {}

    def _meta_labeling_enabled(self) -> bool:
        mc = self._model_cfg.get("meta_labeling")
        if isinstance(mc, dict):
            return bool(mc.get("enabled", False))
        return bool(mc)

    def _regime_conditional_enabled(self) -> bool:
        rc = self._model_cfg.get("regime_conditional")
        if isinstance(rc, dict):
            return bool(rc.get("enabled", False))
        return bool(rc)

    def _load_regime_map(self) -> Dict[Any, str]:
        if self._regime_map is not None:
            return self._regime_map
        rc = self._model_cfg.get("regime_conditional", {})
        path = rc.get("regime_path") if isinstance(rc, dict) else None
        path = path or "data/strategies/regime_daily.parquet"
        p = _resolve(self._project_root, path)
        if not p.exists():
            raise ModelAgentError(f"regime_daily_missing:{p}")
        rdf = pd.read_parquet(p)
        if "date_ts" not in rdf.columns or "regime" not in rdf.columns:
            raise ModelAgentError("regime_daily_missing_columns")
        rdf["date_ts"] = pd.to_datetime(rdf["date_ts"], utc=True, errors="coerce").dt.normalize()
        self._regime_map = dict(zip(rdf["date_ts"], rdf["regime"].astype(str)))
        return self._regime_map

    def _build_meta_model(self):
        """Binary meta-classifier (predict_proba) for the meta-labeling second stage."""
        mcfg = self._meta_cfg()
        seed = int(self._model_cfg.get("random_seed", 42))
        kind = str(mcfg.get("meta_model", "logistic")).lower()
        if kind in {"random_forest", "rf"}:
            from sklearn.ensemble import RandomForestClassifier

            return RandomForestClassifier(
                n_estimators=int(mcfg.get("n_estimators", 200)),
                max_depth=int(mcfg.get("max_depth", 5)),
                min_samples_leaf=int(mcfg.get("min_samples_leaf", 20)),
                n_jobs=-1,
                random_state=seed,
            )
        if kind == "lightgbm":
            try:
                import lightgbm as lgb
            except Exception:
                raise ModelAgentError("lightgbm_unavailable")
            return lgb.LGBMClassifier(
                n_estimators=int(mcfg.get("n_estimators", 200)),
                learning_rate=float(mcfg.get("learning_rate", 0.05)),
                max_depth=int(mcfg.get("max_depth", 5)),
                verbose=-1,
                random_state=seed,
            )
        from sklearn.linear_model import LogisticRegression

        return LogisticRegression(
            C=float(mcfg.get("C", 1.0)),
            max_iter=int(mcfg.get("max_iter", 1000)),
            random_state=seed,
        )

    def _inner_oof_splits(self, train: pd.DataFrame, horizon: int, purge_days: int, embargo_days: int):
        """Purged inner splits of the TRAIN slice for OOF stacking predictions.

        Every returned inner-test window's dates strictly follow the inner-train
        dates by at least ``purge_days`` (no OOF row leaks into its own base-model
        training), and — because ``train`` is itself the outer fold's train slice —
        every OOF date precedes the outer test window."""
        scfg = self._model_cfg.get("stacking", {}) or {}
        iwf = scfg.get("inner_walk_forward", {}) or {}
        dates = np.array(sorted(pd.to_datetime(train["date_ts"], utc=True).unique()))
        n = len(dates)
        if n < 3:
            return []
        splits = []
        init = int(iwf.get("initial_train_days", max(n // 2, 2)))
        test_days = int(iwf.get("test_days", max(n // 5, 1)))
        step_days = int(iwf.get("step_days", test_days))
        try:
            for sp in generate_purged_walk_forward_splits(
                train,
                date_col="date_ts",
                symbol_col="symbol",
                horizon_days=int(horizon),
                initial_train_days=init,
                test_days=test_days,
                step_days=step_days,
                purge_days=int(purge_days),
                embargo_days=int(iwf.get("embargo_days", 0)),
                min_train_rows=int(iwf.get("min_train_rows", 1)),
                min_test_rows=int(iwf.get("min_test_rows", 1)),
                min_test_symbols=int(iwf.get("min_test_symbols", 1)),
            ):
                splits.append((sp.train_idx, sp.test_idx))
        except Exception:  # pragma: no cover - defensive
            splits = []
        if splits:
            return splits
        # Fallback: a single purged holdout so stacking still gets OOF base preds.
        frac = float(scfg.get("inner_holdout_frac", 0.7))
        cut = dates[max(int(n * frac) - 1, 0)]
        test_start = pd.Timestamp(cut) + pd.Timedelta(days=int(purge_days) + 1)
        d = pd.to_datetime(train["date_ts"], utc=True)
        tr = np.flatnonzero((d <= cut).to_numpy())
        te = np.flatnonzero((d >= test_start).to_numpy())
        if len(tr) >= 2 and len(te) >= 1:
            return [(tr, te)]
        return []

    def _fit_meta_learner(self, oof: np.ndarray, y: np.ndarray, learner: str):
        """Fit the stacking meta-learner on OOF base predictions (train-only).

        Returns ``(predict_fn, weights)``. ``learner='nnls'`` uses non-negative
        least squares (convex, non-negative blend); anything else uses ridge."""
        learner = str(learner).lower()
        if learner in {"nnls", "non_negative_least_squares"}:
            from scipy.optimize import nnls

            coef, _ = nnls(oof, y)
            return (lambda M: np.asarray(M) @ coef), np.asarray(coef, dtype=float)
        from sklearn.linear_model import Ridge

        scfg = self._model_cfg.get("stacking", {}) or {}
        ridge = Ridge(alpha=float(scfg.get("meta_alpha", 1.0)), positive=bool(scfg.get("meta_positive", False)))
        ridge.fit(oof, y)
        return (lambda M: ridge.predict(M)), np.asarray(ridge.coef_, dtype=float)

    def _stacked_predict(self, *, base_models, meta_learner, train, test, X_train, X_test, y_train,
                         feature_cols, optuna_params, horizon, purge_days, embargo_days, combo_key, split):
        avail = [m for m in base_models if m not in {"stacked_ensemble", "baseline_cross_sectional_mean"} and self._model_available(m)]
        skipped = [m for m in base_models if m not in avail]
        if skipped:
            self._warnings.append(f"stacking_base_models_unavailable:{combo_key}:{skipped}")
        if len(avail) < 1:
            raise ModelAgentError(f"stacking_no_base_models:{combo_key}")
        inner = self._inner_oof_splits(train, horizon, purge_days, embargo_days)
        y_train_arr = pd.to_numeric(y_train, errors="coerce")
        # Refit each base model on the full outer-train slice for test-time predictions.
        base_test_cols = []
        for m in avail:
            mdl = self._build_model(m, optuna_params)
            mdl.fit(X_train, y_train_arr)
            base_test_cols.append(np.asarray(mdl.predict(X_test), dtype=float))
        base_test = np.column_stack(base_test_cols)

        oof_X_parts, oof_y_parts, oof_dates = [], [], []
        for tr_idx, te_idx in inner:
            Xtr = X_train.iloc[tr_idx]
            ytr = y_train_arr.iloc[tr_idx]
            Xte = X_train.iloc[te_idx]
            if len(Xtr) < 2 or len(Xte) < 1:
                continue
            cols = []
            for m in avail:
                mdl = self._build_model(m, optuna_params)
                mdl.fit(Xtr, ytr)
                cols.append(np.asarray(mdl.predict(Xte), dtype=float))
            oof_X_parts.append(np.column_stack(cols))
            oof_y_parts.append(y_train_arr.iloc[te_idx].to_numpy(dtype=float))
            oof_dates.append(pd.to_datetime(train["date_ts"].iloc[te_idx], utc=True).to_numpy())

        entry = {
            "base_models": avail,
            "meta_learner": str(meta_learner),
            "n_base_models": len(avail),
        }
        if oof_X_parts:
            oof = np.vstack(oof_X_parts)
            oof_y = np.concatenate(oof_y_parts)
            finite = np.isfinite(oof).all(axis=1) & np.isfinite(oof_y)
            oof, oof_y = oof[finite], oof_y[finite]
            if len(oof_y) >= max(2, len(avail)):
                predict_fn, weights = self._fit_meta_learner(oof, oof_y, meta_learner)
                final = np.asarray(predict_fn(base_test), dtype=float)
                oof_max = pd.Timestamp(np.concatenate(oof_dates).max())
                entry.update({
                    "meta_weights": {m: float(w) for m, w in zip(avail, weights)},
                    "oof_rows": int(len(oof_y)),
                    "oof_max_date": oof_max.isoformat(),
                    "test_start": pd.Timestamp(split.test_start).isoformat(),
                    "fold_id": int(split.fold_id),
                    "fallback_equal_weight": False,
                })
                self._stacking_results.setdefault(combo_key, {"folds": []})
                self._stacking_results[combo_key].setdefault("folds", []).append(entry)
                self._stacking_results[combo_key].update({k: entry[k] for k in ("base_models", "meta_learner", "n_base_models")})
                return final
        # Fallback: equal-weight blend (still train-only; recorded honestly).
        self._warnings.append(f"stacking_oof_unavailable_equal_weight:{combo_key}")
        entry.update({
            "meta_weights": {m: 1.0 / len(avail) for m in avail},
            "oof_rows": 0,
            "oof_max_date": None,
            "test_start": pd.Timestamp(split.test_start).isoformat(),
            "fold_id": int(split.fold_id),
            "fallback_equal_weight": True,
        })
        self._stacking_results.setdefault(combo_key, {"folds": []})
        self._stacking_results[combo_key].setdefault("folds", []).append(entry)
        self._stacking_results[combo_key].update({k: entry[k] for k in ("base_models", "meta_learner", "n_base_models")})
        return base_test.mean(axis=1)

    def _regime_predict(self, *, model_name, train, test, X_train, X_test, y_train, optuna_params, combo_key, split):
        reg_map = self._load_regime_map()
        rc = self._model_cfg.get("regime_conditional", {})
        min_rows = int(rc.get("min_regime_train_rows", 200)) if isinstance(rc, dict) else 200
        y_train_arr = pd.to_numeric(y_train, errors="coerce")
        train_reg = train["date_ts"].map(reg_map).fillna("unknown").to_numpy().astype(object)
        test_reg = test["date_ts"].map(reg_map).fillna("unknown").to_numpy().astype(object)

        # All-data fallback model (used when a regime has too few train rows).
        fb = self._build_model(model_name, optuna_params)
        fb.fit(X_train, y_train_arr)
        preds = np.asarray(fb.predict(X_test), dtype=float)
        used = np.array(["_all_data"] * len(test), dtype=object)

        regime_train_counts, regime_models = {}, {}
        for reg in np.unique(train_reg):
            mask_tr = train_reg == reg
            regime_train_counts[str(reg)] = int(mask_tr.sum())
            if int(mask_tr.sum()) >= min_rows:
                m = self._build_model(model_name, optuna_params)
                m.fit(X_train.iloc[mask_tr], y_train_arr.iloc[mask_tr])
                regime_models[reg] = m
        for reg, m in regime_models.items():
            mask_te = test_reg == reg
            if mask_te.any():
                preds[mask_te] = np.asarray(m.predict(X_test.iloc[mask_te]), dtype=float)
                used[mask_te] = str(reg)

        self._regime_results.setdefault(combo_key, {"folds": []})
        self._regime_results[combo_key]["folds"].append({
            "fold_id": int(split.fold_id),
            "test_start": pd.Timestamp(split.test_start).isoformat(),
            "train_regime_counts": regime_train_counts,
            "regime_models_trained": sorted(str(r) for r in regime_models),
            "min_regime_train_rows": min_rows,
        })
        return preds, used

    def _meta_stage(self, *, primary_model, X_train, X_test, y_train, primary_pred_test, train, test,
                    horizon, combo_key, split):
        """Meta-labeling second stage (Lopez de Prado AFML §3.6), inside one fold.

        The meta-model is trained ONLY on the train slice: meta-labels ask whether
        the primary side (``sign`` of its train prediction) matched the realized
        train return, and sample weights come from |train return|. It never sees a
        test-window label. p_meta on the test slice sizes the primary signal."""
        from features.labeling import meta_labels, sample_weights_by_return

        y_idx = y_train.index
        primary_train_pred = np.asarray(primary_model.predict(X_train), dtype=float)
        side_train = pd.Series(np.sign(primary_train_pred), index=y_idx)
        ret_train = pd.Series(pd.to_numeric(y_train, errors="coerce").to_numpy(), index=y_idx)
        meta = meta_labels(side_train, ret_train).reindex(y_idx).fillna(0).astype(int)
        w = sample_weights_by_return(ret_train, normalize="mean").reindex(y_idx).fillna(0.0)

        if meta.nunique() < 2:
            p_meta = np.full(len(test), float(meta.mean()))
        else:
            mm = self._build_meta_model()
            try:
                mm.fit(X_train, meta.to_numpy(), sample_weight=w.to_numpy())
            except TypeError:
                mm.fit(X_train, meta.to_numpy())
            proba = mm.predict_proba(X_test)
            classes = list(getattr(mm, "classes_", [0, 1]))
            pos = classes.index(1) if 1 in classes else proba.shape[1] - 1
            p_meta = np.asarray(proba[:, pos], dtype=float)

        tau = float(self._meta_cfg().get("threshold", 0.0))
        gate = (p_meta >= tau).astype(float) if tau > 0 else np.ones_like(p_meta)
        sized = np.asarray(primary_pred_test, dtype=float) * p_meta * gate

        self._meta_labeling_results.setdefault(combo_key, {"folds": []})
        self._meta_labeling_results[combo_key]["folds"].append({
            "fold_id": int(split.fold_id),
            "meta_train_max_date": pd.Timestamp(pd.to_datetime(train["date_ts"], utc=True).max()).isoformat(),
            "test_start": pd.Timestamp(split.test_start).isoformat(),
            "meta_positive_rate_train": float(meta.mean()),
            "meta_model": str(self._meta_cfg().get("meta_model", "logistic")),
            "threshold": tau,
        })
        return p_meta, sized

    def _produce_fold_prediction(self, *, model_name, feature_set, train, test, X_train, X_test, y_train,
                                 feature_cols, optuna_params, horizon, purge_days, embargo_days, label_col,
                                 split, importances, shap_importances) -> Dict[str, Any]:
        """Dispatch a single walk-forward fold to the requested model mode.

        Every mode fits on the (already purged/embargoed) train slice only; nothing
        from the test window influences training."""
        combo_key = f"{model_name}|{feature_set}|{int(horizon)}"
        extra: Dict[str, Any] = {}

        if model_name == "baseline_cross_sectional_mean":
            model = self._build_model(model_name, optuna_params)
            model.fit(X_train, y_train, train["symbol"])
            pred = np.asarray(model.predict(X_test, test["symbol"]), dtype=float)
            return {"prediction": pred, "extra_columns": extra}

        if model_name == "stacked_ensemble":
            scfg = self._model_cfg.get("stacking", {}) or {}
            base_models = list(scfg.get("base_models", ["linear_ridge", "random_forest", "lightgbm", "xgboost", "catboost"]))
            meta_learner = scfg.get("meta_learner", "ridge")
            pred = self._stacked_predict(
                base_models=base_models, meta_learner=meta_learner, train=train, test=test,
                X_train=X_train, X_test=X_test, y_train=y_train, feature_cols=feature_cols,
                optuna_params=optuna_params, horizon=horizon, purge_days=purge_days,
                embargo_days=embargo_days, combo_key=combo_key, split=split,
            )
            return {"prediction": pred, "extra_columns": extra}

        regime_enabled = self._regime_conditional_enabled()
        meta_enabled = self._meta_labeling_enabled()
        primary_model = None
        if regime_enabled:
            pred, regime_used = self._regime_predict(
                model_name=model_name, train=train, test=test, X_train=X_train, X_test=X_test,
                y_train=y_train, optuna_params=optuna_params, combo_key=combo_key, split=split,
            )
            extra["regime_model"] = regime_used
        else:
            model = self._build_model(model_name, optuna_params)
            model.fit(X_train, y_train)
            pred = np.asarray(model.predict(X_test), dtype=float)
            if hasattr(model, "feature_importances_"):
                importances.append(np.asarray(model.feature_importances_, dtype=float))
            shap_vec = self._compute_shap_importance(model, X_test)
            if shap_vec is not None and shap_vec.shape[0] == len(feature_cols):
                shap_importances.append(shap_vec)
            primary_model = model

        if meta_enabled and primary_model is not None:
            p_meta, sized = self._meta_stage(
                primary_model=primary_model, X_train=X_train, X_test=X_test, y_train=y_train,
                primary_pred_test=pred, train=train, test=test, horizon=horizon,
                combo_key=combo_key, split=split,
            )
            extra["meta_prob"] = p_meta
            extra["primary_prediction"] = pred
            pred = sized
        elif meta_enabled and regime_enabled:
            self._warnings.append(f"meta_labeling_skipped_with_regime_conditional:{combo_key}")

        return {"prediction": pred, "extra_columns": extra}

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

        # Optional leakage-safe Optuna search: tunes on an inner walk-forward built
        # only from pre-test training-window dates; None -> static config params.
        optuna_params: Optional[Dict[str, Any]] = None
        if self._optuna_enabled_for(model_name):
            optuna_params = self._tune_hyperparameters_optuna(
                panel=panel,
                label_col=label_col,
                horizon=horizon,
                feature_set=feature_set,
                model_name=model_name,
                feature_cols=feature_cols,
                outer_splits=splits,
                wf=wf,
                purge_days=purge_days,
                embargo_days=embargo_days,
            )

        prediction_frames: List[pd.DataFrame] = []
        fold_rows: List[Dict[str, Any]] = []
        importances: List[np.ndarray] = []
        shap_importances: List[np.ndarray] = []
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
            fold_result = self._produce_fold_prediction(
                model_name=model_name,
                feature_set=feature_set,
                train=train.reset_index(drop=True),
                test=test.reset_index(drop=True),
                X_train=X_train.reset_index(drop=True),
                X_test=X_test.reset_index(drop=True),
                y_train=y_train.reset_index(drop=True),
                feature_cols=feature_cols,
                optuna_params=optuna_params,
                horizon=horizon,
                purge_days=purge_days,
                embargo_days=embargo_days,
                label_col=label_col,
                split=split,
                importances=importances,
                shap_importances=shap_importances,
            )
            pred = np.asarray(fold_result["prediction"], dtype=float)

            fold_pred = test[["date_ts", "symbol"]].copy()
            fold_pred["model_name"] = model_name
            fold_pred["feature_set"] = feature_set
            fold_pred["horizon_days"] = horizon
            fold_pred["fold_id"] = split.fold_id
            fold_pred["prediction"] = pred
            for extra_col, extra_vals in fold_result.get("extra_columns", {}).items():
                fold_pred[extra_col] = np.asarray(extra_vals)
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
            mean_shap = np.mean(np.vstack(shap_importances), axis=0) if shap_importances else None
            for idx, (feat, importance) in enumerate(zip(feature_cols, mean_importance)):
                fi_rows.append(
                    {
                        "model_name": model_name,
                        "feature_set": feature_set,
                        "horizon_days": horizon,
                        "feature_name": feat,
                        "importance": float(importance),
                        "importance_type": "impurity",
                        "mean_abs_shap": float(mean_shap[idx]) if mean_shap is not None else None,
                        "shap_folds": int(len(shap_importances)),
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
            "data_content_hash": self._content_hash(),
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
            "optuna": {
                "enabled": bool(self._optuna_cfg().get("enabled", False)),
                "n_trials": int(self._optuna_cfg().get("n_trials", 25)),
                "models": list(self._optuna_cfg().get("models", ["lightgbm", "random_forest"])),
                "objective_metric": str(self._optuna_cfg().get("objective_metric", "rank_ic_mean")),
            },
            "optuna_best_params": self._optuna_results,
            "advanced_config": {
                "loaded": bool(self._advanced_cfg_loaded),
                "path": self._advanced_cfg_path,
                "stacking_enabled": "stacked_ensemble" in (requested_models or []),
                "regime_conditional_enabled": self._regime_conditional_enabled(),
                "meta_labeling_enabled": self._meta_labeling_enabled(),
            },
            "stacking": self._stacking_results,
            "meta_labeling": self._meta_labeling_results,
            "regime_conditional": self._regime_results,
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

        # Prediction-only export for PortfolioAgent: its leakage guard rejects
        # any realized/label/target columns, so hand it a sanitized file.
        forbidden_terms = ("actual", "label", "future", "realized", "target", "y_")
        portfolio_cols = [
            c for c in predictions.columns if not any(t in c.lower() for t in forbidden_terms)
        ]
        portfolio_input_path = pred_dir / "model_predictions_portfolio_input.parquet"
        predictions[portfolio_cols].to_parquet(portfolio_input_path, index=False)
        self.output_paths["model_predictions_portfolio_input"] = str(portfolio_input_path)

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

        self._log_to_mlflow(manifest)
