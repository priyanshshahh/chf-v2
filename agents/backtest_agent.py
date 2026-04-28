"""
CHF BacktestAgent — VectorBT Edition
=====================================
Uses vectorbt (vbt) as the primary backtesting engine.
Falls back to a pure-NumPy engine only if vectorbt is unavailable.

Supports:
  - Main strategy backtest (Top-K equal-weight)
  - EW Top-100 benchmark (equal-weight all universe assets, monthly rebalanced)
  - BTC buy-and-hold benchmark
  - ETH buy-and-hold benchmark
  - Cost sweep (10, 20, 30, 50 bps)
  - K sweep (5, 10, 20)
  - Subperiod analysis
  - Risk-adjusted alpha report versus benchmarks

Mathematical Definitions:
─────────────────────────
CAGR = (Final_Value / Initial_Value)^(252/N_days) - 1
Annualized_Vol = std(daily_ret) * sqrt(252)
Sharpe = (CAGR - rf) / Annualized_Vol  [rf=0 for crypto]
Sortino = (CAGR - rf) / Downside_Vol
Calmar = CAGR / |Max_Drawdown|
Max_Drawdown = max((Peak - Trough) / Peak)
Rank IC = Spearman(predicted_rank, realized_rank)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from agents.base import AgentBase
from reports import evaluate_risk_adjusted_alpha, render_alpha_report_markdown

# ── VectorBT import with graceful fallback ──────────────────────────────────
try:
    import vectorbt as vbt
    _VBT_AVAILABLE = True
except ImportError:
    _VBT_AVAILABLE = False


class BacktestAgent(AgentBase):
    """
    Vectorized backtesting agent using vectorbt.

    Inputs
    ------
    - data/allocations/allocations_*.parquet   (from PortfolioAgent)
    - data/raw/market/**/*_ohlcv.parquet       (OHLCV prices, hive or flat)

    Outputs
    -------
    - data/backtests/equity_curves.parquet
    - data/backtests/backtest_summary.parquet
    - data/backtests/backtest_summary.json
    - data/backtests/vbt_stats.json
    - data/reports/alpha_report.json
    - data/reports/alpha_report.md

    Run command
    -----------
    python main.py backtest

    Success criterion
    -----------------
    backtest_summary.parquet exists with Sharpe column populated.

    Failure mode (missing upstream)
    --------------------------------
    Logs a warning and returns empty result dict without raising.
    """

    def __init__(self, cfg: Dict[str, Any], strategy: str = "top_k_equal_weight"):
        super().__init__(cfg)
        self.strategy = strategy
        self._allocations_df: pd.DataFrame = pd.DataFrame()
        self._market_df: Optional[pd.DataFrame] = None

    def prepare(self) -> None:
        alloc_dir = self.get_path("allocations")
        alloc_files = sorted(alloc_dir.glob("allocations_*.parquet"))
        alloc_dfs = []
        for alloc_file in alloc_files:
            df = pd.read_parquet(alloc_file)
            required_cols = {"symbol", "date_ts", "weight"}
            if required_cols.issubset(df.columns):
                alloc_dfs.append(df)
            else:
                self.logger.info(
                    f"Skipping non-allocation artifact during backtest prep: {alloc_file.name}"
                )

        if alloc_dfs:
            self._allocations_df = pd.concat(alloc_dfs, ignore_index=True)
            self._allocations_df["date_ts"] = pd.to_datetime(
                self._allocations_df["date_ts"], utc=True
            )
        else:
            self.logger.warning("No allocation files found — backtest will be empty.")

        self._market_df = self._load_market_data()
        self.get_path("backtests").mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"BacktestAgent prepared | strategy={self.strategy} | vbt={_VBT_AVAILABLE}"
        )

    def run(self) -> Dict[str, Any]:
        btcfg = self.cfg.get("backtesting", {})
        initial_capital = float(btcfg.get("initial_capital", 100_000))
        cost_bps = float(btcfg.get("transaction_cost_bps", 20))
        cost_sweep_vals = btcfg.get("cost_sweep_bps", [10, 20, 30, 50])
        k_sweep_vals = btcfg.get("k_sweep", [5, 10, 20])
        subperiods = btcfg.get("subperiods", [])

        self.generate_snapshot_id(f"backtest:{self.strategy}")
        results: Dict[str, Any] = {}

        # Main strategy
        self.logger.info(f"Running main backtest: strategy={self.strategy}")
        main_equity, main_summary, main_vbt = self._run_backtest_vbt(
            self._allocations_df, cost_bps, initial_capital, label="main"
        )
        results["main"] = {"equity": main_equity, "summary": main_summary, "vbt_stats": main_vbt}

        # BTC and ETH buy-and-hold benchmarks
        btc_eq, btc_s = self._run_btc_benchmark(initial_capital)
        results["benchmark_BTC"] = {"equity": btc_eq, "summary": btc_s}
        eth_eq, eth_s = self._run_eth_benchmark(initial_capital)
        results["benchmark_ETH"] = {"equity": eth_eq, "summary": eth_s}

        # EW Top-100 benchmark (PDF spec §9, §12 — Rigorous Benchmarking)
        ew_eq, ew_s = self._run_ew_top100_benchmark(initial_capital, cost_bps)
        results["benchmark_EW_top100"] = {"equity": ew_eq, "summary": ew_s}

        # Cost sweep
        cost_sweep_results = []
        for c in cost_sweep_vals:
            _, s, _ = self._run_backtest_vbt(
                self._allocations_df, float(c), initial_capital, label=f"cost_{c}bps"
            )
            s["cost_bps"] = c
            cost_sweep_results.append(s)
        results["cost_sweep"] = cost_sweep_results

        # K sweep
        k_sweep_results = []
        alloc_dir = self.get_path("allocations")
        for k in k_sweep_vals:
            k_path = alloc_dir / f"allocations_top_{k}_equal_weight.parquet"
            if k_path.exists():
                k_alloc = pd.read_parquet(k_path)
                k_alloc["date_ts"] = pd.to_datetime(k_alloc["date_ts"], utc=True)
                _, s, _ = self._run_backtest_vbt(
                    k_alloc, cost_bps, initial_capital, label=f"top_{k}"
                )
                s["top_k"] = k
                k_sweep_results.append(s)
        results["k_sweep"] = k_sweep_results

        # Subperiod analysis
        subperiod_results = []
        for sp in subperiods:
            start = pd.Timestamp(sp["start"], tz="UTC")
            end = pd.Timestamp(sp["end"], tz="UTC")
            sp_alloc = self._allocations_df[
                (self._allocations_df["date_ts"] >= start)
                & (self._allocations_df["date_ts"] <= end)
            ].copy()
            if not sp_alloc.empty:
                _, s, _ = self._run_backtest_vbt(
                    sp_alloc, cost_bps, initial_capital, label=sp["name"]
                )
                s.update({"subperiod": sp["name"], "start": sp["start"], "end": sp["end"]})
                subperiod_results.append(s)
        results["subperiods"] = subperiod_results

        self.logger.info(
            f"Backtest complete | Sharpe={main_summary.get('sharpe', 0):.3f} | "
            f"CAGR={main_summary.get('cagr', 0):.3f} | "
            f"MaxDD={main_summary.get('max_drawdown', 0):.3f}"
        )
        return results

    # ── VectorBT core engine ─────────────────────────────────────────────

    def _run_backtest_vbt(
        self,
        allocations: pd.DataFrame,
        cost_bps: float,
        initial_capital: float,
        label: str = "strategy",
    ) -> Tuple[pd.DataFrame, Dict, Dict]:
        """
        Run a single backtest using vectorbt.Portfolio.from_orders().
        Uses size_type='targetpercent' for daily weight rebalancing.
        Falls back to NumPy engine if vectorbt is unavailable or raises.
        """
        if allocations.empty or self._market_df is None:
            return pd.DataFrame(), {}, {}

        if not _VBT_AVAILABLE:
            self.logger.warning("vectorbt not available — using NumPy fallback.")
            eq, s = self._run_backtest_numpy(allocations, cost_bps, initial_capital, label)
            return eq, s, {}

        all_symbols = allocations["symbol"].unique().tolist()
        price_pivot = self._build_price_pivot(all_symbols)
        if price_pivot.empty:
            return pd.DataFrame(), {}, {}

        weight_matrix = self._build_weight_matrix(allocations, price_pivot.index, all_symbols)
        fees = cost_bps / 10_000.0

        try:
            pf = vbt.Portfolio.from_orders(
                close=price_pivot,
                size=weight_matrix,
                size_type="targetpercent",
                fees=fees,
                init_cash=initial_capital,
                freq="1D",
                group_by=True,
                cash_sharing=True,
                call_seq="auto",
            )
            equity_series = pf.value()
            equity_df = pd.DataFrame({
                "date_ts": equity_series.index,
                "portfolio_value": equity_series.values,
            })
            equity_df["daily_return"] = equity_df["portfolio_value"].pct_change().fillna(0)
            equity_df["backtest_name"] = label

            try:
                stats = pf.stats()
                vbt_stats = stats.to_dict() if hasattr(stats, "to_dict") else {}
            except Exception:
                vbt_stats = {}

            summary = self._compute_performance_metrics(equity_df, initial_capital, cost_bps, label)
            return equity_df, summary, vbt_stats

        except Exception as exc:
            self.logger.warning(f"vectorbt failed ({exc}); falling back to NumPy.")
            eq, s = self._run_backtest_numpy(allocations, cost_bps, initial_capital, label)
            return eq, s, {}

    def _build_weight_matrix(
        self,
        allocations: pd.DataFrame,
        date_index: pd.DatetimeIndex,
        symbols: List[str],
    ) -> pd.DataFrame:
        """Convert long-format allocations to wide weight matrix, forward-filled."""
        weight_pivot = allocations.pivot_table(
            index="date_ts", columns="symbol", values="weight", aggfunc="last"
        ).reindex(columns=symbols, fill_value=0.0)
        return weight_pivot.reindex(date_index).ffill().fillna(0.0)

    # ── NumPy fallback ───────────────────────────────────────────────────

    def _run_backtest_numpy(
        self,
        allocations: pd.DataFrame,
        cost_bps: float,
        initial_capital: float,
        label: str = "strategy",
    ) -> Tuple[pd.DataFrame, Dict]:
        """Pure-NumPy vectorized backtest fallback."""
        if allocations.empty or self._market_df is None:
            return pd.DataFrame(), {}

        all_symbols = allocations["symbol"].unique().tolist()
        price_pivot = self._build_price_pivot(all_symbols)
        if price_pivot.empty:
            return pd.DataFrame(), {}

        daily_returns = price_pivot.pct_change().fillna(0)
        weight_matrix = self._build_weight_matrix(allocations, price_pivot.index, all_symbols)

        prev_weights = np.zeros(len(all_symbols))
        portfolio_value = initial_capital
        equity_curve = []

        for i, date in enumerate(daily_returns.index):
            curr_weights = weight_matrix.iloc[i].values
            turnover = np.sum(np.abs(curr_weights - prev_weights))
            cost = turnover * (cost_bps / 10_000.0) * portfolio_value
            day_ret = daily_returns.iloc[i].values
            gross_return = float(np.dot(curr_weights, day_ret))
            portfolio_value = portfolio_value * (1 + gross_return) - cost
            equity_curve.append({
                "date_ts": date,
                "portfolio_value": portfolio_value,
                "daily_return": gross_return,
                "n_positions": int(np.sum(curr_weights > 0)),
            })
            prev_weights = curr_weights.copy()

        equity_df = pd.DataFrame(equity_curve)
        equity_df["backtest_name"] = label
        summary = self._compute_performance_metrics(equity_df, initial_capital, cost_bps, label)
        return equity_df, summary

    # ── BTC benchmark ────────────────────────────────────────────────────

    def _run_btc_benchmark(self, initial_capital: float) -> Tuple[pd.DataFrame, Dict]:
        """100% BTC buy-and-hold."""
        if self._market_df is None:
            return pd.DataFrame(), {}
        btc = self._market_df[self._market_df["symbol"].isin(["BTC", "BTCUSDT"])].copy()
        if btc.empty:
            return pd.DataFrame(), {}
        btc = btc.sort_values("date_ts")
        btc["daily_return"] = btc["close"].pct_change().fillna(0)
        btc["portfolio_value"] = initial_capital * (1 + btc["daily_return"]).cumprod()
        btc["backtest_name"] = "benchmark_BTC"
        cols = ["date_ts", "portfolio_value", "daily_return", "backtest_name"]
        summary = self._compute_performance_metrics(btc[cols], initial_capital, 0.0, "benchmark_BTC")
        return btc[cols], summary

    def _run_eth_benchmark(self, initial_capital: float) -> Tuple[pd.DataFrame, Dict]:
        """100% ETH buy-and-hold."""
        if self._market_df is None:
            return pd.DataFrame(), {}
        eth = self._market_df[self._market_df["symbol"].isin(["ETH", "ETHUSDT"])].copy()
        if eth.empty:
            return pd.DataFrame(), {}
        eth = eth.sort_values("date_ts")
        eth["daily_return"] = eth["close"].pct_change().fillna(0)
        eth["portfolio_value"] = initial_capital * (1 + eth["daily_return"]).cumprod()
        eth["backtest_name"] = "benchmark_ETH"
        cols = ["date_ts", "portfolio_value", "daily_return", "backtest_name"]
        summary = self._compute_performance_metrics(eth[cols], initial_capital, 0.0, "benchmark_ETH")
        return eth[cols], summary

    # ── EW Top-100 benchmark ─────────────────────────────────────────────

    def _run_ew_top100_benchmark(
        self, initial_capital: float, cost_bps: float
    ) -> Tuple[pd.DataFrame, Dict]:
        """
        Equal-weight all universe assets, rebalanced monthly.
        Required by PDF spec §9 and §12 (Rigorous Benchmarking).
        """
        if self._market_df is None:
            return pd.DataFrame(), {}

        all_symbols = self._market_df["symbol"].unique().tolist()
        price_pivot = self._build_price_pivot(all_symbols)
        if price_pivot.empty:
            return pd.DataFrame(), {}

        monthly_dates = pd.date_range(
            start=price_pivot.index.min(),
            end=price_pivot.index.max(),
            freq="MS",
            tz="UTC",
        )

        ew_rows = []
        for d in monthly_dates:
            future = price_pivot.loc[price_pivot.index >= d]
            if future.empty:
                continue
            valid = future.head(1).dropna(axis=1).columns.tolist()
            if not valid:
                continue
            w = 1.0 / len(valid)
            for sym in valid:
                ew_rows.append({"date_ts": d, "symbol": sym, "weight": w})

        if not ew_rows:
            return pd.DataFrame(), {}

        ew_alloc = pd.DataFrame(ew_rows)
        ew_alloc["date_ts"] = pd.to_datetime(ew_alloc["date_ts"], utc=True)

        if _VBT_AVAILABLE:
            eq, s, _ = self._run_backtest_vbt(
                ew_alloc, cost_bps, initial_capital, "benchmark_EW_top100"
            )
        else:
            eq, s = self._run_backtest_numpy(
                ew_alloc, cost_bps, initial_capital, "benchmark_EW_top100"
            )
        return eq, s

    # ── Helpers ──────────────────────────────────────────────────────────

    def _load_market_data(self) -> Optional[pd.DataFrame]:
        """Load market data from hive-partitioned or flat Parquet."""
        market_dir = self.get_path("raw") / "market"
        dfs = []
        hive_files = list(market_dir.glob("year=*/month=*/*.parquet"))
        if hive_files:
            for f in hive_files:
                dfs.append(pd.read_parquet(f))
        else:
            for f in market_dir.glob("*_ohlcv.parquet"):
                dfs.append(pd.read_parquet(f))
        if not dfs:
            return None
        combined = pd.concat(dfs, ignore_index=True)
        combined["date_ts"] = pd.to_datetime(combined["date_ts"], utc=True)
        return combined

    def _build_price_pivot(self, symbols: List[str]) -> pd.DataFrame:
        if self._market_df is None:
            return pd.DataFrame()
        filtered = self._market_df[self._market_df["symbol"].isin(symbols)]
        if filtered.empty:
            return pd.DataFrame()
        pivot = filtered.pivot_table(
            index="date_ts", columns="symbol", values="close", aggfunc="last"
        )
        return pivot.sort_index()

    def _compute_performance_metrics(
        self,
        equity_df: pd.DataFrame,
        initial_capital: float,
        cost_bps: float,
        strategy_name: str,
    ) -> Dict:
        if equity_df.empty:
            return {}
        daily_ret = equity_df["daily_return"].values
        portfolio_values = equity_df["portfolio_value"].values
        n_days = len(daily_ret)
        if n_days < 2:
            return {}
        final_value = float(portfolio_values[-1])
        years = n_days / 252
        cagr = float((final_value / initial_capital) ** (1 / max(years, 0.01)) - 1)
        ann_vol = float(np.std(daily_ret) * np.sqrt(252))
        sharpe = float((np.mean(daily_ret) * 252) / (ann_vol + 1e-10))
        neg_ret = daily_ret[daily_ret < 0]
        downside_vol = float(np.std(neg_ret) * np.sqrt(252)) if len(neg_ret) > 1 else ann_vol
        sortino = float((np.mean(daily_ret) * 252) / (downside_vol + 1e-10))
        running_max = np.maximum.accumulate(portfolio_values)
        drawdown = (portfolio_values - running_max) / (running_max + 1e-10)
        max_drawdown = float(drawdown.min())
        calmar = float(cagr / (abs(max_drawdown) + 1e-10))
        avg_positions = (
            float(equity_df["n_positions"].mean())
            if "n_positions" in equity_df.columns
            else 0.0
        )
        return {
            "strategy": strategy_name,
            "cost_bps": cost_bps,
            "n_days": n_days,
            "initial_capital": initial_capital,
            "final_value": final_value,
            "total_return": float((final_value / initial_capital) - 1),
            "cagr": cagr,
            "annualized_vol": ann_vol,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "max_drawdown": max_drawdown,
            "avg_positions": avg_positions,
            "start_date": str(equity_df["date_ts"].iloc[0]),
            "end_date": str(equity_df["date_ts"].iloc[-1]),
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def persist(self, result: Dict[str, Any]) -> None:
        bt_dir = self.get_path("backtests")
        bt_dir.mkdir(parents=True, exist_ok=True)

        equity_dfs: List[pd.DataFrame] = []
        summaries: List[Dict] = []
        vbt_stats_all: Dict[str, Any] = {}

        for key, data in result.items():
            if not isinstance(data, dict):
                continue
            eq = data.get("equity")
            if isinstance(eq, pd.DataFrame) and not eq.empty:
                eq = eq.copy()
                eq["backtest_name"] = key
                equity_dfs.append(eq)
            summary = data.get("summary", {})
            if summary:
                summary["backtest_name"] = key
                summaries.append(summary)
            vbt_s = data.get("vbt_stats")
            if vbt_s:
                vbt_stats_all[key] = vbt_s

        for sweep_key in ("cost_sweep", "k_sweep", "subperiods"):
            items = result.get(sweep_key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        item["sweep_type"] = sweep_key
                        summaries.append(item)

        if equity_dfs:
            equity_all = pd.concat(equity_dfs, ignore_index=True)
            equity_path = bt_dir / "equity_curves.parquet"
            equity_all.to_parquet(equity_path, index=False)
            self.output_paths["equity_curves"] = str(equity_path)

        summary_df = pd.DataFrame()
        if summaries:
            summary_df = pd.DataFrame(summaries)
            summary_path = bt_dir / "backtest_summary.parquet"
            summary_df.to_parquet(summary_path, index=False)
            self.output_paths["backtest_summary"] = str(summary_path)
            summary_json_path = bt_dir / "backtest_summary.json"
            with open(summary_json_path, "w") as f:
                json.dump(summaries, f, indent=2, default=str)

        if vbt_stats_all:
            vbt_path = bt_dir / "vbt_stats.json"
            with open(vbt_path, "w") as f:
                json.dump(vbt_stats_all, f, indent=2, default=str)

        if equity_dfs and not summary_df.empty:
            reports_dir = self.get_path("reports")
            report = evaluate_risk_adjusted_alpha(equity_all, summary_df)
            report_json_path = reports_dir / "alpha_report.json"
            with open(report_json_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            report_md_path = reports_dir / "alpha_report.md"
            report_md_path.write_text(render_alpha_report_markdown(report))
            self.output_paths["alpha_report_json"] = str(report_json_path)
            self.output_paths["alpha_report_md"] = str(report_md_path)

        self.logger.info(
            f"BacktestAgent persisted | snapshot_id={self.snapshot_id} | vbt={_VBT_AVAILABLE}"
        )
