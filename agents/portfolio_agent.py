from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from agents.base import AgentBase


FORBIDDEN_INPUT_TERMS = (
    "actual",
    "actual_return",
    "actual_forward_return",
    "actual_rank",
    "label",
    "future",
    "future_return",
    "realized",
    "realized_return",
    "target",
    "y_",
)

DEFAULT_METADATA_COLUMNS = {
    "date_ts",
    "signal_date",
    "execution_date",
    "symbol",
    "cmc_id",
    "model_name",
    "horizon_days",
    "feature_set",
    "fold_id",
    "snapshot_id",
    "run_id",
    "created_at_utc",
}


class PortfolioAgentError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _period_group_dates(dates: pd.Series, frequency: str) -> List[pd.Timestamp]:
    dates = pd.Series(sorted(pd.to_datetime(dates, utc=True).dropna().unique()))
    if dates.empty:
        return []
    naive_dates = dates.dt.tz_localize(None)
    if frequency == "W":
        return dates.groupby(naive_dates.dt.to_period("W")).min().tolist()
    if frequency == "2W":
        grp = ((dates - dates.min()).dt.days // 14)
        return dates.groupby(grp).min().tolist()
    if frequency == "M":
        return dates.groupby(naive_dates.dt.to_period("M")).min().tolist()
    return dates.tolist()


def normalize_with_cap(raw_weights: pd.Series, max_weight: float, target_sum: float = 1.0) -> Tuple[pd.Series, float]:
    raw = pd.to_numeric(raw_weights, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    raw = raw.clip(lower=0.0)
    if raw.empty or float(raw.sum()) <= 0:
        return pd.Series(0.0, index=raw.index, dtype=float), float(target_sum)
    max_possible = min(float(target_sum), float(max_weight) * len(raw))
    if max_possible <= 0:
        return pd.Series(0.0, index=raw.index, dtype=float), float(target_sum)
    weights = raw / float(raw.sum()) * max_possible
    capped = pd.Series(False, index=weights.index)
    while True:
        over = (weights > max_weight + 1e-12) & ~capped
        if not over.any():
            break
        weights.loc[over] = max_weight
        capped.loc[over] = True
        remaining_target = max_possible - float(weights.loc[capped].sum())
        if remaining_target <= 1e-12:
            weights.loc[~capped] = 0.0
            break
        uncapped = ~capped
        uncapped_raw = raw.loc[uncapped]
        if float(uncapped_raw.sum()) <= 0:
            weights.loc[uncapped] = 0.0
            break
        weights.loc[uncapped] = uncapped_raw / float(uncapped_raw.sum()) * remaining_target
    cash_weight = max(float(target_sum) - float(weights.sum()), 0.0)
    return weights.astype(float), float(cash_weight)


class PortfolioAgent(AgentBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None, model_name: Optional[str] = None, horizon: Optional[int] = None):
        super().__init__(config)
        self._project_root = Path(self.cfg["_project_root"])
        self._pcfg = self.cfg.get("portfolio", {})
        self.override_model = model_name
        self.override_horizon = horizon
        self._predictions: pd.DataFrame = pd.DataFrame()
        self._leaderboard: pd.DataFrame = pd.DataFrame()
        self._model_manifest: Dict[str, Any] = {}
        self._market: pd.DataFrame = pd.DataFrame()
        self._close_matrix: pd.DataFrame = pd.DataFrame()
        self._vol_matrix: pd.DataFrame = pd.DataFrame()
        self._cmc_map: pd.DataFrame = pd.DataFrame()
        self._selected_combo: Dict[str, Any] = {}
        self._alpha_gate_passed = False
        self._allocation_mode = "diagnostic_not_live_trading"
        self._warnings: List[str] = []
        self._output_dir: Path = self._project_root / "data" / "allocations"
        self._prediction_col = "predicted_return"

    def _validate_prediction_input_columns(self) -> None:
        if self._pcfg.get("allow_realized_columns_in_predictions_for_diagnostics", False):
            return
        bad = [col for col in self._predictions.columns if any(term in col.lower() for term in FORBIDDEN_INPUT_TERMS)]
        if bad:
            raise PortfolioAgentError(f"Prediction input contains forbidden realized/label/target columns: {bad}")

    def prepare(self) -> None:
        self._pcfg = self.cfg.get("portfolio", {})
        self._output_dir = _resolve(self._project_root, self._pcfg.get("output_dir", "data/allocations"))
        self._output_dir.mkdir(parents=True, exist_ok=True)

        pred_path = _resolve(
            self._project_root,
            self._pcfg.get("prediction_path", self._pcfg.get("predictions_path", "data/predictions/model_predictions.parquet")),
        )
        leader_path = _resolve(self._project_root, self._pcfg.get("leaderboard_path", "data/predictions/model_leaderboard.parquet"))
        market_path = _resolve(self._project_root, self._pcfg.get("market_path", "data/raw/market/market_ohlcv.parquet"))
        manifest_path = self._project_root / "data" / "predictions" / "model_manifest.json"
        for required in [pred_path, market_path]:
            if not required.exists():
                raise FileNotFoundError(f"Required portfolio input missing: {required}")
        self._predictions = pd.read_parquet(pred_path)
        if self._predictions.empty:
            raise PortfolioAgentError("Prediction file is empty")
        self._validate_prediction_input_columns()
        self._predictions["date_ts"] = pd.to_datetime(self._predictions["date_ts"], utc=True).dt.normalize()
        self._prediction_col = "predicted_return" if "predicted_return" in self._predictions.columns else "prediction"
        if self._prediction_col not in self._predictions.columns:
            raise PortfolioAgentError("Prediction file missing predicted_return/prediction column")
        self._market = pd.read_parquet(market_path)
        self._market["date_ts"] = pd.to_datetime(self._market["date_ts"], utc=True).dt.normalize()
        if "close" in self._market.columns and (pd.to_numeric(self._market["close"], errors="coerce") <= 0).any():
            raise PortfolioAgentError("Market input contains non-positive close prices")
        self._close_matrix = (
            self._market.pivot_table(index="date_ts", columns="symbol", values="close", aggfunc="last")
            .sort_index()
        )
        returns = self._close_matrix.pct_change(fill_method=None)
        lookback = int(self._pcfg.get("volatility_lookback_days", 30))
        min_periods = max(5, min(lookback, 10))
        self._vol_matrix = returns.rolling(lookback, min_periods=min_periods).std()
        if "cmc_id" in self._market.columns:
            cmc_map = (
                self._market.dropna(subset=["cmc_id"])[["symbol", "cmc_id"]]
                .drop_duplicates(subset=["symbol"], keep="last")
                .copy()
            )
            self._cmc_map = cmc_map.set_index("symbol")
        if leader_path.exists():
            self._leaderboard = pd.read_parquet(leader_path)
        if manifest_path.exists():
            with open(manifest_path, "r") as fh:
                self._model_manifest = json.load(fh)
        self._selected_combo = self._select_combo()
        self.logger.info("PortfolioAgent prepared | combo=%s mode=%s", self._selected_combo, self._allocation_mode)

    def _select_combo(self) -> Dict[str, Any]:
        horizon = int(self.override_horizon or self._pcfg.get("horizon_days", 14))
        if self.override_model:
            self._warnings.append("Portfolio override model supplied; bypassing leaderboard selection.")
            self._alpha_gate_passed = False
            self._allocation_mode = "override_diagnostic"
            return {
                "model_name": self.override_model,
                "feature_set": self._pcfg.get("fallback_feature_set"),
                "horizon_days": horizon,
            }

        if self._pcfg.get("model_selection", "best_available") == "best_available" and not self._leaderboard.empty:
            board = self._leaderboard.copy()
            if "horizon_days" in board.columns:
                board = board[board["horizon_days"] == horizon].copy()
            if not board.empty:
                signal_gate = board.get("signal_gate_passed", pd.Series(False, index=board.index)).fillna(False).astype(bool)
                candidate = board.get("candidate_for_backtest", pd.Series(False, index=board.index)).fillna(False).astype(bool)
                legacy_selected = board.get("selected_for_backtest", pd.Series(False, index=board.index)).fillna(False).astype(bool)
                if legacy_selected.any() and "signal_gate_passed" not in board.columns:
                    self._warnings.append("Legacy selected_for_backtest found; treating it as diagnostic selection only.")
                alpha_flag = signal_gate & candidate
                board["_alpha_gate"] = alpha_flag.astype(int)
                for col in ["rank_ic_mean", "rank_ic_tstat", "top_bottom_10_spread", "prediction_rows", "fold_count"]:
                    if col not in board.columns:
                        board[col] = np.nan
                board = board.sort_values(
                    ["_alpha_gate", "rank_ic_mean", "rank_ic_tstat", "top_bottom_10_spread", "prediction_rows", "fold_count"],
                    ascending=[False, False, False, False, False, False],
                    na_position="last",
                )
                row = board.iloc[0]
                self._alpha_gate_passed = bool(row["_alpha_gate"])
                if self._alpha_gate_passed:
                    self._allocation_mode = "signal_candidate_for_backtest"
                else:
                    self._allocation_mode = "diagnostic_not_live_trading"
                    self._warnings.append("No model passed alpha gate; allocations are diagnostic research outputs only.")
                return {
                    "model_name": row["model_name"],
                    "feature_set": row.get("feature_set", self._pcfg.get("fallback_feature_set")),
                    "horizon_days": int(row.get("horizon_days", horizon)),
                }

        self._allocation_mode = "leaderboard_missing_diagnostic" if self._leaderboard.empty else "diagnostic_not_live_trading"
        self._alpha_gate_passed = False
        self._warnings.append("No eligible leaderboard selection found; using configured fallback.")
        available = self._predictions.copy()
        if "horizon_days" in available.columns:
            available = available[available["horizon_days"].astype(int) == horizon].copy()
        if not available.empty:
            combo = (
                available[["model_name", "feature_set", "horizon_days"]]
                .drop_duplicates()
                .sort_values(["model_name", "feature_set", "horizon_days"])
                .iloc[0]
            )
            return {
                "model_name": combo["model_name"],
                "feature_set": combo.get("feature_set"),
                "horizon_days": int(combo.get("horizon_days", horizon)),
            }
        return {
            "model_name": self._pcfg.get("fallback_model", "baseline_cross_sectional_mean"),
            "feature_set": self._pcfg.get("fallback_feature_set"),
            "horizon_days": horizon,
        }

    def run(self) -> Dict[str, Any]:
        self.generate_snapshot_id("portfolio_research")
        preds = self._filtered_predictions()
        if preds.empty:
            raise PortfolioAgentError("Selected model/horizon/feature set has no usable predictions")

        strategy_names = list(self._pcfg.get("strategy_names", []))
        if not strategy_names:
            raise PortfolioAgentError("No portfolio strategies configured")

        all_allocations: List[pd.DataFrame] = []
        all_coverages: List[pd.DataFrame] = []
        per_strategy_paths: Dict[str, str] = {}
        strategy_summaries: Dict[str, Dict[str, float]] = {}

        for strategy in strategy_names:
            strategy_alloc, strategy_cov = self._build_strategy(preds, strategy)
            if not strategy_cov.empty:
                all_coverages.append(strategy_cov)
            if not strategy_alloc.empty:
                all_allocations.append(strategy_alloc)

        allocations = (
            pd.concat(all_allocations, ignore_index=True)
            .sort_values(["date_ts", "strategy_name", "prediction_rank", "symbol"])
            .reset_index(drop=True)
            if all_allocations
            else pd.DataFrame()
        )
        coverage = (
            pd.concat(all_coverages, ignore_index=True)
            .sort_values(["execution_date", "strategy_name"])
            .reset_index(drop=True)
            if all_coverages
            else pd.DataFrame()
        )

        if not allocations.empty:
            per_strategy_paths = {
                str(strategy_name): str(self._output_dir / f"allocations_{strategy_name}.parquet")
                for strategy_name in sorted(allocations["strategy_name"].drop_duplicates().tolist())
            }
        if not coverage.empty:
            for strategy_name, strategy_cov in coverage.groupby("strategy_name"):
                strategy_summaries[strategy_name] = {
                    "average_turnover": float(strategy_cov["turnover"].mean()),
                    "average_cash_weight": float(strategy_cov["cash_weight"].mean()),
                }

        if allocations.empty and self._pcfg.get("fail_on_empty_allocations", True):
            raise PortfolioAgentError("PortfolioAgent produced empty allocations")

        manifest = self._build_manifest(allocations, coverage, per_strategy_paths)
        self.metrics["allocation_rows"] = int(len(allocations))
        self.metrics["rebalance_count"] = int(coverage["execution_date"].nunique()) if not coverage.empty else 0
        self.metrics["unique_symbols_allocated"] = int(allocations["symbol"].nunique()) if not allocations.empty else 0
        self.metrics["alpha_gate_passed"] = self._alpha_gate_passed
        self.metrics["signal_gate_passed"] = self._alpha_gate_passed
        self.metrics["candidate_for_backtest"] = self._alpha_gate_passed
        self.metrics["allocation_mode"] = self._allocation_mode
        self.metrics["strategy_summaries"] = strategy_summaries
        return {
            "allocations": allocations,
            "coverage": coverage,
            "manifest": manifest,
            "quality_md": self._build_quality_md(allocations, coverage),
            "strategy_paths": per_strategy_paths,
        }

    def _filtered_predictions(self) -> pd.DataFrame:
        preds = self._predictions.copy()
        preds = preds[
            (preds["model_name"] == self._selected_combo["model_name"])
            & (preds["horizon_days"].astype(int) == int(self._selected_combo["horizon_days"]))
        ].copy()
        if "feature_set" in preds.columns and self._selected_combo.get("feature_set") is not None:
            preds = preds[preds["feature_set"] == self._selected_combo["feature_set"]].copy()
        max_symbols = self._pcfg.get("max_symbols")
        if max_symbols:
            keep = sorted(preds["symbol"].astype(str).drop_duplicates())[: int(max_symbols)]
            preds = preds[preds["symbol"].isin(keep)].copy()
        preds[self._prediction_col] = pd.to_numeric(preds[self._prediction_col], errors="coerce")
        preds = preds[np.isfinite(preds[self._prediction_col])].copy()
        if preds.duplicated(["date_ts", "symbol"]).any():
            raise PortfolioAgentError("Filtered predictions contain duplicate date_ts + symbol rows")
        if preds[self._prediction_col].isna().any() or (~np.isfinite(preds[self._prediction_col])).any():
            raise PortfolioAgentError("Filtered predictions contain non-finite prediction values")
        return preds

    def _build_strategy(self, preds: pd.DataFrame, strategy: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        top_k_values = [int(k) for k in self._pcfg.get("top_k_values", [5, 10, 20])]
        if strategy == "top_k_equal_weight":
            frames, covs = [], []
            for k in top_k_values:
                alloc, cov = self._build_rebalances(preds, f"top_{k}_equal_weight", strategy_type="top_equal", top_k=k)
                frames.append(alloc)
                covs.append(cov)
            return self._concat_frames(frames), self._concat_frames(covs)
        if strategy == "top_k_vol_scaled":
            frames, covs = [], []
            for k in top_k_values:
                alloc, cov = self._build_rebalances(preds, f"top_{k}_vol_scaled", strategy_type="top_vol", top_k=k)
                frames.append(alloc)
                covs.append(cov)
            return self._concat_frames(frames), self._concat_frames(covs)
        if strategy == "score_weighted_long_only":
            return self._build_rebalances(preds, "score_weighted_long_only", strategy_type="score")
        if strategy == "score_weighted_vol_scaled":
            return self._build_rebalances(preds, "score_weighted_vol_scaled", strategy_type="score_vol")
        if strategy == "turnover_controlled":
            return self._build_rebalances(preds, "turnover_controlled", strategy_type="turnover")
        raise PortfolioAgentError(f"Unsupported strategy: {strategy}")

    @staticmethod
    def _concat_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
        frames = [frame for frame in frames if frame is not None and not frame.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _build_rebalances(
        self,
        preds: pd.DataFrame,
        strategy_name: str,
        *,
        strategy_type: str,
        top_k: Optional[int] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        rows: List[Dict[str, Any]] = []
        coverage_rows: List[Dict[str, Any]] = []
        previous_weights: Dict[str, float] = {}
        frequency = self._pcfg.get("rebalance_frequency", "W")
        for signal_date in _period_group_dates(preds["date_ts"], frequency):
            signal_ts = pd.Timestamp(signal_date)
            day = preds[preds["date_ts"] == signal_ts].copy()
            day, dropped_missing_prediction_count = self._clean_day_predictions(day)
            execution_date = self._next_market_date(signal_ts, int(self._pcfg.get("execution_lag_days", 1)))
            if execution_date is None:
                coverage_rows.append(self._coverage_row(signal_ts, None, strategy_name, day, 0, 0, 0, 0, 0, 0, 0, False, "no_next_market_date"))
                continue
            day = self._attach_signal_statistics(day)
            day = self._attach_execution_price(day, execution_date)
            dropped_missing_price_count = int(day["_missing_price"].sum()) if "_missing_price" in day.columns else 0
            day = day[~day["_missing_price"]].copy()
            day = self._attach_risk(day, signal_ts)
            dropped_missing_risk_count = int(day["_missing_risk"].sum()) if "_missing_risk" in day.columns else 0
            candidate_count = len(day)
            if candidate_count < int(self._pcfg.get("min_assets_per_rebalance", 5)):
                coverage_rows.append(
                    self._coverage_row(
                        signal_ts,
                        execution_date,
                        strategy_name,
                        day,
                        candidate_count,
                        0,
                        dropped_missing_prediction_count,
                        dropped_missing_price_count,
                        dropped_missing_risk_count,
                        0,
                        0,
                        False,
                        "below_min_assets_per_rebalance",
                    )
                )
                previous_weights = {}
                continue
            final = self._strategy_weights(day, strategy_type, previous_weights, top_k=top_k)
            if final.empty:
                coverage_rows.append(
                    self._coverage_row(
                        signal_ts,
                        execution_date,
                        strategy_name,
                        day,
                        candidate_count,
                        0,
                        dropped_missing_prediction_count,
                        dropped_missing_price_count,
                        dropped_missing_risk_count,
                        0,
                        0,
                        False,
                        "no_selected_assets",
                    )
                )
                previous_weights = {}
                continue
            weight_sum = float(final["weight"].sum())
            gross_exposure = float(final["weight"].abs().sum())
            net_exposure = float(final["weight"].sum())
            cash_weight = max(float(self._pcfg.get("target_gross_exposure", 1.0)) - weight_sum, 0.0)
            selected_count = len(final)
            turnover = float(final["turnover_contribution"].sum() + sum(abs(previous_weights.get(sym, 0.0)) for sym in set(previous_weights) - set(final["symbol"])))
            passed = True
            reason = ""
            if bool(self._pcfg.get("fail_on_weight_sum_error", True)) and weight_sum > float(self._pcfg.get("target_gross_exposure", 1.0)) + 1e-6:
                passed = False
                reason = "weight_sum_above_target"
            if bool(self._pcfg.get("fail_on_lookahead", True)) and execution_date <= signal_ts:
                passed = False
                reason = reason or "lookahead_execution"
            coverage_rows.append(
                self._coverage_row(
                    signal_ts,
                    execution_date,
                    strategy_name,
                    day,
                    candidate_count,
                    selected_count,
                    dropped_missing_prediction_count,
                    dropped_missing_price_count,
                    dropped_missing_risk_count,
                    cash_weight,
                    turnover,
                    passed,
                    reason,
                    gross_exposure=gross_exposure,
                    net_exposure=net_exposure,
                    weight_sum=weight_sum,
                    max_weight_actual=float(final["weight"].max()),
                )
            )
            for _, row in final.iterrows():
                rows.append(
                    {
                        "date_ts": execution_date,
                        "signal_date": signal_ts,
                        "execution_date": execution_date,
                        "symbol": row["symbol"],
                        "cmc_id": row.get("cmc_id"),
                        "model_name": self._selected_combo["model_name"],
                        "horizon_days": int(self._selected_combo["horizon_days"]),
                        "feature_set": self._selected_combo.get("feature_set"),
                        "predicted_return": float(row[self._prediction_col]),
                        "prediction_rank": float(row["prediction_rank"]),
                        "prediction_zscore": float(row["prediction_zscore"]),
                        "signal_score": float(row["signal_score"]),
                        "side": "long",
                        "raw_weight": float(row["raw_weight"]),
                        "target_weight": float(row["target_weight"]),
                        "weight": float(row["weight"]),
                        "previous_weight": float(row["previous_weight"]),
                        "turnover_contribution": float(row["turnover_contribution"]),
                        "risk_estimate": float(row["risk_estimate"]),
                        "rebalance_frequency": frequency,
                        "strategy_name": strategy_name,
                        "alpha_gate_passed": bool(self._alpha_gate_passed),
                        "signal_gate_passed": bool(self._alpha_gate_passed),
                        "candidate_for_backtest": bool(self._alpha_gate_passed),
                        "alpha_verified": False,
                        "allocation_mode": self._allocation_mode,
                        "snapshot_id": self.snapshot_id,
                        "run_id": self.run_id,
                        "created_at_utc": _utcnow_iso(),
                    }
                )
            previous_weights = dict(zip(final["symbol"], final["weight"]))
        return pd.DataFrame(rows), pd.DataFrame(coverage_rows)

    def _clean_day_predictions(self, day: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
        day = day.copy()
        pred = pd.to_numeric(day[self._prediction_col], errors="coerce")
        invalid = pred.isna() | (~np.isfinite(pred))
        dropped = int(invalid.sum())
        day = day[~invalid].copy()
        day = day.sort_values([self._prediction_col, "symbol"], ascending=[False, True]).reset_index(drop=True)
        return day, dropped

    def _attach_signal_statistics(self, day: pd.DataFrame) -> pd.DataFrame:
        day = day.copy()
        pred = pd.to_numeric(day[self._prediction_col], errors="coerce")
        std = float(pred.std(ddof=0)) if len(pred) > 1 else 0.0
        mean = float(pred.mean()) if len(pred) else 0.0
        if std > 0:
            day["prediction_zscore"] = (pred - mean) / std
        else:
            day["prediction_zscore"] = 0.0
        day["prediction_rank"] = pred.rank(method="first", ascending=False)
        day["signal_score"] = day["prediction_zscore"]
        return day

    def _attach_execution_price(self, day: pd.DataFrame, execution_date: pd.Timestamp) -> pd.DataFrame:
        day = day.copy()
        if execution_date not in self._close_matrix.index:
            day["_missing_price"] = True
            return day
        price_row = self._close_matrix.loc[execution_date]
        prices = day["symbol"].map(price_row.to_dict())
        day["_execution_close"] = pd.to_numeric(prices, errors="coerce")
        day["_missing_price"] = day["_execution_close"].isna() | (day["_execution_close"] <= 0)
        return day

    def _attach_risk(self, day: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
        day = day.copy()
        risk_row = self._vol_matrix.loc[signal_date] if signal_date in self._vol_matrix.index else pd.Series(dtype=float)
        risks = pd.to_numeric(day["symbol"].map(risk_row.to_dict()), errors="coerce")
        median_risk = float(risks.dropna().median()) if risks.dropna().size else np.nan
        risks = risks.where(risks > 0, np.nan)
        if np.isfinite(median_risk) and median_risk > 0:
            risks = risks.fillna(median_risk)
        day["risk_estimate"] = risks
        day["_missing_risk"] = day["risk_estimate"].isna() | ~np.isfinite(day["risk_estimate"]) | (day["risk_estimate"] <= 0)
        return day

    def _strategy_weights(
        self,
        day: pd.DataFrame,
        strategy_type: str,
        previous_weights: Dict[str, float],
        *,
        top_k: Optional[int] = None,
    ) -> pd.DataFrame:
        cfg = self._pcfg
        max_assets = int(cfg.get("max_assets_per_rebalance", 25))
        min_assets = int(cfg.get("min_assets_per_rebalance", 5))
        max_weight = float(cfg.get("max_weight", cfg.get("max_position_weight", 0.15)))
        target_sum = float(cfg.get("target_gross_exposure", 1.0))
        min_signal_z = float(cfg.get("min_signal_zscore", 0.0))

        base = day.copy()
        if strategy_type in {"top_vol", "score_vol", "turnover"}:
            base = base[~base["_missing_risk"]].copy()
        if strategy_type == "top_equal":
            assert top_k is not None
            selected = self._extend_for_cap(base.sort_values([self._prediction_col, "symbol"], ascending=[False, True]), top_k, max_assets, max_weight, target_sum)
            raw = pd.Series(1.0, index=selected.index)
        elif strategy_type == "top_vol":
            assert top_k is not None
            selected = self._extend_for_cap(base.sort_values([self._prediction_col, "symbol"], ascending=[False, True]), top_k, max_assets, max_weight, target_sum)
            raw = 1.0 / pd.to_numeric(selected["risk_estimate"], errors="coerce")
        elif strategy_type == "score":
            selected = base[base["prediction_zscore"] > min_signal_z].sort_values(["prediction_zscore", "symbol"], ascending=[False, True]).head(max_assets).copy()
            raw = selected["prediction_zscore"].clip(lower=0.0)
        elif strategy_type in {"score_vol", "turnover"}:
            selected = base[base["prediction_zscore"] > min_signal_z].sort_values(["prediction_zscore", "symbol"], ascending=[False, True]).head(max_assets).copy()
            raw = selected["prediction_zscore"].clip(lower=0.0) / pd.to_numeric(selected["risk_estimate"], errors="coerce")
        else:
            raise PortfolioAgentError(f"Unsupported strategy type {strategy_type}")

        if selected.empty or len(selected) < min_assets:
            return pd.DataFrame()
        weights, _cash = normalize_with_cap(raw, max_weight=max_weight, target_sum=target_sum)
        selected["raw_weight"] = pd.to_numeric(raw, errors="coerce").fillna(0.0).values
        selected["target_weight"] = weights.values
        selected["previous_weight"] = selected["symbol"].map(previous_weights).fillna(0.0)
        selected["weight"] = selected["target_weight"]

        if strategy_type == "turnover":
            buffer = float(cfg.get("turnover_buffer", 0.02))
            max_turnover = float(cfg.get("max_turnover_per_rebalance", 0.50))
            symbol_order = selected["symbol"].tolist()
            merged_symbols = sorted(set(symbol_order) | set(previous_weights))
            current_target = pd.Series(0.0, index=merged_symbols)
            current_target.loc[selected["symbol"]] = selected["target_weight"].values
            prev_series = pd.Series(previous_weights, index=merged_symbols, dtype=float).fillna(0.0)
            final_weights = current_target.copy()
            freeze = (current_target - prev_series).abs() < buffer
            final_weights.loc[freeze] = prev_series.loc[freeze]
            pos = final_weights[final_weights > 0]
            if not pos.empty:
                renorm, _ = normalize_with_cap(pos, max_weight=max_weight, target_sum=target_sum)
                final_weights.loc[pos.index] = renorm
            turnover = float((final_weights - prev_series).abs().sum())
            if turnover > max_turnover and turnover > 0:
                scale = max_turnover / turnover
                final_weights = prev_series + (final_weights - prev_series) * scale
                pos = final_weights[final_weights > 0]
                if not pos.empty:
                    renorm, _ = normalize_with_cap(pos, max_weight=max_weight, target_sum=min(target_sum, float(pos.sum())))
                    final_weights.loc[pos.index] = renorm
            selected["weight"] = selected["symbol"].map(final_weights).fillna(0.0)

        selected["turnover_contribution"] = (selected["weight"] - selected["previous_weight"]).abs()
        selected["cmc_id"] = selected["symbol"].map(self._cmc_map["cmc_id"].to_dict()) if not self._cmc_map.empty else pd.NA
        selected = selected[selected["weight"] > 0].copy()
        return selected

    @staticmethod
    def _extend_for_cap(base: pd.DataFrame, top_k: int, max_assets: int, max_weight: float, target_sum: float) -> pd.DataFrame:
        base = base.head(max_assets).copy()
        needed = min(len(base), max(top_k, int(np.ceil(target_sum / max_weight - 1e-12))))
        return base.head(needed).copy()

    def _next_market_date(self, signal_date: pd.Timestamp, lag_days: int) -> Optional[pd.Timestamp]:
        calendar = pd.DatetimeIndex(sorted(self._market["date_ts"].drop_duplicates()))
        target = signal_date + pd.Timedelta(days=lag_days)
        future = calendar[calendar >= target]
        if lag_days > 0:
            future = future[future > signal_date]
        return future[0] if len(future) else None

    def _coverage_row(
        self,
        signal_date: pd.Timestamp,
        execution_date: Optional[pd.Timestamp],
        strategy_name: str,
        candidate_df: pd.DataFrame,
        candidate_count: int,
        selected_count: int,
        dropped_missing_prediction_count: int,
        dropped_missing_price_count: int,
        dropped_missing_risk_count: int,
        cash_weight: float,
        turnover: float,
        passed_qa: bool,
        failure_reason: str,
        *,
        gross_exposure: float = 0.0,
        net_exposure: float = 0.0,
        weight_sum: float = 0.0,
        max_weight_actual: float = 0.0,
    ) -> Dict[str, Any]:
        return {
            "date_ts": execution_date,
            "signal_date": signal_date,
            "execution_date": execution_date,
            "strategy_name": strategy_name,
            "model_name": self._selected_combo["model_name"],
            "horizon_days": int(self._selected_combo["horizon_days"]),
            "feature_set": self._selected_combo.get("feature_set"),
            "candidate_count": int(candidate_count),
            "selected_count": int(selected_count),
            "dropped_missing_prediction_count": int(dropped_missing_prediction_count),
            "dropped_missing_price_count": int(dropped_missing_price_count),
            "dropped_missing_risk_count": int(dropped_missing_risk_count),
            "gross_exposure": float(gross_exposure),
            "net_exposure": float(net_exposure),
            "weight_sum": float(weight_sum),
            "cash_weight": float(cash_weight),
            "max_weight_actual": float(max_weight_actual),
            "turnover": float(turnover),
            "alpha_gate_passed": bool(self._alpha_gate_passed),
            "signal_gate_passed": bool(self._alpha_gate_passed),
            "candidate_for_backtest": bool(self._alpha_gate_passed),
            "alpha_verified": False,
            "allocation_mode": self._allocation_mode,
            "passed_qa": bool(passed_qa),
            "failure_reason": failure_reason,
        }

    def _build_manifest(self, allocations: pd.DataFrame, coverage: pd.DataFrame, per_strategy_paths: Dict[str, str]) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": _utcnow_iso(),
            "input_prediction_path": self._pcfg.get(
                "prediction_path",
                self._pcfg.get("predictions_path", "data/predictions/model_predictions.parquet"),
            ),
            "input_leaderboard_path": self._pcfg.get("leaderboard_path", "data/predictions/model_leaderboard.parquet"),
            "input_market_path": self._pcfg.get("market_path", "data/raw/market/market_ohlcv.parquet"),
            "selected_model_name": self._selected_combo["model_name"],
            "selected_horizon_days": int(self._selected_combo["horizon_days"]),
            "selected_feature_set": self._selected_combo.get("feature_set"),
            "strategy_names": sorted(allocations["strategy_name"].drop_duplicates().tolist()) if not allocations.empty else [],
            "rebalance_frequency": self._pcfg.get("rebalance_frequency", "W"),
            "execution_lag_days": int(self._pcfg.get("execution_lag_days", 1)),
            "allocation_rows": int(len(allocations)),
            "rebalance_count": int(coverage["execution_date"].nunique()) if not coverage.empty else 0,
            "unique_symbols_allocated": int(allocations["symbol"].nunique()) if not allocations.empty else 0,
            "alpha_gate_passed": bool(self._alpha_gate_passed),
            "signal_gate_passed": bool(self._alpha_gate_passed),
            "candidate_for_backtest": bool(self._alpha_gate_passed),
            "alpha_verified": False,
            "allocation_mode": self._allocation_mode,
            "max_weight": float(self._pcfg.get("max_weight", self._pcfg.get("max_position_weight", 0.15))),
            "target_gross_exposure": float(self._pcfg.get("target_gross_exposure", 1.0)),
            "allow_short": bool(self._pcfg.get("allow_short", False)),
            "warnings": self._warnings,
            "limitations": [
                "PortfolioAgent transforms model forecasts into deterministic allocations; it does not prove alpha by itself.",
                "Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
            ],
            "output_files": {
                "allocations": str(self._output_dir / "allocations_from_predictions.parquet"),
                "coverage": str(self._output_dir / "allocation_coverage_report.parquet"),
                "manifest": str(self._output_dir / "allocation_manifest.json"),
                "quality": str(self._output_dir / "data_quality_allocations.md"),
                **per_strategy_paths,
            },
        }

    def _build_quality_md(self, allocations: pd.DataFrame, coverage: pd.DataFrame) -> str:
        lines = [
            "# Data Quality Allocations",
            "",
            f"- Selected model: {self._selected_combo['model_name']}",
            f"- Selected horizon: {self._selected_combo['horizon_days']}",
            f"- Selected feature set: {self._selected_combo.get('feature_set')}",
            f"- Alpha gate passed: {self._alpha_gate_passed}",
            f"- Allocation mode: {self._allocation_mode}",
            f"- Strategies produced: {sorted(allocations['strategy_name'].drop_duplicates().tolist()) if not allocations.empty else []}",
            f"- Allocation rows: {len(allocations)}",
            f"- Rebalance count: {coverage['execution_date'].nunique() if not coverage.empty else 0}",
        ]
        if not coverage.empty:
            summary = coverage.groupby("strategy_name").agg(
                average_selected_assets=("selected_count", "mean"),
                average_turnover=("turnover", "mean"),
                average_cash_weight=("cash_weight", "mean"),
            )
            lines.extend(["", "## Strategy Summary", "", summary.to_markdown()])
        if self._warnings:
            lines.extend(["", "## Warnings", ""] + [f"- {w}" for w in self._warnings])
        lines.extend(
            [
                "",
                "## Limitations",
                "",
                "- PortfolioAgent creates deterministic research allocations from model forecasts.",
                "- BacktestAgent determines whether those allocations produce alpha after transaction costs and benchmark comparison.",
                "- Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
            ]
        )
        return "\n".join(lines)

    def persist(self, result: Dict[str, Any]) -> None:
        allocations: pd.DataFrame = result["allocations"]
        coverage: pd.DataFrame = result["coverage"]
        manifest = result["manifest"]
        if allocations.empty and self._pcfg.get("fail_on_empty_allocations", True):
            raise PortfolioAgentError("No allocations to persist")
        if not allocations.empty:
            if allocations.duplicated(["date_ts", "symbol", "strategy_name"]).any():
                raise PortfolioAgentError("Duplicate date_ts + symbol + strategy_name rows detected")
            weight_numeric = pd.to_numeric(allocations["weight"], errors="coerce")
            if weight_numeric.isna().any() or (~np.isfinite(weight_numeric)).any():
                raise PortfolioAgentError("Non-finite allocation weights detected")
            if self._pcfg.get("allow_short", False) is False and (weight_numeric < -1e-12).any():
                raise PortfolioAgentError("Negative weights detected in long-only configuration")
            if self._pcfg.get("fail_on_lookahead", True):
                if (pd.to_datetime(allocations["execution_date"], utc=True) <= pd.to_datetime(allocations["signal_date"], utc=True)).any():
                    raise PortfolioAgentError("Same-day or lookahead execution detected")
            max_weight = float(self._pcfg.get("max_weight", 0.15))
            if (weight_numeric > max_weight + 1e-8).any():
                raise PortfolioAgentError("Allocation exceeds max_weight")

        alloc_path = self._output_dir / "allocations_from_predictions.parquet"
        cov_path = self._output_dir / "allocation_coverage_report.parquet"
        manifest_path = self._output_dir / "allocation_manifest.json"
        quality_path = self._output_dir / "data_quality_allocations.md"

        for strategy_name, strategy_alloc in allocations.groupby("strategy_name") if not allocations.empty else []:
            strategy_alloc.to_parquet(self._output_dir / f"allocations_{strategy_name}.parquet", index=False)
        allocations.to_parquet(alloc_path, index=False)
        coverage.to_parquet(cov_path, index=False)
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        quality_path.write_text(result["quality_md"])

        self.output_paths["allocations"] = str(alloc_path)
        self.output_paths["coverage"] = str(cov_path)
        self.output_paths["manifest"] = str(manifest_path)
        self.output_paths["quality"] = str(quality_path)
        self.output_paths.update(result.get("strategy_paths", {}))
