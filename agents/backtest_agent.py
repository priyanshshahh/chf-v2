from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from agents.base import AgentBase

try:
    import vectorbt as vbt  # noqa: F401

    _VBT_AVAILABLE = True
except Exception:
    _VBT_AVAILABLE = False


class BacktestAgentError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out


def _perf_from_returns(
    returns: pd.Series,
    *,
    initial_capital: float,
    turnover: pd.Series,
    costs: pd.Series,
    n_positions: pd.Series,
    annualization_days: float = 365.0,
) -> Tuple[pd.Series, pd.Series, Dict[str, Any]]:
    returns = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    turnover = pd.to_numeric(turnover, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    costs = pd.to_numeric(costs, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    n_positions = pd.to_numeric(n_positions, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    equity = initial_capital * (1.0 + returns).cumprod()
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0

    n_days = int(len(returns))
    final_value = float(equity.iloc[-1]) if n_days else float(initial_capital)
    total_return = float(final_value / initial_capital - 1.0) if initial_capital > 0 else float("nan")
    ann_days = float(annualization_days) if float(annualization_days) > 0 else 365.0
    years = n_days / ann_days if n_days > 0 else 0.0
    if final_value <= 0 or years <= 0:
        cagr = float("nan")
    else:
        cagr = float((final_value / initial_capital) ** (1.0 / years) - 1.0)
    ann_vol = float(returns.std(ddof=0) * np.sqrt(ann_days)) if n_days > 1 else float("nan")
    mean_daily = float(returns.mean()) if n_days else float("nan")
    sharpe = float((mean_daily * ann_days) / ann_vol) if np.isfinite(ann_vol) and ann_vol > 0 else float("nan")
    downside = returns[returns < 0]
    downside_vol = float(downside.std(ddof=0) * np.sqrt(ann_days)) if len(downside) > 1 else float("nan")
    sortino = (
        float((mean_daily * ann_days) / downside_vol)
        if np.isfinite(downside_vol) and downside_vol > 0
        else float("nan")
    )
    max_dd = float(drawdown.min()) if n_days else float("nan")
    calmar = float(cagr / abs(max_dd)) if np.isfinite(cagr) and max_dd < 0 else float("nan")

    return equity, drawdown, {
        "n_days": n_days,
        "start_date": returns.index.min().isoformat() if n_days else None,
        "end_date": returns.index.max().isoformat() if n_days else None,
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "annualized_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "average_daily_return": mean_daily,
        "hit_rate": float((returns > 0).mean()) if n_days else float("nan"),
        "best_day": float(returns.max()) if n_days else float("nan"),
        "worst_day": float(returns.min()) if n_days else float("nan"),
        "average_turnover": float(turnover.mean()) if n_days else 0.0,
        "annualized_turnover": float(turnover.mean() * ann_days) if n_days else 0.0,
        "total_cost_drag": float(costs.sum()) if n_days else 0.0,
        "average_positions": float(n_positions.mean()) if n_days else 0.0,
        "min_positions": int(n_positions.min()) if n_days else 0,
        "max_positions": int(n_positions.max()) if n_days else 0,
    }


class BacktestAgent(AgentBase):
    def __init__(self, cfg: Dict[str, Any], strategy: Optional[str] = None):
        super().__init__(cfg)
        self._project_root = Path(self.cfg["_project_root"])
        self._bt_cfg = self.cfg.get("backtesting", {})
        self.strategy_override = strategy
        self._allocations = pd.DataFrame()
        self._allocation_manifest: Dict[str, Any] = {}
        self._market = pd.DataFrame()
        self._prices = pd.DataFrame()
        self._returns = pd.DataFrame()
        self._warnings: List[str] = []
        self._output_dir = self._project_root / "data" / "backtests"
        self._strategy_start: Optional[pd.Timestamp] = None
        self._strategy_end: Optional[pd.Timestamp] = None

    def prepare(self) -> None:
        cfg = self._bt_cfg = self.cfg.get("backtesting", {})
        self._output_dir = _resolve(self._project_root, cfg.get("output_dir", "data/backtests"))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        allocation_path = _resolve(
            self._project_root,
            cfg.get("allocation_path", "data/allocations/allocations_from_predictions.parquet"),
        )
        allocation_manifest_path = _resolve(
            self._project_root,
            cfg.get("allocation_manifest_path", "data/allocations/allocation_manifest.json"),
        )
        market_path = _resolve(self._project_root, cfg.get("market_path", "data/raw/market/market_ohlcv.parquet"))
        if not allocation_path.exists():
            raise FileNotFoundError(f"Required backtest allocation input missing: {allocation_path}")
        if not market_path.exists():
            raise FileNotFoundError(f"Required backtest market input missing: {market_path}")
        self._allocations = pd.read_parquet(allocation_path)
        if self._allocations.empty and cfg.get("fail_on_empty_backtest", True):
            raise BacktestAgentError("Allocation file is empty")
        for col in ["date_ts", "signal_date", "execution_date"]:
            self._allocations[col] = pd.to_datetime(self._allocations[col], utc=True).dt.normalize()
        self._validate_allocation_inputs()
        self._strategy_start = pd.to_datetime(self._allocations["date_ts"], utc=True).min()
        self._strategy_end = pd.to_datetime(self._allocations["date_ts"], utc=True).max()
        if allocation_manifest_path.exists():
            with open(allocation_manifest_path, "r") as fh:
                self._allocation_manifest = json.load(fh)
        self._market = pd.read_parquet(market_path)
        self._market["date_ts"] = pd.to_datetime(self._market["date_ts"], utc=True).dt.normalize()
        self._market = self._market.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        self._prices = (
            self._market.pivot_table(index="date_ts", columns="symbol", values="close", aggfunc="last").sort_index()
        )
        self._prices = self._prices.loc[(self._prices.index >= self._strategy_start) & (self._prices.index <= self._strategy_end)]
        self._returns = self._prices.pct_change(fill_method=None)
        if self._prices.empty:
            raise BacktestAgentError("Backtest market price matrix is empty")
        self.logger.info("BacktestAgent prepared | strategies=%s", sorted(self._allocations["strategy_name"].drop_duplicates()))

    def _validate_allocation_inputs(self) -> None:
        cfg = self._bt_cfg
        required = {"date_ts", "signal_date", "execution_date", "symbol", "strategy_name", "weight"}
        missing = required - set(self._allocations.columns)
        if missing:
            raise BacktestAgentError(f"Allocation file missing required columns: {sorted(missing)}")
        if self._allocations.duplicated(["date_ts", "symbol", "strategy_name"]).any():
            raise BacktestAgentError("Allocation file has duplicate date_ts + symbol + strategy_name rows")
        if cfg.get("fail_on_lookahead", True) and (self._allocations["execution_date"] <= self._allocations["signal_date"]).any():
            raise BacktestAgentError("Allocation file contains same-day or earlier execution_date")
        weights = pd.to_numeric(self._allocations["weight"], errors="coerce")
        if weights.isna().any() or (~np.isfinite(weights)).any():
            raise BacktestAgentError("Allocation weights contain null or non-finite values")
        if not bool(cfg.get("allow_short", False)) and (weights < -1e-12).any():
            raise BacktestAgentError("Long-only backtest received negative allocation weights")
        max_weight = float(cfg.get("max_weight", cfg.get("max_position_weight", 1.0)))
        if (weights > max_weight + 1e-9).any():
            raise BacktestAgentError("Allocation weight exceeds configured max_weight")
        target = float(cfg.get("target_gross_exposure", 1.0))
        exposure = self._allocations.assign(_abs_weight=weights.abs()).groupby(["date_ts", "strategy_name"])["_abs_weight"].sum()
        if (exposure > target + 1e-6).any():
            raise BacktestAgentError("Allocation gross exposure exceeds configured target_gross_exposure")

    def run(self) -> Dict[str, Any]:
        self.generate_snapshot_id("backtest_research")
        strategies = sorted(self._allocations["strategy_name"].drop_duplicates().tolist())
        if self.strategy_override:
            strategies = [s for s in strategies if s == self.strategy_override]
        if not strategies:
            raise BacktestAgentError("No strategies available to backtest")

        strategy_equities: List[pd.DataFrame] = []
        drawdown_frames: List[pd.DataFrame] = []
        turnover_frames: List[pd.DataFrame] = []
        strategy_summaries: List[Dict[str, Any]] = []
        for strategy_name in strategies:
            alloc = self._allocations[self._allocations["strategy_name"] == strategy_name].copy()
            if alloc.empty:
                continue
            equity_df, drawdown_df, turnover_df, summary = self._run_strategy(alloc, strategy_name=strategy_name)
            strategy_equities.append(equity_df)
            drawdown_frames.append(drawdown_df)
            turnover_frames.append(turnover_df)
            strategy_summaries.append(summary)

        if not strategy_summaries:
            raise BacktestAgentError("No strategy produced a usable backtest")

        benchmark_equities, benchmark_summaries, benchmark_sanity_report = self._run_benchmarks()
        strategy_comparison = self._build_strategy_comparison(strategy_summaries, benchmark_summaries, benchmark_sanity_report)
        cost_sweep = self._run_cost_sweep(strategies)
        alpha_report = self._build_alpha_report(strategy_comparison, benchmark_summaries)
        backtest_summary = pd.DataFrame(strategy_summaries).sort_values("strategy_name").reset_index(drop=True)
        benchmark_summary = pd.DataFrame(benchmark_summaries).sort_values("strategy_name").reset_index(drop=True)
        equity_curves = pd.concat(strategy_equities + benchmark_equities, ignore_index=True).sort_values(
            ["strategy_name", "date_ts"]
        )
        drawdown_series = pd.concat(drawdown_frames, ignore_index=True).sort_values(["strategy_name", "date_ts"])
        turnover_report = pd.concat(turnover_frames, ignore_index=True).sort_values(["strategy_name", "date_ts"])

        best_sharpe_row = backtest_summary.sort_values(["sharpe", "total_return"], ascending=[False, False], na_position="last").iloc[0]
        self.metrics["strategy_cagr"] = _safe_float(best_sharpe_row.get("cagr"))
        self.metrics["strategy_sharpe"] = _safe_float(best_sharpe_row.get("sharpe"))
        self.metrics["best_strategy_by_sharpe"] = best_sharpe_row["strategy_name"]

        manifest = {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": _utcnow_iso(),
            "input_allocation_path": self._bt_cfg.get("allocation_path", "data/allocations/allocations_from_predictions.parquet"),
            "input_allocation_manifest_path": self._bt_cfg.get("allocation_manifest_path", "data/allocations/allocation_manifest.json"),
            "input_market_path": self._bt_cfg.get("market_path", "data/raw/market/market_ohlcv.parquet"),
            "strategies_backtested": strategies,
            "benchmark_names": sorted([row["strategy_name"] for row in benchmark_summaries]),
            "transaction_cost_bps": float(self._bt_cfg.get("transaction_cost_bps", 20)),
            "cost_sweep_bps": list(self._bt_cfg.get("cost_sweep_bps", [0, 10, 20, 50, 100])),
            "allocation_mode": self._allocation_manifest.get("allocation_mode"),
            "alpha_gate_passed": self._allocation_manifest.get("alpha_gate_passed"),
            "signal_gate_passed": self._allocation_manifest.get("signal_gate_passed", self._allocation_manifest.get("alpha_gate_passed")),
            "candidate_for_backtest": self._allocation_manifest.get("candidate_for_backtest", self._allocation_manifest.get("alpha_gate_passed")),
            "alpha_verified": bool((strategy_comparison["alpha_status"] == "passed").any()),
            "annualization_days": float(self._bt_cfg.get("annualization_days", 365)),
            "benchmark_sanity_passed": bool(benchmark_sanity_report["passed_sanity"].fillna(False).all()) if not benchmark_sanity_report.empty else False,
            "warnings": self._warnings,
            "limitations": [
                "Backtest results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
            ],
            "output_files": {
                "equity_curves": str(self._output_dir / "equity_curves.parquet"),
                "backtest_summary": str(self._output_dir / "backtest_summary.parquet"),
                "benchmark_summary": str(self._output_dir / "benchmark_summary.parquet"),
                "strategy_comparison": str(self._output_dir / "strategy_comparison.parquet"),
                "cost_sweep": str(self._output_dir / "cost_sweep.parquet"),
                "benchmark_sanity_report": str(self._output_dir / "benchmark_sanity_report.parquet"),
                "drawdown_series": str(self._output_dir / "drawdown_series.parquet"),
                "turnover_report": str(self._output_dir / "turnover_report.parquet"),
                "alpha_report_json": str(self._output_dir / "alpha_report.json"),
                "alpha_report_md": str(self._output_dir / "alpha_report.md"),
            },
        }

        return {
            "equity_curves": equity_curves,
            "backtest_summary": backtest_summary,
            "benchmark_summary": benchmark_summary,
            "strategy_comparison": strategy_comparison,
            "cost_sweep": cost_sweep,
            "benchmark_sanity_report": benchmark_sanity_report,
            "drawdown_series": drawdown_series,
            "turnover_report": turnover_report,
            "alpha_report": alpha_report,
            "manifest": manifest,
            "data_quality_md": self._build_quality_report(backtest_summary, benchmark_summary, strategy_comparison),
        }

    def _run_strategy(
        self,
        allocations: pd.DataFrame,
        *,
        strategy_name: str,
        cost_bps: Optional[float] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        cost_bps = float(self._bt_cfg.get("transaction_cost_bps", 20) if cost_bps is None else cost_bps)
        initial_capital = float(self._bt_cfg.get("initial_capital", 100000))
        strategy_symbols = sorted(allocations["symbol"].astype(str).drop_duplicates())
        if not strategy_symbols:
            raise BacktestAgentError(f"{strategy_name} has no symbols")
        prices = self._prices.reindex(columns=strategy_symbols)
        if prices.isna().all().all():
            raise BacktestAgentError(f"{strategy_name} has no market prices")

        schedule = (
            allocations.pivot_table(index="execution_date", columns="symbol", values="weight", aggfunc="sum")
            .sort_index()
            .reindex(columns=strategy_symbols)
            .fillna(0.0)
        )
        daily_target = schedule.reindex(prices.index).ffill().fillna(0.0)
        valid_prices = prices.notna() & (prices > 0)
        effective_weights = daily_target.where(valid_prices, 0.0)
        effective_weights = effective_weights.fillna(0.0)
        prev_weights = effective_weights.shift(1).fillna(0.0)
        turnover = (effective_weights - prev_weights).abs().sum(axis=1)
        gross_returns = (prev_weights * self._returns.reindex(columns=strategy_symbols).fillna(0.0)).sum(axis=1)
        transaction_cost = turnover * (cost_bps / 10000.0)
        net_returns = gross_returns - transaction_cost
        n_positions = (prev_weights > 0).sum(axis=1).astype(int)

        equity, drawdown, perf = _perf_from_returns(
            net_returns,
            initial_capital=initial_capital,
            turnover=turnover,
            costs=transaction_cost,
            n_positions=n_positions,
            annualization_days=float(self._bt_cfg.get("annualization_days", 365)),
        )
        running_peak = equity.cummax()
        failure_reason = ""
        missing_held = (prev_weights > 0) & self._returns.reindex(columns=strategy_symbols).isna()
        missing_held_fraction = float(missing_held.sum().sum() / max((prev_weights > 0).sum().sum(), 1))
        if missing_held_fraction > float(self._bt_cfg.get("max_missing_held_return_fraction", 0.0)):
            failure_reason = "missing_held_returns"
            if self._bt_cfg.get("fail_on_missing_held_returns", True):
                raise BacktestAgentError(f"{strategy_name} missing held returns fraction {missing_held_fraction:.6f}")
        extreme = self._returns.reindex(columns=strategy_symbols).abs() > float(self._bt_cfg.get("max_abs_daily_return", 10.0))
        if extreme.any().any() and not self._bt_cfg.get("allow_extreme_return_sanitization", False):
            raise BacktestAgentError(f"{strategy_name} has extreme held return observations")
        final_value = float(equity.iloc[-1]) if len(equity) else float(initial_capital)
        if final_value <= 0:
            failure_reason = "portfolio_value_non_positive"
        elif prices.notna().sum().sum() == 0:
            failure_reason = "missing_market_prices"

        equity_df = pd.DataFrame(
            {
                "date_ts": prices.index,
                "strategy_name": strategy_name,
                "portfolio_value": equity.values,
                "gross_return": gross_returns.values,
                "net_return": net_returns.values,
                "transaction_cost": transaction_cost.values,
                "turnover": turnover.values,
                "n_positions": n_positions.values,
                "benchmark_type": "strategy",
                "snapshot_id": self.snapshot_id,
                "run_id": self.run_id,
            }
        )
        drawdown_df = pd.DataFrame(
            {
                "date_ts": prices.index,
                "strategy_name": strategy_name,
                "portfolio_value": equity.values,
                "running_peak": running_peak.values,
                "drawdown": drawdown.values,
            }
        )
        turnover_df = pd.DataFrame(
            {
                "date_ts": prices.index,
                "strategy_name": strategy_name,
                "turnover": turnover.values,
                "transaction_cost": transaction_cost.values,
                "gross_return": gross_returns.values,
                "net_return": net_returns.values,
                "n_positions": n_positions.values,
            }
        )
        perf.update(
            {
                "strategy_name": strategy_name,
                "failure_reason": failure_reason,
                "transaction_cost_bps": cost_bps,
                "alpha_gate_passed": bool(self._allocation_manifest.get("alpha_gate_passed", False)),
                "signal_gate_passed": bool(self._allocation_manifest.get("signal_gate_passed", self._allocation_manifest.get("alpha_gate_passed", False))),
                "candidate_for_backtest": bool(self._allocation_manifest.get("candidate_for_backtest", self._allocation_manifest.get("alpha_gate_passed", False))),
                "allocation_mode": self._allocation_manifest.get("allocation_mode"),
                "missing_held_return_fraction": missing_held_fraction,
            }
        )
        return equity_df, drawdown_df, turnover_df, perf

    def _run_benchmark_weights(
        self,
        *,
        name: str,
        weights: pd.DataFrame,
        cost_bps: Optional[float] = None,
        sanitize_extreme_returns: bool = False,
    ) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        cost_bps = float(self._bt_cfg.get("transaction_cost_bps", 20) if cost_bps is None else cost_bps)
        initial_capital = float(self._bt_cfg.get("initial_capital", 100000))
        prices = self._prices.reindex(columns=weights.columns)
        weights = weights.reindex(index=prices.index, columns=prices.columns).ffill().fillna(0.0)
        valid_prices = prices.notna() & (prices > 0)
        effective_weights = weights.where(valid_prices, 0.0).fillna(0.0)
        prev_weights = effective_weights.shift(1).fillna(0.0)
        turnover = (effective_weights - prev_weights).abs().sum(axis=1)
        asset_returns = self._returns.reindex(columns=weights.columns).copy()
        sanitized_extreme_return_count = 0
        extreme_mask = asset_returns.abs() > float(self._bt_cfg.get("max_abs_daily_return", 10.0))
        if bool(extreme_mask.any().any()):
            sanitized_extreme_return_count = int(extreme_mask.sum().sum())
            if not sanitize_extreme_returns:
                raise BacktestAgentError(f"{name}: extreme daily returns detected")
        if sanitize_extreme_returns:
            if bool(extreme_mask.any().any()):
                self._warnings.append(f"{name}: zeroed {sanitized_extreme_return_count} extreme daily returns")
                asset_returns = asset_returns.mask(extreme_mask, 0.0)
        gross_returns = (prev_weights * asset_returns.fillna(0.0)).sum(axis=1)
        transaction_cost = turnover * (cost_bps / 10000.0)
        net_returns = gross_returns - transaction_cost
        n_positions = (prev_weights > 0).sum(axis=1).astype(int)
        equity, _drawdown, perf = _perf_from_returns(
            net_returns,
            initial_capital=initial_capital,
            turnover=turnover,
            costs=transaction_cost,
            n_positions=n_positions,
            annualization_days=float(self._bt_cfg.get("annualization_days", 365)),
        )
        equity_df = pd.DataFrame(
            {
                "date_ts": prices.index,
                "strategy_name": name,
                "portfolio_value": equity.values,
                "gross_return": gross_returns.values,
                "net_return": net_returns.values,
                "transaction_cost": transaction_cost.values,
                "turnover": turnover.values,
                "n_positions": n_positions.values,
                "benchmark_type": "benchmark",
                "snapshot_id": self.snapshot_id,
                "run_id": self.run_id,
            }
        )
        perf.update(
            {
                "strategy_name": name,
                "benchmark_type": "benchmark",
                "failure_reason": "" if perf["final_value"] > 0 else "portfolio_value_non_positive",
                "transaction_cost_bps": cost_bps,
                "alpha_gate_passed": None,
                "allocation_mode": "benchmark",
            }
        )
        sanity = {
            "benchmark_name": name,
            "start_date": prices.index.min().isoformat() if len(prices.index) else None,
            "end_date": prices.index.max().isoformat() if len(prices.index) else None,
            "n_days": int(len(prices.index)),
            "start_value": float(initial_capital),
            "final_value": float(perf["final_value"]),
            "total_return": float(perf["total_return"]),
            "min_daily_return": float(net_returns.min()) if len(net_returns) else float("nan"),
            "max_daily_return": float(net_returns.max()) if len(net_returns) else float("nan"),
            "max_abs_daily_return": float(net_returns.abs().max()) if len(net_returns) else float("nan"),
            "valid_price_days": int(valid_prices.all(axis=1).sum()) if not valid_prices.empty else 0,
            "average_assets_with_valid_prices": float(valid_prices.sum(axis=1).mean()) if not valid_prices.empty else 0.0,
            "min_assets_with_valid_prices": int(valid_prices.sum(axis=1).min()) if not valid_prices.empty else 0,
            "days_with_missing_held_prices": int(((effective_weights > 0) & ~valid_prices).any(axis=1).sum()) if not valid_prices.empty else 0,
            "sanitized_extreme_return_count": sanitized_extreme_return_count,
            "passed_sanity": True,
            "failure_reason": "",
        }
        return equity_df, perf, sanity

    def _rebalance_anchor_dates(self, frequency: str) -> List[pd.Timestamp]:
        dates = pd.Series(self._prices.index)
        naive = dates.dt.tz_localize(None)
        if frequency == "M":
            return dates.groupby(naive.dt.to_period("M")).min().tolist()
        if frequency == "W":
            return dates.groupby(naive.dt.to_period("W")).min().tolist()
        return dates.tolist()

    def _run_benchmarks(self) -> Tuple[List[pd.DataFrame], List[Dict[str, Any]], pd.DataFrame]:
        benchmark_equities: List[pd.DataFrame] = []
        benchmark_summaries: List[Dict[str, Any]] = []
        benchmark_sanity_rows: List[Dict[str, Any]] = []
        for sym in ["BTC", "ETH"]:
            if sym not in self._prices.columns:
                raise BacktestAgentError(f"Missing benchmark symbol {sym}")
            weights = pd.DataFrame(1.0, index=self._prices.index, columns=[sym])
            eq, summary, sanity = self._run_benchmark_weights(name=sym, weights=weights)
            benchmark_equities.append(eq)
            benchmark_summaries.append(summary)
            benchmark_sanity_rows.append(sanity)

        if not {"BTC", "ETH"}.issubset(set(self._prices.columns)):
            raise BacktestAgentError("Missing BTC or ETH benchmark price history")
        freq = self._bt_cfg.get("benchmark_rebalance_frequency", "M")
        anchors = self._rebalance_anchor_dates(freq)
        btc_eth = pd.DataFrame(np.nan, index=self._prices.index, columns=["BTC", "ETH"])
        for dt in anchors:
            btc_eth.loc[dt, ["BTC", "ETH"]] = 0.5
        valid_btc_eth = self._prices[["BTC", "ETH"]].notna().all(axis=1) & (self._prices[["BTC", "ETH"]] > 0).all(axis=1)
        btc_eth = btc_eth.where(valid_btc_eth, np.nan)
        eq, summary, sanity = self._run_benchmark_weights(name="BTC_ETH_50_50", weights=btc_eth)
        benchmark_equities.append(eq)
        benchmark_summaries.append(summary)
        benchmark_sanity_rows.append(sanity)

        ew = pd.DataFrame(np.nan, index=self._prices.index, columns=self._prices.columns)
        min_hist = 30
        for dt in anchors:
            row = self._prices.loc[dt]
            avail = []
            for sym in row[row.notna() & (row > 0)].index.tolist():
                hist = self._prices.loc[:dt, sym].dropna()
                if len(hist) < min_hist:
                    continue
                recent_ret = self._returns.loc[:dt, sym].dropna().tail(min_hist)
                if not recent_ret.empty and float(recent_ret.abs().max()) > 10.0:
                    continue
                avail.append(sym)
            if avail:
                ew.loc[dt, avail] = 1.0 / len(avail)
        eq, summary, sanity = self._run_benchmark_weights(
            name="equal_weight_universe",
            weights=ew,
            sanitize_extreme_returns=bool(self._bt_cfg.get("allow_extreme_return_sanitization", False)),
        )
        benchmark_equities.append(eq)
        benchmark_summaries.append(summary)
        benchmark_sanity_rows.append(sanity)

        cash = pd.DataFrame(0.0, index=self._prices.index, columns=["CASH"])
        eq = pd.DataFrame(
            {
                "date_ts": self._prices.index,
                "strategy_name": "cash",
                "portfolio_value": float(self._bt_cfg.get("initial_capital", 100000)),
                "gross_return": 0.0,
                "net_return": 0.0,
                "transaction_cost": 0.0,
                "turnover": 0.0,
                "n_positions": 0,
                "benchmark_type": "benchmark",
                "snapshot_id": self.snapshot_id,
                "run_id": self.run_id,
            }
        )
        cash_summary = {
            "strategy_name": "cash",
            "benchmark_type": "benchmark",
            "n_days": len(self._prices.index),
            "start_date": self._prices.index.min().isoformat(),
            "end_date": self._prices.index.max().isoformat(),
            "final_value": float(self._bt_cfg.get("initial_capital", 100000)),
            "total_return": 0.0,
            "cagr": 0.0,
            "annualized_vol": 0.0,
            "sharpe": float("nan"),
            "sortino": float("nan"),
            "calmar": float("nan"),
            "max_drawdown": 0.0,
            "average_daily_return": 0.0,
            "hit_rate": 0.0,
            "best_day": 0.0,
            "worst_day": 0.0,
            "average_turnover": 0.0,
            "annualized_turnover": 0.0,
            "total_cost_drag": 0.0,
            "average_positions": 0.0,
            "min_positions": 0,
            "max_positions": 0,
            "failure_reason": "",
            "transaction_cost_bps": 0.0,
            "alpha_gate_passed": None,
            "allocation_mode": "benchmark",
        }
        benchmark_equities.append(eq)
        benchmark_summaries.append(cash_summary)
        benchmark_sanity_rows.append(
            {
                "benchmark_name": "cash",
                "start_date": self._prices.index.min().isoformat(),
                "end_date": self._prices.index.max().isoformat(),
                "n_days": int(len(self._prices.index)),
                "start_value": float(self._bt_cfg.get("initial_capital", 100000)),
                "final_value": float(self._bt_cfg.get("initial_capital", 100000)),
                "total_return": 0.0,
                "min_daily_return": 0.0,
                "max_daily_return": 0.0,
                "max_abs_daily_return": 0.0,
                "valid_price_days": int(len(self._prices.index)),
                "average_assets_with_valid_prices": 0.0,
                "min_assets_with_valid_prices": 0,
                "days_with_missing_held_prices": 0,
                "sanitized_extreme_return_count": 0,
                "passed_sanity": True,
                "failure_reason": "",
            }
        )
        benchmark_sanity = pd.DataFrame(benchmark_sanity_rows).sort_values("benchmark_name").reset_index(drop=True)
        btc_summary = next(row for row in benchmark_summaries if row["strategy_name"] == "BTC")
        eth_summary = next(row for row in benchmark_summaries if row["strategy_name"] == "ETH")
        mix_summary = next(row for row in benchmark_summaries if row["strategy_name"] == "BTC_ETH_50_50")
        if float(btc_summary["total_return"]) < 0 and float(eth_summary["total_return"]) < 0 and float(mix_summary["total_return"]) > 0.25:
            idx = benchmark_sanity["benchmark_name"] == "BTC_ETH_50_50"
            benchmark_sanity.loc[idx, "passed_sanity"] = False
            benchmark_sanity.loc[idx, "failure_reason"] = "impossible_btc_eth_50_50_return"
        ew_idx = benchmark_sanity["benchmark_name"] == "equal_weight_universe"
        if not benchmark_sanity.loc[ew_idx].empty and float(benchmark_sanity.loc[ew_idx, "max_abs_daily_return"].iloc[0]) > 10.0:
            benchmark_sanity.loc[ew_idx, "passed_sanity"] = False
            benchmark_sanity.loc[ew_idx, "failure_reason"] = "absurd_equal_weight_daily_return"
        return benchmark_equities, benchmark_summaries, benchmark_sanity

    def _build_strategy_comparison(
        self,
        strategy_summaries: List[Dict[str, Any]],
        benchmark_summaries: List[Dict[str, Any]],
        benchmark_sanity_report: pd.DataFrame,
    ) -> pd.DataFrame:
        bench = {row["strategy_name"]: row for row in benchmark_summaries}
        rows = []
        eq = bench["equal_weight_universe"]
        btc = bench["BTC"]
        eth = bench["ETH"]
        btc_eth = bench["BTC_ETH_50_50"]
        max_allowed_drawdown = float(self._bt_cfg.get("max_allowed_drawdown", -0.80))
        allocation_mode = str(self._allocation_manifest.get("allocation_mode") or "")
        signal_gate_passed = bool(self._allocation_manifest.get("signal_gate_passed", self._allocation_manifest.get("alpha_gate_passed", False)))
        candidate_for_backtest = bool(self._allocation_manifest.get("candidate_for_backtest", self._allocation_manifest.get("alpha_gate_passed", False)))
        benchmark_sanity_passed = bool(benchmark_sanity_report["passed_sanity"].fillna(False).all()) if not benchmark_sanity_report.empty else False
        diagnostic_modes = {"diagnostic_not_live_trading", "override_diagnostic", "leaderboard_missing_diagnostic"}
        for row in strategy_summaries:
            final_value = _safe_float(row.get("final_value"))
            failed = bool(row.get("failure_reason")) or not np.isfinite(final_value) or final_value <= 0
            eligible = (
                allocation_mode == "signal_candidate_for_backtest"
                and signal_gate_passed
                and candidate_for_backtest
                and benchmark_sanity_passed
                and allocation_mode not in diagnostic_modes
            )
            alpha_status = "passed" if (
                eligible
                and not failed
                and _safe_float(row.get("sharpe")) > _safe_float(eq.get("sharpe"))
                and _safe_float(row.get("total_return")) > _safe_float(eq.get("total_return"))
                and _safe_float(row.get("max_drawdown")) >= max_allowed_drawdown
                and (
                    _safe_float(row.get("total_return")) > _safe_float(btc_eth.get("total_return"))
                    or _safe_float(row.get("sharpe")) > _safe_float(btc_eth.get("sharpe"))
                )
                and final_value > float(self._bt_cfg.get("initial_capital", 100000))
            ) else "failed"
            reasons: List[str] = []
            if allocation_mode in diagnostic_modes:
                reasons.append("diagnostic_allocation_not_alpha_eligible")
            if not signal_gate_passed:
                reasons.append("signal_gate_not_passed")
            if not candidate_for_backtest:
                reasons.append("not_candidate_for_backtest")
            if not benchmark_sanity_passed:
                reasons.append("benchmark_sanity_failed")
            if failed and row.get("failure_reason"):
                reasons.append(str(row.get("failure_reason")))
            rows.append(
                {
                    "strategy_name": row["strategy_name"],
                    "Sharpe": _safe_float(row.get("sharpe")),
                    "CAGR": _safe_float(row.get("cagr")),
                    "max_drawdown": _safe_float(row.get("max_drawdown")),
                    "total_return": _safe_float(row.get("total_return")),
                    "average_turnover": _safe_float(row.get("average_turnover")),
                    "total_cost_drag": _safe_float(row.get("total_cost_drag")),
                    "beats_btc": _safe_float(row.get("total_return")) > _safe_float(btc.get("total_return")),
                    "beats_eth": _safe_float(row.get("total_return")) > _safe_float(eth.get("total_return")),
                    "beats_btc_eth_50_50": _safe_float(row.get("total_return")) > _safe_float(btc_eth.get("total_return")),
                    "beats_equal_weight": _safe_float(row.get("total_return")) > _safe_float(eq.get("total_return")),
                    "excess_return_vs_btc": _safe_float(row.get("total_return")) - _safe_float(btc.get("total_return")),
                    "excess_return_vs_eth": _safe_float(row.get("total_return")) - _safe_float(eth.get("total_return")),
                    "excess_return_vs_equal_weight": _safe_float(row.get("total_return")) - _safe_float(eq.get("total_return")),
                    "excess_sharpe_vs_btc": _safe_float(row.get("sharpe")) - _safe_float(btc.get("sharpe")),
                    "excess_sharpe_vs_equal_weight": _safe_float(row.get("sharpe")) - _safe_float(eq.get("sharpe")),
                    "alpha_status": alpha_status,
                    "failure_reason": "; ".join(dict.fromkeys(reasons)),
                    "research_note": "; ".join(dict.fromkeys(reasons)) if reasons else "",
                }
            )
        return pd.DataFrame(rows).sort_values("strategy_name").reset_index(drop=True)

    def _run_cost_sweep(self, strategies: Iterable[str]) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        for strategy_name in strategies:
            alloc = self._allocations[self._allocations["strategy_name"] == strategy_name].copy()
            if alloc.empty:
                continue
            for cost_bps in self._bt_cfg.get("cost_sweep_bps", [0, 10, 20, 50, 100]):
                _eq, _dd, _to, summary = self._run_strategy(alloc, strategy_name=strategy_name, cost_bps=float(cost_bps))
                rows.append(
                    {
                        "strategy_name": strategy_name,
                        "cost_bps": float(cost_bps),
                        "Sharpe": _safe_float(summary.get("sharpe")),
                        "CAGR": _safe_float(summary.get("cagr")),
                        "max_drawdown": _safe_float(summary.get("max_drawdown")),
                        "total_return": _safe_float(summary.get("total_return")),
                        "final_value": _safe_float(summary.get("final_value")),
                    }
                )
        return pd.DataFrame(rows).sort_values(["strategy_name", "cost_bps"]).reset_index(drop=True)

    def _build_alpha_report(
        self,
        strategy_comparison: pd.DataFrame,
        benchmark_summaries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if strategy_comparison.empty:
            raise BacktestAgentError("No strategy comparison rows generated")
        best_sharpe = strategy_comparison.sort_values(["Sharpe", "total_return"], ascending=[False, False], na_position="last").iloc[0]
        best_return = strategy_comparison.sort_values(["total_return", "Sharpe"], ascending=[False, False], na_position="last").iloc[0]
        any_passed = bool((strategy_comparison["alpha_status"] == "passed").any())
        return {
            "allocation_mode": self._allocation_manifest.get("allocation_mode"),
            "alpha_gate_passed": self._allocation_manifest.get("alpha_gate_passed"),
            "strategies_backtested": strategy_comparison["strategy_name"].tolist(),
            "best_strategy_by_sharpe": best_sharpe["strategy_name"],
            "best_strategy_by_total_return": best_return["strategy_name"],
            "any_strategy_passed_alpha_status": any_passed,
            "diagnostic_note": (
                "Allocation source model did not pass alpha gate; backtest is diagnostic."
                if not bool(self._allocation_manifest.get("alpha_gate_passed", False))
                else ""
            ),
            "survivorship_bias_limitation": "Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
            "benchmark_rows": benchmark_summaries,
        }

    def _build_quality_report(
        self,
        summary: pd.DataFrame,
        benchmarks: pd.DataFrame,
        comparison: pd.DataFrame,
    ) -> str:
        lines = [
            "# Data Quality Backtest",
            "",
            f"- Strategies backtested: {sorted(summary['strategy_name'].tolist())}",
            f"- Allocation mode: {self._allocation_manifest.get('allocation_mode')}",
            f"- Alpha gate passed: {self._allocation_manifest.get('alpha_gate_passed')}",
            f"- Transaction cost bps: {self._bt_cfg.get('transaction_cost_bps', 20)}",
            "",
            "## Strategy Summary",
            "",
            summary[["strategy_name", "final_value", "total_return", "cagr", "sharpe", "max_drawdown", "average_turnover", "total_cost_drag"]].to_markdown(index=False),
            "",
            "## Benchmarks",
            "",
            benchmarks[["strategy_name", "total_return", "cagr", "sharpe", "max_drawdown"]].to_markdown(index=False),
            "",
            "## Alpha Status",
            "",
            comparison[["strategy_name", "alpha_status", "beats_btc", "beats_eth", "beats_btc_eth_50_50", "beats_equal_weight"]].to_markdown(index=False),
            "",
            "## Limitations",
            "",
            "- BacktestAgent evaluates realized performance from precomputed portfolio allocations. It does not create alpha by itself.",
            "- Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
        ]
        return "\n".join(lines)

    def persist(self, result: Dict[str, Any]) -> None:
        self._validate_result(result)
        bt_dir = self._output_dir
        bt_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "equity_curves.parquet": result["equity_curves"],
            "backtest_summary.parquet": result["backtest_summary"],
            "benchmark_summary.parquet": result["benchmark_summary"],
            "strategy_comparison.parquet": result["strategy_comparison"],
            "cost_sweep.parquet": result["cost_sweep"],
            "benchmark_sanity_report.parquet": result["benchmark_sanity_report"],
            "drawdown_series.parquet": result["drawdown_series"],
            "turnover_report.parquet": result["turnover_report"],
        }
        for filename, df in outputs.items():
            path = bt_dir / filename
            df.to_parquet(path, index=False)
            self.output_paths[filename.replace(".parquet", "")] = str(path)

        summary_json = bt_dir / "backtest_summary.json"
        with open(summary_json, "w") as fh:
            json.dump(result["backtest_summary"].to_dict(orient="records"), fh, indent=2, default=str)
        self.output_paths["backtest_summary_json"] = str(summary_json)

        alpha_json = bt_dir / "alpha_report.json"
        with open(alpha_json, "w") as fh:
            json.dump(result["alpha_report"], fh, indent=2, default=str)
        self.output_paths["alpha_report_json"] = str(alpha_json)

        alpha_md = bt_dir / "alpha_report.md"
        comparison = result["strategy_comparison"]
        best_sharpe = result["alpha_report"]["best_strategy_by_sharpe"]
        best_return = result["alpha_report"]["best_strategy_by_total_return"]
        alpha_md.write_text(
            "\n".join(
                [
                    "# Alpha Report",
                    "",
                    f"- Allocation mode: {self._allocation_manifest.get('allocation_mode')}",
                    f"- Alpha gate passed: {self._allocation_manifest.get('alpha_gate_passed')}",
                    f"- Best strategy by Sharpe: {best_sharpe}",
                    f"- Best strategy by total return: {best_return}",
                    f"- Any strategy passed alpha status: {result['alpha_report']['any_strategy_passed_alpha_status']}",
                    "",
                    "## Strategy Comparison",
                    "",
                    comparison.to_markdown(index=False),
                    "",
                    result["alpha_report"].get("diagnostic_note", ""),
                    "",
                    "Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.",
                ]
            ).strip()
            + "\n"
        )
        self.output_paths["alpha_report_md"] = str(alpha_md)

        manifest_path = bt_dir / "backtest_manifest.json"
        with open(manifest_path, "w") as fh:
            json.dump(result["manifest"], fh, indent=2, default=str)
        self.output_paths["backtest_manifest"] = str(manifest_path)

        quality_path = bt_dir / "data_quality_backtest.md"
        quality_path.write_text(result["data_quality_md"])
        self.output_paths["data_quality_backtest"] = str(quality_path)

    def _validate_result(self, result: Dict[str, Any]) -> None:
        required_frames = [
            "equity_curves",
            "backtest_summary",
            "benchmark_summary",
            "strategy_comparison",
            "benchmark_sanity_report",
        ]
        for key in required_frames:
            if result.get(key) is None or result[key].empty:
                raise BacktestAgentError(f"{key} is empty before persist")
        sanity = result["benchmark_sanity_report"]
        if self._bt_cfg.get("fail_on_benchmark_sanity_failure", True) and not sanity["passed_sanity"].fillna(False).all():
            raise BacktestAgentError("Benchmark sanity failure blocks backtest persistence")
        comp = result["strategy_comparison"]
        diagnostic_modes = {"diagnostic_not_live_trading", "override_diagnostic", "leaderboard_missing_diagnostic"}
        allocation_mode = str(self._allocation_manifest.get("allocation_mode") or "")
        if allocation_mode in diagnostic_modes and (comp["alpha_status"] == "passed").any():
            raise BacktestAgentError("Diagnostic allocation cannot pass alpha_status")
