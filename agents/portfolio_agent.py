"""
CHF PortfolioAgent
Converts model predictions into portfolio allocations.
Implements Top-K Equal Weight and Score-Proportional strategies.

Portfolio Constraints:
- Long-only
- Max weight per asset: 10% (configurable)
- Positive signal filter (configurable)
- Weekly rebalancing (configurable)
- Liquidity filter before allocation
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from agents.base import AgentBase


class PortfolioAgent(AgentBase):
    """
    Constructs portfolio allocations from model predictions.

    Pipeline position: After ModelAgent.
    Outputs: data/allocations/allocations_{strategy}.parquet
             data/allocations/transaction_log.parquet
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        model_name: str = "lightgbm",
        horizon: int = 7,
    ):
        super().__init__(config)
        self.model_name = model_name
        self.horizon = horizon
        self._predictions_df: Optional[pd.DataFrame] = None
        self._market_df: Optional[pd.DataFrame] = None

    def prepare(self) -> None:
        """Load predictions and market data."""
        pred_dir = self.get_path("predictions")
        pred_path = pred_dir / f"predictions_{self.model_name}_h{self.horizon}d.parquet"

        # Try to find any available predictions
        if not pred_path.exists():
            pred_files = list(pred_dir.glob("predictions_*.parquet"))
            if pred_files:
                pred_path = pred_files[0]
                self.logger.warning(f"Using fallback predictions: {pred_path}")
            else:
                raise FileNotFoundError("No predictions found. Run ModelAgent first.")

        self._predictions_df = pd.read_parquet(pred_path)
        self._predictions_df["date_ts"] = pd.to_datetime(
            self._predictions_df["date_ts"], utc=True
        )

        # Load market data for liquidity filter
        raw_dir = self.get_path("raw") / "market"
        mkt_files = list(raw_dir.glob("*_ohlcv.parquet"))
        if mkt_files:
            dfs = [pd.read_parquet(f) for f in mkt_files]
            self._market_df = pd.concat(dfs, ignore_index=True)
            self._market_df["date_ts"] = pd.to_datetime(
                self._market_df["date_ts"], utc=True
            )

        alloc_dir = self.get_path("allocations")
        alloc_dir.mkdir(parents=True, exist_ok=True)

        pcfg = self.cfg.get("portfolio", {})
        self.logger.info(
            f"PortfolioAgent prepared | model={self.model_name} | "
            f"horizon={self.horizon}d | "
            f"strategies={pcfg.get('strategies', [])}"
        )

    def run(self) -> Dict[str, pd.DataFrame]:
        """
        Generate allocations for all configured strategies.
        Returns dict: strategy_name -> allocations DataFrame.
        """
        pcfg = self.cfg.get("portfolio", {})
        strategies = pcfg.get("strategies", ["top_k_equal_weight"])
        top_k_values = pcfg.get("top_k_values", [5, 10, 20])
        default_top_k = pcfg.get("default_top_k", 10)
        max_weight = pcfg.get("max_weight", 0.10)
        positive_signal_filter = pcfg.get("positive_signal_filter", True)
        rebalance_freq = pcfg.get("rebalance_frequency", "weekly")

        self.generate_snapshot_id(f"portfolio:{self.model_name}:h{self.horizon}")

        # Get rebalance dates
        rebalance_dates = self._get_rebalance_dates(rebalance_freq)

        result: Dict[str, pd.DataFrame] = {}
        tx_logs = []

        for strategy in strategies:
            self.logger.info(f"Building allocations for strategy={strategy}")
            all_alloc = []
            prev_weights: Dict[str, float] = {}

            for date in rebalance_dates:
                # Get predictions available at this date (no look-ahead)
                avail_preds = self._predictions_df[
                    self._predictions_df["date_ts"] <= date
                ]
                if avail_preds.empty:
                    continue

                # Use most recent prediction per symbol
                latest_preds = (
                    avail_preds.sort_values("date_ts")
                    .groupby("symbol")
                    .last()
                    .reset_index()
                )

                # Liquidity filter
                latest_preds = self._apply_liquidity_filter(latest_preds, date)

                # Positive signal filter
                if positive_signal_filter:
                    latest_preds = latest_preds[
                        latest_preds["predicted_return"] > 0
                    ].copy()

                if latest_preds.empty:
                    continue

                # Compute weights
                if strategy == "top_k_equal_weight":
                    weights = self._top_k_equal_weight(
                        latest_preds, default_top_k, max_weight
                    )
                elif strategy == "score_proportional":
                    weights = self._score_proportional(latest_preds, max_weight)
                else:
                    continue

                if weights.empty:
                    continue

                # Build allocation rows
                for _, row in weights.iterrows():
                    all_alloc.append({
                        "symbol": row["symbol"],
                        "date_ts": date,
                        "weight": row["weight"],
                        "rank": row.get("rank", 0),
                        "signal_score": row.get("predicted_return", 0),
                        "strategy": strategy,
                        "top_k": default_top_k,
                        "run_id": self.run_id,
                        "snapshot_id": self.snapshot_id,
                    })

                # Compute transaction log
                new_weights = dict(zip(weights["symbol"], weights["weight"]))
                tx = self._compute_transactions(prev_weights, new_weights, date)
                tx_logs.extend(tx)
                prev_weights = new_weights

            if all_alloc:
                alloc_df = pd.DataFrame(all_alloc)
                result[strategy] = alloc_df
                self.metrics[f"{strategy}_rebalance_count"] = len(rebalance_dates)

        # Compute K-sweep allocations
        for k in top_k_values:
            if k != default_top_k:
                k_alloc = self._k_sweep_allocations(
                    rebalance_dates, k, max_weight, positive_signal_filter
                )
                if not k_alloc.empty:
                    result[f"top_{k}_equal_weight"] = k_alloc

        result["transaction_log"] = pd.DataFrame(tx_logs) if tx_logs else pd.DataFrame()
        return result

    def _get_rebalance_dates(self, freq: str) -> List[pd.Timestamp]:
        """Get rebalancing dates based on frequency."""
        if self._predictions_df is None or self._predictions_df.empty:
            return []

        min_date = self._predictions_df["date_ts"].min()
        max_date = self._predictions_df["date_ts"].max()

        if freq == "weekly":
            dates = pd.date_range(start=min_date, end=max_date, freq="W-MON", tz="UTC")
        elif freq == "biweekly":
            dates = pd.date_range(start=min_date, end=max_date, freq="2W-MON", tz="UTC")
        elif freq == "monthly":
            dates = pd.date_range(start=min_date, end=max_date, freq="MS", tz="UTC")
        else:
            dates = pd.date_range(start=min_date, end=max_date, freq="W-MON", tz="UTC")

        return list(dates)

    def _apply_liquidity_filter(
        self, preds: pd.DataFrame, date: pd.Timestamp
    ) -> pd.DataFrame:
        """Remove low-liquidity assets from consideration."""
        if self._market_df is None:
            return preds

        # Get recent volume for each symbol
        recent_mkt = self._market_df[
            (self._market_df["date_ts"] <= date) &
            (self._market_df["date_ts"] >= date - pd.Timedelta(days=30))
        ]
        if recent_mkt.empty:
            return preds

        avg_vol = (
            recent_mkt.groupby("symbol")["volume"]
            .mean()
            .reset_index()
            .rename(columns={"volume": "avg_volume"})
        )

        preds = preds.merge(avg_vol, on="symbol", how="left")
        # Filter: keep assets with non-zero volume
        preds = preds[preds["avg_volume"].fillna(0) > 0].copy()
        preds = preds.drop(columns=["avg_volume"], errors="ignore")
        return preds

    def _top_k_equal_weight(
        self,
        preds: pd.DataFrame,
        k: int,
        max_weight: float,
    ) -> pd.DataFrame:
        """
        Top-K Equal Weight strategy.
        Select top K assets by predicted return, assign equal weights.
        """
        top_k = preds.nlargest(k, "predicted_return").copy()
        if top_k.empty:
            return pd.DataFrame()

        n = len(top_k)
        raw_weight = 1.0 / n
        weight = min(raw_weight, max_weight)

        # Normalize to sum to 1 after capping
        top_k["weight"] = weight
        total = top_k["weight"].sum()
        if total > 0:
            top_k["weight"] = top_k["weight"] / total

        top_k["rank"] = range(1, n + 1)
        return top_k[["symbol", "predicted_return", "weight", "rank"]]

    def _score_proportional(
        self,
        preds: pd.DataFrame,
        max_weight: float,
    ) -> pd.DataFrame:
        """
        Score-Proportional strategy.
        Weights proportional to predicted return scores (positive only).
        """
        pos_preds = preds[preds["predicted_return"] > 0].copy()
        if pos_preds.empty:
            return pd.DataFrame()

        total_score = pos_preds["predicted_return"].sum()
        if total_score <= 0:
            return pd.DataFrame()

        pos_preds["weight"] = pos_preds["predicted_return"] / total_score
        pos_preds["weight"] = pos_preds["weight"].clip(upper=max_weight)

        # Re-normalize after capping
        total = pos_preds["weight"].sum()
        if total > 0:
            pos_preds["weight"] = pos_preds["weight"] / total

        pos_preds = pos_preds.sort_values("predicted_return", ascending=False)
        pos_preds["rank"] = range(1, len(pos_preds) + 1)
        return pos_preds[["symbol", "predicted_return", "weight", "rank"]]

    def _k_sweep_allocations(
        self,
        rebalance_dates: List,
        k: int,
        max_weight: float,
        positive_signal_filter: bool,
    ) -> pd.DataFrame:
        """Generate allocations for a specific K value."""
        all_alloc = []
        for date in rebalance_dates:
            avail_preds = self._predictions_df[
                self._predictions_df["date_ts"] <= date
            ]
            if avail_preds.empty:
                continue
            latest_preds = (
                avail_preds.sort_values("date_ts")
                .groupby("symbol")
                .last()
                .reset_index()
            )
            latest_preds = self._apply_liquidity_filter(latest_preds, date)
            if positive_signal_filter:
                latest_preds = latest_preds[latest_preds["predicted_return"] > 0].copy()
            if latest_preds.empty:
                continue
            weights = self._top_k_equal_weight(latest_preds, k, max_weight)
            for _, row in weights.iterrows():
                all_alloc.append({
                    "symbol": row["symbol"],
                    "date_ts": date,
                    "weight": row["weight"],
                    "rank": row.get("rank", 0),
                    "signal_score": row.get("predicted_return", 0),
                    "strategy": "top_k_equal_weight",
                    "top_k": k,
                    "run_id": self.run_id,
                    "snapshot_id": self.snapshot_id,
                })
        return pd.DataFrame(all_alloc)

    def _compute_transactions(
        self,
        prev_weights: Dict[str, float],
        new_weights: Dict[str, float],
        date: pd.Timestamp,
    ) -> List[Dict]:
        """Compute transaction log between two weight sets."""
        all_symbols = set(prev_weights.keys()) | set(new_weights.keys())
        txs = []
        for sym in all_symbols:
            w_before = prev_weights.get(sym, 0.0)
            w_after = new_weights.get(sym, 0.0)
            if abs(w_after - w_before) > 1e-6:
                action = "BUY" if w_after > w_before else "SELL"
                if w_after == 0:
                    action = "SELL"
                elif w_before == 0:
                    action = "BUY"
                txs.append({
                    "date_ts": date,
                    "symbol": sym,
                    "action": action,
                    "weight_before": w_before,
                    "weight_after": w_after,
                    "turnover": abs(w_after - w_before),
                    "cost_bps": self.cfg.get("portfolio", {}).get(
                        "transaction_cost_bps", 20
                    ),
                    "run_id": self.run_id,
                })
        return txs

    def persist(self, result: Dict[str, pd.DataFrame]) -> None:
        """Save allocations and transaction log."""
        alloc_dir = self.get_path("allocations")
        alloc_dir.mkdir(parents=True, exist_ok=True)

        for name, df in result.items():
            if df.empty:
                continue
            path = alloc_dir / f"allocations_{name}.parquet"
            df.to_parquet(path, index=False)
            self.output_paths[f"alloc_{name}"] = str(path)

        # Save latest allocation summary
        if "top_k_equal_weight" in result:
            alloc_df = result["top_k_equal_weight"]
            if not alloc_df.empty:
                latest_date = alloc_df["date_ts"].max()
                latest = alloc_df[alloc_df["date_ts"] == latest_date].copy()
                latest_path = alloc_dir / "latest_allocation.parquet"
                latest.to_parquet(latest_path, index=False)
                self.output_paths["latest_allocation"] = str(latest_path)

        self.logger.info(
            f"PortfolioAgent persisted {len(result)} allocation sets | "
            f"snapshot_id={self.snapshot_id}"
        )
