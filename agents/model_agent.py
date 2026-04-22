"""
CHF ModelAgent
Trains and evaluates ML models with walk-forward validation.
Logs all experiments to local MLflow. Supports RandomForest and LightGBM.

Models:
- RandomForestRegressor (baseline)
- LightGBM Regressor (advanced)

Evaluation:
- Rank IC (primary)
- Hit Rate
- R² (secondary)
- Feature importance
- SHAP values (optional)
"""
from __future__ import annotations

import json
import os
import pickle
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from agents.base import AgentBase
from models.walk_forward import (
    WalkForwardSplit,
    aggregate_fold_metrics,
    compute_fold_metrics,
    rank_ic,
    walk_forward_splits,
)

warnings.filterwarnings("ignore")


class ModelAgent(AgentBase):
    """
    Trains ML models with walk-forward validation.

    Pipeline position: After FeatureAgent + LabelAgent.
    Outputs: data/predictions/predictions_{model}_{horizon}d.parquet
             artifacts/models/{model}_{horizon}d.pkl
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        horizon: Optional[int] = None,
        model_names: Optional[List[str]] = None,
    ):
        super().__init__(config)
        mcfg = self.cfg.get("modeling", {})
        self.horizon = horizon or mcfg.get("default_horizon", 7)
        self.model_names = model_names or mcfg.get("models", ["random_forest", "lightgbm"])
        self._features_df: Optional[pd.DataFrame] = None
        self._labels_df: Optional[pd.DataFrame] = None
        self._feature_cols: List[str] = []
        self._mlflow_experiment_id: Optional[str] = None

    def prepare(self) -> None:
        """Load features and labels."""
        features_dir = self.get_path("features")

        # Try full features first, then market-only
        for fname in ["full_features.parquet", "market_features.parquet"]:
            fpath = features_dir / fname
            if fpath.exists():
                self._features_df = pd.read_parquet(fpath)
                self._features_df["date_ts"] = pd.to_datetime(
                    self._features_df["date_ts"], utc=True
                )
                break

        if self._features_df is None:
            raise FileNotFoundError("No features found. Run FeatureAgent first.")

        # Load labels
        labels_path = self.get_path("labels") / f"labels_{self.horizon}d.parquet"
        if not labels_path.exists():
            raise FileNotFoundError(
                f"Labels for horizon={self.horizon}d not found. Run LabelAgent first."
            )
        self._labels_df = pd.read_parquet(labels_path)
        self._labels_df["date_ts"] = pd.to_datetime(self._labels_df["date_ts"], utc=True)

        # Identify feature columns (exclude metadata)
        exclude_cols = {
            "symbol", "date_ts", "feature_version", "snapshot_id", "run_id",
            "source", "retrieved_at",
        }
        self._feature_cols = [
            c for c in self._features_df.columns
            if c not in exclude_cols
            and self._features_df[c].dtype in (np.float64, np.float32, np.int64, np.int32)
        ]

        # Load feature keep list if available
        keep_path = features_dir / "feature_keep_list.json"
        if keep_path.exists():
            with open(keep_path) as f:
                keep_data = json.load(f)
            keep_list = keep_data.get("keep_list", [])
            if keep_list:
                self._feature_cols = [c for c in self._feature_cols if c in keep_list]

        # Setup MLflow
        self._setup_mlflow()

        self.logger.info(
            f"ModelAgent prepared | horizon={self.horizon}d | "
            f"{len(self._feature_cols)} features | "
            f"models={self.model_names}"
        )

    def _setup_mlflow(self) -> None:
        """Initialize MLflow tracking."""
        try:
            import mlflow
            mlflow_cfg = self.cfg.get("mlflow", {})
            tracking_uri = mlflow_cfg.get("tracking_uri", "mlruns")
            root = Path(self.cfg["_project_root"])
            uri = str(root / tracking_uri)
            mlflow.set_tracking_uri(uri)
            exp_name = mlflow_cfg.get("experiment_name", "CHF_experiments")
            exp = mlflow.get_experiment_by_name(exp_name)
            if exp is None:
                self._mlflow_experiment_id = mlflow.create_experiment(exp_name)
            else:
                self._mlflow_experiment_id = exp.experiment_id
            self.logger.info(f"MLflow tracking URI: {uri}")
        except ImportError:
            self.logger.warning("MLflow not installed, skipping experiment tracking")

    def run(self) -> Dict[str, Any]:
        """
        Train all models with walk-forward validation.
        Returns dict with predictions and metrics per model.
        """
        self.generate_snapshot_id(f"model:h{self.horizon}")

        # Build panel dataset
        panel = self._build_panel()
        if panel.empty:
            self.logger.error("Empty panel dataset")
            return {}

        results = {}
        for model_name in self.model_names:
            self.logger.info(f"Training {model_name} for horizon={self.horizon}d")
            try:
                preds, metrics, model = self._train_model(panel, model_name)
                results[model_name] = {
                    "predictions": preds,
                    "metrics": metrics,
                    "model": model,
                }
                self.metrics[f"{model_name}_rank_ic"] = metrics.get("rank_ic_mean", np.nan)
                self.logger.info(
                    f"{model_name} h={self.horizon}d: "
                    f"Rank IC={metrics.get('rank_ic_mean', 0):.4f} ± "
                    f"{metrics.get('rank_ic_std', 0):.4f}"
                )
            except Exception as e:
                self.logger.error(f"Model {model_name} failed: {e}")

        return results

    def _build_panel(self) -> pd.DataFrame:
        """Merge features and labels into a panel dataset."""
        panel = self._features_df.merge(
            self._labels_df[["symbol", "date_ts", "label_value", "horizon_days"]],
            on=["symbol", "date_ts"],
            how="inner",
        )
        panel = panel.dropna(subset=["label_value"])
        panel = panel.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        self.logger.info(f"Panel dataset: {len(panel)} rows, {panel['symbol'].nunique()} symbols")
        return panel

    def _train_model(
        self,
        panel: pd.DataFrame,
        model_name: str,
    ) -> Tuple[pd.DataFrame, Dict, Any]:
        """Train a single model with walk-forward validation."""
        mcfg = self.cfg.get("modeling", {})
        wf_cfg = mcfg.get("walk_forward", {})

        # Walk-forward splits
        splits = list(walk_forward_splits(
            panel,
            date_col="date_ts",
            initial_train_months=wf_cfg.get("initial_train_months", 12),
            step_months=wf_cfg.get("step_months", 1),
            embargo_days=wf_cfg.get("embargo_days", 7),
            horizon_days=self.horizon,
        ))

        if not splits:
            self.logger.warning(f"No walk-forward splits generated for {model_name}")
            return pd.DataFrame(), {}, None

        fold_metrics = []
        all_predictions = []
        final_model = None

        for split in splits:
            train_df = panel.iloc[split.train_idx]
            val_df = panel.iloc[split.val_idx]

            X_train = train_df[self._feature_cols].fillna(0)
            y_train = train_df["label_value"].values
            X_val = val_df[self._feature_cols].fillna(0)
            y_val = val_df["label_value"].values

            if len(X_train) < 50 or len(X_val) < 5:
                continue

            model = self._build_model(model_name, mcfg)
            model.fit(X_train, y_train)
            y_pred = model.predict(X_val)

            # Store predictions
            pred_df = val_df[["symbol", "date_ts"]].copy()
            pred_df["predicted_return"] = y_pred
            pred_df["actual_return"] = y_val
            pred_df["fold_id"] = split.fold_id
            pred_df["model_name"] = model_name
            pred_df["horizon_days"] = self.horizon
            all_predictions.append(pred_df)

            # Compute metrics
            fm = compute_fold_metrics(y_val, y_pred, split.fold_id, model_name, self.horizon)
            fm["train_start"] = split.train_start.isoformat()
            fm["train_end"] = split.train_end.isoformat()
            fm["val_start"] = split.val_start.isoformat()
            fm["val_end"] = split.val_end.isoformat()
            fold_metrics.append(fm)
            final_model = model

        # Aggregate metrics
        agg_metrics = aggregate_fold_metrics(fold_metrics)
        agg_metrics["model_name"] = model_name
        agg_metrics["horizon_days"] = self.horizon
        agg_metrics["n_features"] = len(self._feature_cols)
        agg_metrics["feature_version"] = self.cfg.get("features", {}).get("feature_version", "v1")
        agg_metrics["snapshot_id"] = self.snapshot_id
        agg_metrics["run_id"] = self.run_id

        # Log to MLflow
        self._log_to_mlflow(model_name, agg_metrics, fold_metrics, final_model)

        # Concatenate predictions
        if all_predictions:
            predictions_df = pd.concat(all_predictions, ignore_index=True)
            predictions_df["model_version"] = "1.0"
            predictions_df["feature_version"] = self.cfg.get("features", {}).get(
                "feature_version", "v1"
            )
            predictions_df["snapshot_id"] = self.snapshot_id
            predictions_df["run_id"] = self.run_id
        else:
            predictions_df = pd.DataFrame()

        return predictions_df, agg_metrics, final_model

    def _build_model(self, model_name: str, mcfg: Dict) -> Any:
        """Instantiate a model from config."""
        seed = self.cfg.get("project", {}).get("seed", 42)

        if model_name == "random_forest":
            from sklearn.ensemble import RandomForestRegressor
            rf_cfg = mcfg.get("random_forest", {})
            return RandomForestRegressor(
                n_estimators=rf_cfg.get("n_estimators", 200),
                max_depth=rf_cfg.get("max_depth", 6),
                min_samples_leaf=rf_cfg.get("min_samples_leaf", 20),
                n_jobs=rf_cfg.get("n_jobs", -1),
                random_state=seed,
            )
        elif model_name == "lightgbm":
            try:
                import lightgbm as lgb
                lgb_cfg = mcfg.get("lightgbm", {})
                return lgb.LGBMRegressor(
                    n_estimators=lgb_cfg.get("n_estimators", 300),
                    learning_rate=lgb_cfg.get("learning_rate", 0.05),
                    max_depth=lgb_cfg.get("max_depth", 6),
                    num_leaves=lgb_cfg.get("num_leaves", 31),
                    min_child_samples=lgb_cfg.get("min_child_samples", 20),
                    n_jobs=lgb_cfg.get("n_jobs", -1),
                    verbose=lgb_cfg.get("verbose", -1),
                    random_state=seed,
                )
            except ImportError:
                self.logger.warning("LightGBM not installed, falling back to RandomForest")
                return self._build_model("random_forest", mcfg)
        elif model_name == "xgboost":
            try:
                import xgboost as xgb
                return xgb.XGBRegressor(
                    n_estimators=300,
                    learning_rate=0.05,
                    max_depth=6,
                    random_state=seed,
                    verbosity=0,
                )
            except ImportError:
                self.logger.warning("XGBoost not installed, falling back to RandomForest")
                return self._build_model("random_forest", mcfg)
        else:
            raise ValueError(f"Unknown model: {model_name}")

    def _log_to_mlflow(
        self,
        model_name: str,
        metrics: Dict,
        fold_metrics: List[Dict],
        model: Any,
    ) -> None:
        """Log experiment to MLflow."""
        try:
            import mlflow
            import mlflow.sklearn

            with mlflow.start_run(experiment_id=self._mlflow_experiment_id):
                # Log parameters
                mlflow.log_param("model_name", model_name)
                mlflow.log_param("horizon_days", self.horizon)
                mlflow.log_param("n_features", len(self._feature_cols))
                mlflow.log_param("feature_version", metrics.get("feature_version", "v1"))
                mlflow.log_param("snapshot_id", self.snapshot_id)
                mlflow.log_param("run_id", self.run_id)
                mlflow.log_param("seed", self.cfg.get("project", {}).get("seed", 42))

                # Log metrics
                for k, v in metrics.items():
                    if isinstance(v, (int, float)) and not np.isnan(v):
                        mlflow.log_metric(k, v)

                # Log feature importance
                if model is not None and hasattr(model, "feature_importances_"):
                    fi = pd.DataFrame({
                        "feature": self._feature_cols,
                        "importance": model.feature_importances_,
                    }).sort_values("importance", ascending=False)

                    fi_path = Path(self.cfg["_project_root"]) / "artifacts" / "feature_importance"
                    fi_path.mkdir(parents=True, exist_ok=True)
                    fi_file = fi_path / f"fi_{model_name}_h{self.horizon}.csv"
                    fi.to_csv(fi_file, index=False)
                    mlflow.log_artifact(str(fi_file))

                # Log fold metrics
                fold_path = Path(self.cfg["_project_root"]) / "artifacts" / "fold_metrics"
                fold_path.mkdir(parents=True, exist_ok=True)
                fold_file = fold_path / f"folds_{model_name}_h{self.horizon}.json"
                with open(fold_file, "w") as f:
                    json.dump(fold_metrics, f, indent=2, default=str)
                mlflow.log_artifact(str(fold_file))

                # Log model
                if model is not None:
                    try:
                        mlflow.sklearn.log_model(model, f"{model_name}_h{self.horizon}")
                    except Exception:
                        pass

        except Exception as e:
            self.logger.warning(f"MLflow logging failed: {e}")

    def persist(self, result: Dict[str, Any]) -> None:
        """Save predictions and models to disk."""
        pred_dir = self.get_path("predictions")
        pred_dir.mkdir(parents=True, exist_ok=True)
        model_dir = Path(self.cfg["_project_root"]) / "artifacts" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)

        for model_name, data in result.items():
            # Save predictions
            preds = data.get("predictions", pd.DataFrame())
            if not preds.empty:
                path = pred_dir / f"predictions_{model_name}_h{self.horizon}d.parquet"
                preds.to_parquet(path, index=False)
                self.output_paths[f"predictions_{model_name}"] = str(path)

            # Save model
            model = data.get("model")
            if model is not None:
                model_path = model_dir / f"{model_name}_h{self.horizon}d.pkl"
                with open(model_path, "wb") as f:
                    pickle.dump(model, f)
                self.output_paths[f"model_{model_name}"] = str(model_path)

            # Save metrics
            metrics = data.get("metrics", {})
            metrics_path = pred_dir / f"metrics_{model_name}_h{self.horizon}d.json"
            with open(metrics_path, "w") as f:
                json.dump(metrics, f, indent=2, default=str)

        self.logger.info(
            f"ModelAgent persisted {len(result)} models for horizon={self.horizon}d"
        )

    def get_feature_importance(self, model_name: str) -> pd.DataFrame:
        """Load feature importance for a model."""
        fi_path = (
            Path(self.cfg["_project_root"])
            / "artifacts"
            / "feature_importance"
            / f"fi_{model_name}_h{self.horizon}.csv"
        )
        if not fi_path.exists():
            return pd.DataFrame()
        return pd.read_csv(fi_path)

    def compute_shap_values(self, model_name: str, X: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Compute SHAP values for tree-based models."""
        try:
            import shap
            model_path = (
                Path(self.cfg["_project_root"])
                / "artifacts"
                / "models"
                / f"{model_name}_h{self.horizon}d.pkl"
            )
            if not model_path.exists():
                return None
            with open(model_path, "rb") as f:
                model = pickle.load(f)

            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X.fillna(0))
            shap_df = pd.DataFrame(shap_values, columns=X.columns)
            return shap_df
        except Exception as e:
            self.logger.warning(f"SHAP computation failed: {e}")
            return None
