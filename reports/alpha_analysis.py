from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats


ANNUALIZATION_FACTOR = 252


def _summary_lookup(summary_df: pd.DataFrame, backtest_name: str) -> Dict[str, Any]:
    match = summary_df[summary_df["backtest_name"] == backtest_name]
    if match.empty:
        return {}
    return match.iloc[0].to_dict()


def _regression_alpha(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> Dict[str, Any]:
    aligned = pd.concat(
        [
            strategy_returns.rename("strategy"),
            benchmark_returns.rename("benchmark"),
        ],
        axis=1,
    ).dropna()
    if len(aligned) < 10:
        return {
            "n_obs": int(len(aligned)),
            "alpha_daily": None,
            "alpha_annualized": None,
            "beta": None,
            "alpha_t_stat": None,
            "alpha_p_value": None,
        }

    x = aligned["benchmark"].to_numpy(dtype=float)
    y = aligned["strategy"].to_numpy(dtype=float)
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    sxx = float(np.sum((x - x_mean) ** 2))

    if sxx <= 1e-12:
        alpha_daily = y_mean
        return {
            "n_obs": int(len(aligned)),
            "alpha_daily": alpha_daily,
            "alpha_annualized": float((1 + alpha_daily) ** ANNUALIZATION_FACTOR - 1),
            "beta": 0.0,
            "alpha_t_stat": None,
            "alpha_p_value": None,
        }

    beta = float(np.sum((x - x_mean) * (y - y_mean)) / sxx)
    alpha_daily = float(y_mean - beta * x_mean)
    residuals = y - (alpha_daily + beta * x)
    dof = max(len(aligned) - 2, 1)
    sigma2 = float(np.sum(residuals**2) / dof)
    alpha_se = float(np.sqrt(sigma2 * (1 / len(aligned) + (x_mean**2 / sxx))))
    alpha_t_stat = float(alpha_daily / (alpha_se + 1e-12))
    alpha_p_value = float(2 * stats.t.sf(abs(alpha_t_stat), df=dof))

    return {
        "n_obs": int(len(aligned)),
        "alpha_daily": alpha_daily,
        "alpha_annualized": float((1 + alpha_daily) ** ANNUALIZATION_FACTOR - 1),
        "beta": beta,
        "alpha_t_stat": alpha_t_stat,
        "alpha_p_value": alpha_p_value,
    }


def _benchmark_assessment(
    strategy_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    strategy_summary: Dict[str, Any],
    benchmark_summary: Dict[str, Any],
) -> Dict[str, Any]:
    benchmark_name = str(
        benchmark_summary.get("backtest_name", benchmark_df["backtest_name"].iloc[0])
    )
    aligned = (
        strategy_df[["date_ts", "daily_return"]]
        .rename(columns={"daily_return": "strategy_return"})
        .merge(
            benchmark_df[["date_ts", "daily_return"]].rename(
                columns={"daily_return": "benchmark_return"}
            ),
            on="date_ts",
            how="inner",
        )
        .dropna()
        .sort_values("date_ts")
    )

    if aligned.empty:
        return {
            "benchmark_name": benchmark_name,
            "status": "missing_overlap",
            "verdict": f"No overlapping dates with {benchmark_name}.",
        }

    active_days = int((strategy_df["daily_return"].abs() > 1e-12).sum())
    regression = _regression_alpha(
        aligned["strategy_return"],
        aligned["benchmark_return"],
    )
    excess = aligned["strategy_return"] - aligned["benchmark_return"]
    tracking_error = float(excess.std(ddof=0) * np.sqrt(ANNUALIZATION_FACTOR))
    excess_return_ann = float(excess.mean() * ANNUALIZATION_FACTOR)
    info_ratio = float(excess_return_ann / (tracking_error + 1e-12))
    sharpe_gap = float(strategy_summary.get("sharpe", 0.0) - benchmark_summary.get("sharpe", 0.0))
    total_return_gap = float(
        strategy_summary.get("total_return", 0.0) - benchmark_summary.get("total_return", 0.0)
    )

    alpha_ann = regression.get("alpha_annualized")
    alpha_p = regression.get("alpha_p_value")
    has_positive_alpha = bool(alpha_ann is not None and alpha_ann > 0 and info_ratio > 0)
    statistically_supported = bool(has_positive_alpha and alpha_p is not None and alpha_p <= 0.10)

    if active_days == 0:
        verdict = "No trading activity in the strategy equity curve, so alpha is not measurable."
        status = "inactive_strategy"
    elif statistically_supported and sharpe_gap > 0:
        verdict = f"Positive risk-adjusted alpha versus {benchmark_name} with better Sharpe."
        status = "positive_alpha_supported"
    elif has_positive_alpha:
        verdict = f"Returns are better than {benchmark_name}, but statistical support is weak."
        status = "positive_but_weak"
    else:
        verdict = f"No convincing risk-adjusted alpha versus {benchmark_name}."
        status = "no_alpha"

    return {
        "benchmark_name": benchmark_name,
        "status": status,
        "verdict": verdict,
        "n_obs": int(len(aligned)),
        "active_strategy_days": active_days,
        "excess_return_annualized": excess_return_ann,
        "tracking_error": tracking_error,
        "information_ratio": info_ratio,
        "sharpe_gap": sharpe_gap,
        "total_return_gap": total_return_gap,
        **regression,
    }


def evaluate_risk_adjusted_alpha(
    equity_df: pd.DataFrame,
    summary_df: pd.DataFrame,
) -> Dict[str, Any]:
    strategy_df = equity_df[equity_df["backtest_name"] == "main"].copy()
    strategy_summary = _summary_lookup(summary_df, "main")

    if strategy_df.empty or not strategy_summary:
        return {
            "status": "missing_strategy",
            "overall_assessment": "Main strategy backtest is missing, so alpha cannot be evaluated.",
            "comparisons": [],
        }

    benchmark_names = ["benchmark_BTC", "benchmark_ETH", "benchmark_EW_top100"]
    comparisons: List[Dict[str, Any]] = []
    for benchmark_name in benchmark_names:
        benchmark_df = equity_df[equity_df["backtest_name"] == benchmark_name].copy()
        benchmark_summary = _summary_lookup(summary_df, benchmark_name)
        if benchmark_df.empty or not benchmark_summary:
            comparisons.append(
                {
                    "benchmark_name": benchmark_name,
                    "status": "missing_benchmark",
                    "verdict": f"{benchmark_name} is missing from backtest outputs.",
                }
            )
            continue
        comparisons.append(
            _benchmark_assessment(
                strategy_df,
                benchmark_df,
                strategy_summary,
                benchmark_summary,
            )
        )

    supported = [c for c in comparisons if c.get("status") == "positive_alpha_supported"]
    weak = [c for c in comparisons if c.get("status") == "positive_but_weak"]

    if supported and len(supported) >= 2:
        overall = "The strategy shows evidence of risk-adjusted alpha across multiple benchmarks."
    elif supported or weak:
        overall = "The strategy looks promising, but the alpha evidence is not yet strong enough to call robust."
    else:
        overall = "The strategy does not yet show convincing risk-adjusted alpha versus the configured benchmarks."

    return {
        "status": "ok",
        "overall_assessment": overall,
        "strategy_summary": strategy_summary,
        "comparisons": comparisons,
    }


def render_alpha_report_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# CHF Risk-Adjusted Alpha Report",
        "",
        f"Overall assessment: {report.get('overall_assessment', 'No assessment available.')}",
        "",
    ]

    strategy_summary = report.get("strategy_summary", {})
    if strategy_summary:
        lines.extend(
            [
                "## Strategy Summary",
                "",
                f"- Sharpe: {strategy_summary.get('sharpe', 0.0):.3f}",
                f"- CAGR: {strategy_summary.get('cagr', 0.0):.2%}",
                f"- Max drawdown: {strategy_summary.get('max_drawdown', 0.0):.2%}",
                "",
            ]
        )

    lines.append("## Benchmark Comparisons")
    lines.append("")
    for comparison in report.get("comparisons", []):
        lines.append(f"### {comparison.get('benchmark_name', 'Unknown benchmark')}")
        lines.append("")
        lines.append(f"- Verdict: {comparison.get('verdict', 'No verdict available.')}")
        if comparison.get("status") not in {"missing_benchmark", "missing_overlap"}:
            alpha_ann = comparison.get("alpha_annualized")
            alpha_t = comparison.get("alpha_t_stat")
            alpha_p = comparison.get("alpha_p_value")
            lines.append(f"- Information ratio: {comparison.get('information_ratio', 0.0):.3f}")
            lines.append(f"- Excess return annualized: {comparison.get('excess_return_annualized', 0.0):.2%}")
            lines.append(f"- Beta: {comparison.get('beta', 0.0):.3f}")
            lines.append(
                f"- Annualized alpha: {alpha_ann:.2%}"
                if alpha_ann is not None
                else "- Annualized alpha: unavailable"
            )
            lines.append(
                f"- Alpha t-stat / p-value: {alpha_t:.3f} / {alpha_p:.3f}"
                if alpha_t is not None and alpha_p is not None
                else "- Alpha t-stat / p-value: unavailable"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"
