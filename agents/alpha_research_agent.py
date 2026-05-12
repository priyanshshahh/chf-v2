from __future__ import annotations

import hashlib
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from scipy.stats import pearsonr, spearmanr

from agents.base import AgentBase
from models.walk_forward import generate_purged_walk_forward_splits


ONCHAIN_HINTS = (
    "onchain",
    "coinmetrics",
    "defillama",
    "missing_",
    "adr_",
    "tx_count",
    "mvrv",
    "nvt",
    "chain_tvl",
    "protocol_tvl",
    "fees_",
    "dex_volume",
    "current_supply",
    "issuance",
    "market_cap_usd",
    "realized_cap",
)
METADATA_COLUMNS = {
    "date_ts",
    "symbol",
    "feature_set",
    "feature_version",
    "snapshot_id",
    "run_id",
    "created_at_utc",
}
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
LABEL_PREFIXES = ("label_", "y_")
RULE_MODELS = {
    "rule_momentum_14d",
    "rule_momentum_30d",
    "rule_vol_adjusted_momentum",
    "rule_reversal_3d",
    "rule_liquidity_momentum",
    "rule_onchain_growth",
    "rule_valuation_onchain",
    "rule_composite_market_onchain",
}


class AlphaResearchAgentError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _cs_zscore(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    std = x.std(ddof=0)
    if not np.isfinite(std) or std <= 0:
        return pd.Series(0.0, index=s.index)
    return (x - x.mean()) / std


def _rank_pct(s: pd.Series, ascending: bool = True) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").rank(method="average", pct=True, ascending=ascending)


def _safe_corr(a: pd.Series, b: pd.Series, method: str) -> Tuple[float, float]:
    valid = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(valid) < 3 or valid["a"].nunique() < 2 or valid["b"].nunique() < 2:
        return np.nan, np.nan
    if method == "spearman":
        c, p = spearmanr(valid["a"], valid["b"])
    else:
        c, p = pearsonr(valid["a"], valid["b"])
    return float(c) if pd.notna(c) else np.nan, float(p) if pd.notna(p) else np.nan


class BaselineCrossSectionalMean:
    def fit(self, X: pd.DataFrame, y: pd.Series, symbols: pd.Series) -> "BaselineCrossSectionalMean":
        frame = pd.DataFrame({"symbol": symbols.values, "y": y.values})
        self.symbol_mean_ = frame.groupby("symbol")["y"].mean().to_dict()
        self.global_mean_ = float(frame["y"].mean()) if len(frame) else 0.0
        return self

    def predict(self, X: pd.DataFrame, symbols: pd.Series) -> np.ndarray:
        return np.array([self.symbol_mean_.get(sym, self.global_mean_) for sym in symbols], dtype=float)


class AlphaResearchAgent(AgentBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._project_root = Path(self.cfg["_project_root"])
        self._cfg = self.cfg.get("alpha_research", {})
        self._output_dir = self._project_root / "data" / "research"
        self._pred_dir = self._project_root / "data" / "predictions"
        self._features = pd.DataFrame()
        self._labels = pd.DataFrame()
        self._panel = pd.DataFrame()
        self._feature_sets: Dict[str, List[str]] = {}
        self._warnings: List[str] = []
        self._skipped: List[Dict[str, Any]] = []

    def prepare(self) -> None:
        cfg = self._cfg = self.cfg.get("alpha_research", {})
        self._output_dir = _resolve(self._project_root, cfg.get("output_dir", "data/research"))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._pred_dir.mkdir(parents=True, exist_ok=True)
        feature_path = _resolve(self._project_root, cfg.get("feature_path", "data/features/full_features.parquet"))
        label_path = _resolve(self._project_root, cfg.get("label_path", "data/labels/label_matrix.parquet"))
        if not feature_path.exists():
            raise FileNotFoundError(f"Missing alpha research feature input: {feature_path}")
        if not label_path.exists():
            raise FileNotFoundError(f"Missing alpha research label input: {label_path}")
        self._features = pd.read_parquet(feature_path)
        self._labels = pd.read_parquet(label_path)
        for df in [self._features, self._labels]:
            df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True, errors="coerce").dt.normalize()
        self._labels = self._enrich_label_matrix(self._labels)
        self._write_enriched_labels()
        self._feature_sets = self._build_feature_sets(self._features)
        self._write_feature_sets()
        self._panel = self._features.merge(self._labels, on=["date_ts", "symbol"], how="inner", suffixes=("", "_label"))
        self._panel = self._panel.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        if self._panel.empty:
            raise AlphaResearchAgentError("Alpha research panel is empty")
        self.logger.info("AlphaResearchAgent prepared | rows=%s symbols=%s", len(self._panel), self._panel["symbol"].nunique())

    def run(self) -> Dict[str, Any]:
        self.generate_snapshot_id("alpha_research")
        cfg = self._cfg
        experiments = self._experiment_grid()
        predictions: List[pd.DataFrame] = []
        fold_rows: List[Dict[str, Any]] = []
        leaderboard_rows: List[Dict[str, Any]] = []
        importance_rows: List[Dict[str, Any]] = []
        max_experiments = int(cfg.get("max_experiments", len(experiments)))
        selected_experiments = self._select_experiments(experiments, max_experiments)
        selected_ids = {exp["experiment_id"] for exp in selected_experiments}
        for exp in experiments:
            if exp["experiment_id"] not in selected_ids:
                skipped = dict(exp)
                skipped["failure_reason"] = "not_selected_by_max_experiments_budget"
                self._skipped.append(skipped)
        for exp in selected_experiments:
            try:
                pred, folds, row, imp = self._run_experiment(exp)
                if pred.empty:
                    raise AlphaResearchAgentError("empty_predictions")
                predictions.append(pred)
                fold_rows.extend(folds)
                leaderboard_rows.append(row)
                importance_rows.extend(imp)
            except Exception as exc:
                skipped = dict(exp)
                skipped["failure_reason"] = str(exc)
                self._skipped.append(skipped)

        pred_df = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
        folds_df = pd.DataFrame(fold_rows)
        leaderboard = pd.DataFrame(leaderboard_rows)
        feature_importance = pd.DataFrame(importance_rows)
        if pred_df.empty and cfg.get("fail_on_empty_results", True):
            raise AlphaResearchAgentError("No alpha research predictions produced")
        leaderboard = self._finalize_leaderboard(leaderboard)
        best_experiments = leaderboard.sort_values(
            ["final_research_score", "mean_rank_ic", "sharpe"],
            ascending=[False, False, False],
            na_position="last",
        ).head(20)
        regime_report = self._build_regime_report(pred_df)
        subperiod_report = self._build_subperiod_report(pred_df)
        compatible_predictions, compatible_leaderboard = self._compatible_model_outputs(pred_df, leaderboard)
        manifest = self._build_manifest(pred_df, folds_df, leaderboard)

        self.metrics["experiments_run"] = int(len(leaderboard))
        self.metrics["experiments_skipped"] = int(len(self._skipped))
        self.metrics["any_final_alpha_passed"] = bool((leaderboard.get("final_alpha_status", pd.Series(dtype=str)) == "passed").any())
        return {
            "predictions": pred_df,
            "fold_metrics": folds_df,
            "leaderboard": leaderboard,
            "best_experiments": best_experiments,
            "regime_report": regime_report,
            "subperiod_report": subperiod_report,
            "feature_importance": feature_importance,
            "compatible_predictions": compatible_predictions,
            "compatible_leaderboard": compatible_leaderboard,
            "manifest": manifest,
            "report_md": self._build_report(leaderboard, best_experiments),
        }

    def _enrich_label_matrix(self, labels: pd.DataFrame) -> pd.DataFrame:
        labels = labels.copy()
        horizons = [7, 14, 30]
        for h in horizons:
            raw = f"label_fwd_logret_{h}d"
            if raw not in labels.columns:
                continue
            labels[f"raw_forward_return_{h}d"] = labels[raw]
            ew = labels.groupby("date_ts")[raw].transform("mean")
            btc = labels.loc[labels["symbol"] == "BTC", ["date_ts", raw]].rename(columns={raw: "_btc_forward"})
            labels = labels.merge(btc, on="date_ts", how="left")
            labels[f"excess_vs_equal_weight_{h}d"] = labels[raw] - ew
            labels[f"excess_vs_btc_{h}d"] = labels[raw] - labels["_btc_forward"]
            vol = labels.groupby("symbol")[raw].transform(lambda s: s.rolling(30, min_periods=5).std()).replace(0, np.nan)
            labels[f"volatility_adjusted_forward_return_{h}d"] = labels[raw] / vol
            labels[f"cross_sectional_forward_rank_{h}d"] = labels.groupby("date_ts")[raw].rank(method="average", pct=True)
            q80 = labels.groupby("date_ts")[raw].transform(lambda s: s.quantile(0.80))
            labels[f"top_quantile_classification_{h}d"] = (labels[raw] >= q80).astype(int)
            labels = labels.drop(columns=["_btc_forward"])
        return labels

    def _write_enriched_labels(self) -> None:
        variants = sorted(c for c in self._labels.columns if c.startswith(("raw_forward_return_", "excess_vs_", "volatility_adjusted_", "cross_sectional_", "top_quantile_")))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._labels[["date_ts", "symbol", *variants]].to_parquet(self._output_dir / "label_variants.parquet", index=False)
        coverage_rows = []
        for col in variants:
            vals = pd.to_numeric(self._labels[col], errors="coerce")
            coverage_rows.append(
                {
                    "horizon_days": int(col.split("_")[-1].replace("d", "")) if col.split("_")[-1].replace("d", "").isdigit() else None,
                    "label_name": col,
                    "non_null_count": int(vals.notna().sum()),
                    "null_count": int(vals.isna().sum()),
                    "infinite_count": int((~np.isfinite(vals.dropna())).sum()) if vals.notna().any() else 0,
                }
            )
        if coverage_rows:
            pd.DataFrame(coverage_rows).to_parquet(self._output_dir / "label_variant_coverage.parquet", index=False)

    def _numeric_feature_candidates(self, features: pd.DataFrame) -> List[str]:
        cols = []
        for col in features.columns:
            lower = col.lower()
            if col in METADATA_COLUMNS or any(lower.startswith(p) for p in LABEL_PREFIXES):
                continue
            if not self._cfg.get("allow_diagnostic_features", False) and col in DIAGNOSTIC_FEATURE_COLUMNS:
                continue
            if any(tok in lower for tok in ["label", "target", "future", "forward", "actual_return"]):
                continue
            if pd.api.types.is_numeric_dtype(features[col]):
                cols.append(col)
        return cols

    def _build_feature_sets(self, features: pd.DataFrame) -> Dict[str, List[str]]:
        candidates = self._numeric_feature_candidates(features)

        def has_any(col: str, words: Iterable[str]) -> bool:
            low = col.lower()
            return any(w in low for w in words)

        onchain = [c for c in candidates if has_any(c, ONCHAIN_HINTS)]
        market = [c for c in candidates if c not in onchain]
        liquidity_momentum = [c for c in candidates if has_any(c, ["momentum", "volume", "dollar_volume", "log_ret_14", "log_ret_30"])]
        valuation = [c for c in candidates if has_any(c, ["mvrv", "nvt", "tvl", "fees", "market_cap_to", "valuation"])]
        low_vol = [c for c in candidates if has_any(c, ["momentum", "log_ret_14", "log_ret_30", "vol", "downside", "beta"])]
        sets = {
            "market_only": sorted(market),
            "market_plus_onchain": sorted(candidates),
            "onchain_only": sorted(onchain),
            "liquidity_momentum": sorted(liquidity_momentum),
            "valuation_onchain": sorted(valuation),
            "low_vol_momentum": sorted(low_vol),
        }
        return sets

    def _write_feature_sets(self) -> None:
        rows = []
        for name, cols in self._feature_sets.items():
            miss = {c: float(self._features[c].isna().mean()) for c in cols[:200]}
            rows.append(
                {
                    "feature_set_name": name,
                    "feature_columns": cols,
                    "feature_count": len(cols),
                    "missingness_summary": miss,
                    "warnings": [] if cols else ["empty_feature_set"],
                }
            )
        out = self._output_dir / "feature_sets.json"
        out.write_text(json.dumps(rows, indent=2, default=str))

    def _experiment_grid(self) -> List[Dict[str, Any]]:
        cfg = self._cfg
        exps = []
        for fs in cfg.get("feature_sets", ["market_only"]):
            for target in cfg.get("label_targets", ["excess_vs_equal_weight"]):
                for h in cfg.get("horizons", [7, 14]):
                    for model in cfg.get("models", ["baseline_cross_sectional_mean"]):
                        exps.append(
                            {
                                "experiment_id": hashlib.sha1(f"{model}|{fs}|{target}|{h}".encode()).hexdigest()[:12],
                                "model_name": model,
                                "feature_set": fs,
                                "label_target": target,
                                "horizon_days": int(h),
                            }
                        )
        return exps

    def _select_experiments(self, experiments: List[Dict[str, Any]], max_experiments: int) -> List[Dict[str, Any]]:
        if max_experiments >= len(experiments):
            return experiments
        # A bounded research pass should cover model families, not spend the
        # whole budget on the first model's feature/label grid.
        model_order = {
            "baseline_cross_sectional_mean": 0,
            "rule_momentum_14d": 1,
            "rule_momentum_30d": 2,
            "rule_vol_adjusted_momentum": 3,
            "rule_liquidity_momentum": 4,
            "rule_composite_market_onchain": 5,
            "linear_ridge": 6,
            "elastic_net": 7,
            "random_forest": 8,
            "lightgbm": 9,
        }
        feature_order = {name: i for i, name in enumerate(self._cfg.get("feature_sets", []))}
        label_order = {name: i for i, name in enumerate(self._cfg.get("label_targets", []))}
        horizon_order = {int(h): i for i, h in enumerate(self._cfg.get("horizons", []))}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for exp in experiments:
            grouped.setdefault(str(exp["model_name"]), []).append(exp)
        for model, rows in grouped.items():
            grouped[model] = sorted(
                rows,
                key=lambda e: (
                    feature_order.get(e["feature_set"], 99)
                    + label_order.get(e["label_target"], 99)
                    + horizon_order.get(int(e["horizon_days"]), 99),
                    feature_order.get(e["feature_set"], 99),
                    label_order.get(e["label_target"], 99),
                    horizon_order.get(int(e["horizon_days"]), 99),
                    e["experiment_id"],
                ),
            )
        model_names = sorted(grouped, key=lambda name: (model_order.get(name, 99), name))
        selected: List[Dict[str, Any]] = []
        offset = 0
        while len(selected) < max_experiments:
            added = False
            for model in model_names:
                rows = grouped[model]
                if offset < len(rows):
                    selected.append(rows[offset])
                    added = True
                    if len(selected) >= max_experiments:
                        break
            if not added:
                break
            offset += 1
        return selected

    def _target_col(self, target: str, horizon: int) -> str:
        if target == "raw_forward_return":
            return f"raw_forward_return_{horizon}d"
        return f"{target}_{horizon}d"

    def _run_experiment(self, exp: Dict[str, Any]) -> Tuple[pd.DataFrame, List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
        horizon = int(exp["horizon_days"])
        target_col = self._target_col(exp["label_target"], horizon)
        actual_col = f"raw_forward_return_{horizon}d"
        if target_col not in self._panel.columns or actual_col not in self._panel.columns:
            raise AlphaResearchAgentError(f"missing_label_{target_col}")
        feature_cols = self._feature_sets.get(exp["feature_set"], [])
        if exp["model_name"] in RULE_MODELS:
            feature_cols = [c for c in feature_cols if c in self._panel.columns]
        else:
            feature_cols = feature_cols[: int(self._cfg.get("max_model_features", 60))]
        if not feature_cols and exp["model_name"] not in RULE_MODELS:
            raise AlphaResearchAgentError("empty_feature_set")
        panel = self._panel.dropna(subset=[target_col, actual_col]).copy()
        panel = panel[np.isfinite(pd.to_numeric(panel[target_col], errors="coerce"))].copy()
        if panel.empty:
            raise AlphaResearchAgentError("empty_panel")
        splits = list(
            generate_purged_walk_forward_splits(
                panel,
                horizon_days=horizon,
                initial_train_days=int(self._cfg.get("train_days", 730)),
                test_days=int(self._cfg.get("test_days", 90)),
                step_days=int(self._cfg.get("step_days", 30)),
                embargo_days=max(int(self._cfg.get("embargo_days", 30)), horizon),
                min_train_rows=int(self._cfg.get("min_train_rows", 1000)),
                min_test_rows=int(self._cfg.get("min_test_rows", 100)),
                min_test_symbols=5,
            )
        )
        minimum_folds = int(self._cfg.get("minimum_folds", 2))
        if len(splits) < minimum_folds:
            raise AlphaResearchAgentError("too_few_valid_folds")
        predictions = []
        fold_rows = []
        importances = []
        for split in splits:
            train = panel.iloc[split.train_idx].copy()
            test = panel.iloc[split.test_idx].copy()
            pred = self._predict_fold(exp, train, test, target_col, feature_cols)
            out = test[["date_ts", "symbol"]].copy()
            out["prediction"] = pred
            out["actual_forward_return"] = pd.to_numeric(test[actual_col], errors="coerce").to_numpy()
            out["target_value"] = pd.to_numeric(test[target_col], errors="coerce").to_numpy()
            out["fold_id"] = split.fold_id
            out["experiment_id"] = exp["experiment_id"]
            out["model_name"] = exp["model_name"]
            out["feature_set"] = exp["feature_set"]
            out["label_target"] = exp["label_target"]
            out["horizon_days"] = horizon
            out["snapshot_id"] = self.snapshot_id
            out["run_id"] = self.run_id
            predictions.append(out)
            fold_rows.append(self._fold_metrics(out, exp, split, len(feature_cols)))
        pred_df = pd.concat(predictions, ignore_index=True)
        leader = self._aggregate_metrics(pred_df, exp, len(feature_cols))
        return pred_df, fold_rows, leader, importances

    def _predict_fold(self, exp: Dict[str, Any], train: pd.DataFrame, test: pd.DataFrame, target_col: str, feature_cols: List[str]) -> np.ndarray:
        model_name = exp["model_name"]
        if model_name in RULE_MODELS:
            return self._rule_scores(model_name, test)
        max_train_rows = int(self._cfg.get("max_train_rows_per_fold", 20000))
        if len(train) > max_train_rows:
            train = train.sort_values(["date_ts", "symbol"]).tail(max_train_rows).copy()
        X_train, X_test = self._preprocess(train, test, feature_cols)
        y_train = pd.to_numeric(train[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid_y = y_train.notna()
        min_train_rows = int(self._cfg.get("min_train_rows", 1000))
        if int(valid_y.sum()) < min_train_rows:
            raise AlphaResearchAgentError(f"too_few_valid_training_labels:{int(valid_y.sum())}")
        X_train = X_train.loc[valid_y].reset_index(drop=True)
        y_train = y_train.loc[valid_y].reset_index(drop=True)
        train_symbols = train.loc[valid_y, "symbol"].reset_index(drop=True)
        if model_name == "baseline_cross_sectional_mean":
            return BaselineCrossSectionalMean().fit(X_train, y_train, train_symbols).predict(X_test, test["symbol"])
        if model_name == "linear_ridge":
            from sklearn.linear_model import Ridge

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return Ridge(alpha=10.0, solver="lsqr").fit(X_train, y_train).predict(X_test)
        if model_name == "elastic_net":
            from sklearn.linear_model import ElasticNet

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=ConvergenceWarning)
                return ElasticNet(alpha=0.01, l1_ratio=0.15, max_iter=300, random_state=42).fit(X_train, y_train).predict(X_test)
        if model_name == "random_forest":
            from sklearn.ensemble import RandomForestRegressor

            return RandomForestRegressor(n_estimators=10, max_depth=4, min_samples_leaf=30, max_features=0.5, n_jobs=-1, random_state=42).fit(X_train, y_train).predict(X_test)
        if model_name == "lightgbm":
            try:
                import lightgbm as lgb

                return lgb.LGBMRegressor(n_estimators=20, learning_rate=0.04, max_depth=3, num_leaves=7, min_child_samples=60, subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, random_state=42, n_jobs=-1, verbose=-1).fit(X_train, y_train).predict(X_test)
            except Exception:
                model_name = "gradient_boosting_sklearn"
        if model_name == "gradient_boosting_sklearn":
            from sklearn.ensemble import GradientBoostingRegressor

            return GradientBoostingRegressor(n_estimators=80, learning_rate=0.04, max_depth=3, random_state=42).fit(X_train, y_train).predict(X_test)
        raise AlphaResearchAgentError(f"unsupported_model_{model_name}")

    def _preprocess(self, train: pd.DataFrame, test: pd.DataFrame, cols: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
        X_train = train[cols].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        X_test = test[cols].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        missing = X_train.isna().mean()
        keep = missing[missing <= 0.95].index.tolist()
        if not keep:
            raise AlphaResearchAgentError("all_features_missing_in_train")
        X_train = X_train[keep]
        X_test = X_test[keep]
        med = X_train.median()
        lo = X_train.quantile(0.01)
        hi = X_train.quantile(0.99)
        X_train = X_train.clip(lo, hi, axis=1).fillna(med).fillna(0.0)
        X_test = X_test.clip(lo, hi, axis=1).fillna(med).fillna(0.0)
        return X_train, X_test

    def _rule_scores(self, model_name: str, test: pd.DataFrame) -> np.ndarray:
        def col(name: str) -> pd.Series:
            return pd.to_numeric(test[name], errors="coerce") if name in test.columns else pd.Series(np.nan, index=test.index)

        def date_rank(values: pd.Series, ascending: bool = True) -> pd.Series:
            return values.groupby(test["date_ts"]).transform(lambda s: _rank_pct(s, ascending=ascending))

        if model_name == "rule_momentum_14d":
            raw = col("log_ret_14d")
        elif model_name == "rule_momentum_30d":
            raw = col("log_ret_30d")
        elif model_name == "rule_vol_adjusted_momentum":
            raw = col("log_ret_30d") / col("realized_vol_30d").replace(0, np.nan)
        elif model_name == "rule_reversal_3d":
            raw = -col("log_ret_3d")
        elif model_name == "rule_liquidity_momentum":
            raw = date_rank(col("log_ret_30d")) + date_rank(col("volume_ratio_30d"))
        elif model_name == "rule_onchain_growth":
            raw = col("adr_active_growth_30d").fillna(0) + col("tx_count_growth_30d").fillna(0) + col("chain_tvl_growth_30d").fillna(0)
        elif model_name == "rule_valuation_onchain":
            raw = -date_rank(col("mvrv_current")) - date_rank(col("nvt_tx_proxy")) + date_rank(col("chain_tvl_usd"))
        elif model_name == "rule_composite_market_onchain":
            raw = (
                0.35 * date_rank(col("log_ret_30d") / col("realized_vol_30d").replace(0, np.nan))
                + 0.20 * date_rank(col("volume_ratio_30d"))
                + 0.25 * date_rank(col("adr_active_growth_30d").fillna(0) + col("tx_count_growth_30d").fillna(0) + col("chain_tvl_growth_30d").fillna(0))
                + 0.20 * date_rank(-col("mvrv_current"))
            )
        else:
            raw = pd.Series(0.0, index=test.index)
        scored = raw.groupby(test["date_ts"]).transform(_cs_zscore)
        return scored.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)

    def _fold_metrics(self, pred: pd.DataFrame, exp: Dict[str, Any], split: Any, n_features: int) -> Dict[str, Any]:
        rank_ic, pval = _safe_corr(pred["prediction"], pred["actual_forward_return"], "spearman")
        pearson_ic, _ = _safe_corr(pred["prediction"], pred["actual_forward_return"], "pearson")
        date_rows = []
        for _, grp in pred.groupby("date_ts"):
            sorted_pred = grp.sort_values("prediction", ascending=False)
            n = max(int(len(sorted_pred) * 0.1), 1)
            top = sorted_pred.head(n)["actual_forward_return"].mean()
            bottom = sorted_pred.tail(n)["actual_forward_return"].mean()
            date_rows.append(
                {
                    "top": top,
                    "bottom": bottom,
                    "spread": top - bottom,
                    "hit": (sorted_pred.head(n)["actual_forward_return"] > sorted_pred["actual_forward_return"].median()).mean(),
                }
            )
        by_date = pd.DataFrame(date_rows)
        return {
            **exp,
            "fold_id": split.fold_id,
            "rank_ic": rank_ic,
            "rank_ic_pvalue": pval,
            "spearman_ic": rank_ic,
            "pearson_ic": pearson_ic,
            "top_decile_mean_return": float(by_date["top"].mean()) if not by_date.empty else np.nan,
            "bottom_decile_mean_return": float(by_date["bottom"].mean()) if not by_date.empty else np.nan,
            "top_bottom_spread": float(by_date["spread"].mean()) if not by_date.empty else np.nan,
            "hit_rate_top_quantile": float(by_date["hit"].mean()) if not by_date.empty else np.nan,
            "directional_accuracy": float((np.sign(pred["prediction"]) == np.sign(pred["actual_forward_return"])).mean()),
            "r2": np.nan,
            "mae": float((pred["prediction"] - pred["actual_forward_return"]).abs().mean()),
            "prediction_coverage": float(pred["prediction"].notna().mean()),
            "n_train_rows": split.train_rows,
            "n_test_rows": split.test_rows,
            "n_symbols": split.test_symbols,
            "test_start": split.test_start,
            "test_end": split.test_end,
            "n_features": n_features,
        }

    def _aggregate_metrics(self, pred: pd.DataFrame, exp: Dict[str, Any], n_features: int) -> Dict[str, Any]:
        per_date = []
        for date, grp in pred.groupby("date_ts"):
            ic, _ = _safe_corr(grp["prediction"], grp["actual_forward_return"], "spearman")
            spread = grp.sort_values("prediction", ascending=False).head(max(int(len(grp) * 0.1), 1))["actual_forward_return"].mean() - grp.sort_values("prediction", ascending=False).tail(max(int(len(grp) * 0.1), 1))["actual_forward_return"].mean()
            per_date.append({"date_ts": date, "rank_ic": ic, "spread": spread})
        m = pd.DataFrame(per_date)
        ic = pd.to_numeric(m["rank_ic"], errors="coerce").dropna()
        spreads = pd.to_numeric(m["spread"], errors="coerce").dropna()
        mean_ic = float(ic.mean()) if len(ic) else np.nan
        std_ic = float(ic.std(ddof=1)) if len(ic) > 1 else np.nan
        tstat = float(mean_ic / (std_ic / np.sqrt(len(ic)))) if len(ic) > 1 and std_ic > 0 else np.nan
        pos = float((ic > 0).mean()) if len(ic) else np.nan
        spread_mean = float(spreads.mean()) if len(spreads) else np.nan
        signal_gate_passed = bool(mean_ic > 0 and tstat > float(self._cfg.get("min_rank_ic_tstat", 1.0)) and spread_mean > 0 and pos >= 0.55 and pred["fold_id"].nunique() >= int(self._cfg.get("minimum_folds", 2)))
        signal_status = "passed_signal_screen" if signal_gate_passed else "failed_signal_screen"
        candidate = signal_gate_passed
        if exp["model_name"] == "baseline_cross_sectional_mean" and not self._cfg.get("allow_baseline_candidate", False):
            candidate = False
        return {
            **exp,
            "mean_rank_ic": mean_ic,
            "median_rank_ic": float(ic.median()) if len(ic) else np.nan,
            "rank_ic_std": std_ic,
            "rank_ic_tstat": tstat,
            "hit_rate": float((pred["actual_forward_return"] > 0).mean()),
            "mean_top_bottom_spread": spread_mean,
            "top_bottom_spread": spread_mean,
            "percent_positive_ic_folds": pos,
            "worst_fold_ic": float(ic.min()) if len(ic) else np.nan,
            "stability_score": float(pos * np.sign(mean_ic)) if np.isfinite(pos) and np.isfinite(mean_ic) else np.nan,
            "n_folds": int(pred["fold_id"].nunique()),
            "n_predictions": int(len(pred)),
            "n_features": int(n_features),
            "alpha_signal_status": signal_status,
            "signal_status": signal_status,
            "signal_gate_passed": signal_gate_passed,
            "candidate_for_backtest": bool(candidate),
            "backtest_source": "not_run",
            "metric_status": "signal_only",
            "alpha_backtest_status": "not_run",
            "sharpe": np.nan,
            "cagr": np.nan,
            "total_return": np.nan,
            "max_drawdown": np.nan,
            "average_turnover": np.nan,
            "beats_btc": False,
            "beats_eth": False,
            "beats_btc_eth_50_50": False,
            "beats_equal_weight": False,
        }

    def _finalize_leaderboard(self, lb: pd.DataFrame) -> pd.DataFrame:
        if lb.empty:
            return lb
        lb = lb.copy()
        lb["final_alpha_status"] = "failed"
        lb["portfolio_strategy"] = "not_run"
        lb["transaction_cost_bps"] = 20
        lb["failure_reason"] = np.where(lb["signal_gate_passed"], "backtest_not_verified", "failed_signal_screen; backtest_not_verified")
        baseline_mask = (lb["model_name"] == "baseline_cross_sectional_mean") & (~self._cfg.get("allow_baseline_candidate", False))
        lb.loc[baseline_mask, "failure_reason"] = lb.loc[baseline_mask, "failure_reason"] + "; baseline_candidate_disabled"
        lb["limitations"] = "signal-only screening; final PortfolioAgent/BacktestAgent required for alpha verification"
        lb["final_research_score"] = lb["mean_rank_ic"].fillna(0) + 0.1 * lb["rank_ic_tstat"].fillna(0) + lb["mean_top_bottom_spread"].fillna(0)
        return lb.sort_values(["final_alpha_status", "final_research_score"], ascending=[False, False]).reset_index(drop=True)

    def _compatible_model_outputs(self, pred: pd.DataFrame, lb: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        if pred.empty or lb.empty:
            return pd.DataFrame(), pd.DataFrame()
        candidates = lb[lb.get("candidate_for_backtest", False) == True].copy()
        if candidates.empty:
            return pd.DataFrame(), pd.DataFrame()
        selected = candidates.sort_values(["signal_gate_passed", "final_research_score"], ascending=[False, False]).iloc[0]
        combo = pred[pred["experiment_id"] == selected["experiment_id"]].copy()
        combo["prediction_rank"] = combo.groupby("date_ts")["prediction"].rank(method="first", ascending=False)
        combo["prediction_rank_pct"] = combo.groupby("date_ts")["prediction"].rank(method="average", pct=True)
        combo["is_top_5"] = combo["prediction_rank"] <= 5
        combo["is_top_10"] = combo["prediction_rank"] <= 10
        combo["is_top_20"] = combo["prediction_rank"] <= 20
        keep = ["date_ts", "symbol", "model_name", "feature_set", "horizon_days", "fold_id", "prediction", "prediction_rank", "prediction_rank_pct", "is_top_5", "is_top_10", "is_top_20", "snapshot_id", "run_id"]
        compat_lb = lb.rename(columns={"mean_rank_ic": "rank_ic_mean", "rank_ic_tstat": "rank_ic_tstat", "mean_top_bottom_spread": "top_bottom_10_spread", "n_predictions": "prediction_rows", "n_folds": "fold_count"}).copy()
        compat_lb["selected_for_backtest"] = compat_lb["experiment_id"] == selected["experiment_id"]
        compat_lb["alpha_status"] = compat_lb["signal_status"]
        return combo[keep], compat_lb

    def _build_regime_report(self, pred: pd.DataFrame) -> pd.DataFrame:
        if pred.empty:
            return pd.DataFrame()
        rows = []
        for exp, grp in pred.groupby("experiment_id"):
            rows.append({"experiment_id": exp, "regime": "all", "mean_actual_return_top_decile": grp.sort_values("prediction", ascending=False).head(max(int(len(grp) * 0.1), 1))["actual_forward_return"].mean(), "rows": len(grp)})
        return pd.DataFrame(rows)

    def _build_subperiod_report(self, pred: pd.DataFrame) -> pd.DataFrame:
        if pred.empty:
            return pd.DataFrame()
        tmp = pred.copy()
        tmp["year"] = pd.to_datetime(tmp["date_ts"], utc=True).dt.year
        rows = []
        for (exp, year), grp in tmp.groupby(["experiment_id", "year"]):
            ic, _ = _safe_corr(grp["prediction"], grp["actual_forward_return"], "spearman")
            rows.append({"experiment_id": exp, "subperiod": str(year), "rank_ic": ic, "rows": len(grp)})
        return pd.DataFrame(rows)

    def _build_manifest(self, pred: pd.DataFrame, folds: pd.DataFrame, lb: pd.DataFrame) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": _utcnow_iso(),
            "experiments_run": int(len(lb)),
            "experiments_skipped": int(len(self._skipped)),
            "skipped_experiments": self._skipped,
            "prediction_rows": int(len(pred)),
            "fold_metric_rows": int(len(folds)),
            "warnings": self._warnings,
            "limitations": [
                "Current production universe is latest-survivor baseline.",
                "Historical CMC point-in-time universe is not yet the production path.",
                "AlphaResearchAgent is signal-only; final PortfolioAgent and BacktestAgent verification is required before any alpha claim.",
            ],
            "canonical_outputs_mutated": False,
            "export_candidate_to_predictions": bool(self._cfg.get("export_candidate_to_predictions", False)),
            "signal_only": bool(self._cfg.get("signal_only", True)),
        }

    def _build_report(self, lb: pd.DataFrame, best: pd.DataFrame) -> str:
        passed = int((lb.get("final_alpha_status", pd.Series(dtype=str)) == "passed").sum()) if not lb.empty else 0
        lines = ["# Alpha Research Report", "", f"- Experiments run: {len(lb)}", f"- Experiments skipped: {len(self._skipped)}", f"- Final alpha passed: {passed}", ""]
        if passed == 0:
            lines.append("No robust alpha found under tested configurations.")
        if not best.empty:
            lines.extend(["", "## Best Experiments", "", best.head(20).to_markdown(index=False)])
        return "\n".join(lines) + "\n"

    def persist(self, result: Dict[str, Any]) -> None:
        self._validate_before_persist(result)
        out = self._output_dir
        out.mkdir(parents=True, exist_ok=True)
        result["predictions"].to_parquet(self._pred_dir / "alpha_research_predictions.parquet", index=False)
        result["fold_metrics"].to_parquet(self._pred_dir / "alpha_fold_metrics.parquet", index=False)
        result["leaderboard"].to_parquet(self._pred_dir / "alpha_model_leaderboard.parquet", index=False)
        result["feature_importance"].to_parquet(self._pred_dir / "alpha_feature_importance.parquet", index=False)
        (self._pred_dir / "data_quality_alpha_research.md").write_text(result["report_md"])
        if self._cfg.get("export_candidate_to_predictions", False) and not result["compatible_predictions"].empty:
            result["compatible_predictions"].to_parquet(self._pred_dir / "model_predictions.parquet", index=False)
            result["compatible_leaderboard"].to_parquet(self._pred_dir / "model_leaderboard.parquet", index=False)
        result["leaderboard"].to_parquet(out / "research_leaderboard.parquet", index=False)
        result["best_experiments"].to_parquet(out / "best_experiments.parquet", index=False)
        result["regime_report"].to_parquet(out / "regime_report.parquet", index=False)
        result["subperiod_report"].to_parquet(out / "subperiod_report.parquet", index=False)
        with open(out / "research_manifest.json", "w") as fh:
            json.dump(result["manifest"], fh, indent=2, default=str)
        (out / "alpha_research_report.md").write_text(result["report_md"])
        with open(self._pred_dir / "alpha_research_manifest.json", "w") as fh:
            json.dump(result["manifest"], fh, indent=2, default=str)
        self.output_paths = {
            "research_leaderboard": str(out / "research_leaderboard.parquet"),
            "alpha_research_predictions": str(self._pred_dir / "alpha_research_predictions.parquet"),
            "alpha_model_leaderboard": str(self._pred_dir / "alpha_model_leaderboard.parquet"),
            "alpha_feature_importance": str(self._pred_dir / "alpha_feature_importance.parquet"),
        }

    def _validate_before_persist(self, result: Dict[str, Any]) -> None:
        lb = result.get("leaderboard", pd.DataFrame())
        if not lb.empty:
            if (lb["final_alpha_status"] == "passed").any():
                bad = lb[(lb["final_alpha_status"] == "passed") & ~((lb["backtest_source"] == "backtest_agent") & (lb["metric_status"] == "backtest_verified"))]
                if not bad.empty:
                    raise AlphaResearchAgentError("AlphaResearchAgent cannot mark final_alpha_status passed without BacktestAgent verification")
            signal_only = lb["metric_status"] == "signal_only"
            for col in ["sharpe", "cagr", "total_return", "max_drawdown"]:
                if col in lb.columns and lb.loc[signal_only, col].notna().any():
                    raise AlphaResearchAgentError(f"signal_only rows must not contain {col}")
        compat = result.get("compatible_predictions", pd.DataFrame())
        forbidden = ("actual", "label", "future", "realized", "target", "y_")
        bad_cols = [c for c in compat.columns if any(tok in c.lower() for tok in forbidden)]
        if bad_cols:
            raise AlphaResearchAgentError(f"portfolio-compatible export contains forbidden columns: {bad_cols}")
