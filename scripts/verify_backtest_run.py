#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config


def _merge(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section:
        return cfg
    merged = dict(cfg)
    base_key = "backtesting" if section.startswith("backtesting") else section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def _resolve_output_dir(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get("backtesting", {}).get("output_dir", "data/backtests")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cfg["_project_root"]) / path
    return path


def validate_backtest_outputs(cfg: Dict[str, Any]) -> List[str]:
    btcfg = cfg.get("backtesting", {})
    out_dir = _resolve_output_dir(cfg)
    files = {
        "equity_curves": out_dir / "equity_curves.parquet",
        "backtest_summary": out_dir / "backtest_summary.parquet",
        "benchmark_summary": out_dir / "benchmark_summary.parquet",
        "strategy_comparison": out_dir / "strategy_comparison.parquet",
        "cost_sweep": out_dir / "cost_sweep.parquet",
        "benchmark_sanity_report": out_dir / "benchmark_sanity_report.parquet",
        "drawdown_series": out_dir / "drawdown_series.parquet",
        "turnover_report": out_dir / "turnover_report.parquet",
        "alpha_report_json": out_dir / "alpha_report.json",
        "alpha_report_md": out_dir / "alpha_report.md",
        "manifest": out_dir / "backtest_manifest.json",
    }
    failures: List[str] = []
    for path in files.values():
        if not path.exists():
            failures.append(f"FAIL: missing {path.name}")
    if failures:
        return failures

    eq = pd.read_parquet(files["equity_curves"])
    summary = pd.read_parquet(files["backtest_summary"])
    bench = pd.read_parquet(files["benchmark_summary"])
    comp = pd.read_parquet(files["strategy_comparison"])
    cost = pd.read_parquet(files["cost_sweep"])
    sanity = pd.read_parquet(files["benchmark_sanity_report"])
    drawdown = pd.read_parquet(files["drawdown_series"])
    turnover = pd.read_parquet(files["turnover_report"])
    with open(files["manifest"], "r") as fh:
        manifest = json.load(fh)

    for name, df in {
        "equity_curves": eq,
        "backtest_summary": summary,
        "benchmark_summary": bench,
        "strategy_comparison": comp,
        "cost_sweep": cost,
        "benchmark_sanity_report": sanity,
        "drawdown_series": drawdown,
        "turnover_report": turnover,
    }.items():
        if df.empty:
            failures.append(f"FAIL: {name}.parquet is empty")

    required_eq = {
        "date_ts",
        "strategy_name",
        "portfolio_value",
        "gross_return",
        "net_return",
        "transaction_cost",
        "turnover",
        "n_positions",
        "benchmark_type",
        "snapshot_id",
        "run_id",
    }
    required_summary = {
        "strategy_name",
        "final_value",
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "failure_reason",
    }
    required_comp = {
        "strategy_name",
        "Sharpe",
        "CAGR",
        "max_drawdown",
        "total_return",
        "beats_btc",
        "beats_eth",
        "beats_btc_eth_50_50",
        "beats_equal_weight",
        "alpha_status",
    }
    required_sanity = {
        "benchmark_name",
        "start_date",
        "end_date",
        "n_days",
        "start_value",
        "final_value",
        "total_return",
        "min_daily_return",
        "max_daily_return",
        "max_abs_daily_return",
        "valid_price_days",
        "passed_sanity",
        "failure_reason",
    }
    for col in sorted(required_eq - set(eq.columns)):
        failures.append(f"FAIL: equity_curves.parquet missing required column {col}")
    for col in sorted(required_summary - set(summary.columns)):
        failures.append(f"FAIL: backtest_summary.parquet missing required column {col}")
    for col in sorted(required_comp - set(comp.columns)):
        failures.append(f"FAIL: strategy_comparison.parquet missing required column {col}")
    for col in sorted(required_sanity - set(sanity.columns)):
        failures.append(f"FAIL: benchmark_sanity_report.parquet missing required column {col}")
    if failures:
        return failures

    if eq.duplicated(["date_ts", "strategy_name"]).any():
        failures.append("FAIL: duplicate date_ts + strategy_name rows in equity curves")
    for col in ["portfolio_value", "gross_return", "net_return", "transaction_cost", "turnover"]:
        numeric = pd.to_numeric(eq[col], errors="coerce")
        if numeric.isna().any() or (~np.isfinite(numeric)).any():
            failures.append(f"FAIL: {col} contains null or non-finite values")
    if (pd.to_numeric(eq["transaction_cost"], errors="coerce") < -1e-12).any():
        failures.append("FAIL: transaction_cost < 0")
    if (pd.to_numeric(eq["turnover"], errors="coerce") < -1e-12).any():
        failures.append("FAIL: turnover < 0")
    if (pd.to_numeric(eq["n_positions"], errors="coerce") < -1e-12).any():
        failures.append("FAIL: n_positions < 0")
    for col in ["sharpe", "cagr", "max_drawdown", "total_return"]:
        if col not in summary.columns:
            failures.append(f"FAIL: summary missing {col}")
    final_vals = pd.to_numeric(summary["final_value"], errors="coerce")
    if ((final_vals <= 0) & summary["failure_reason"].fillna("").eq("")).any():
        failures.append("FAIL: strategy with final_value <= 0 lacks failure_reason")
    dd = pd.to_numeric(summary["max_drawdown"], errors="coerce")
    if (dd < -1.0 - 1e-9).any():
        failures.append("FAIL: max_drawdown < -1.0")
    if (dd > 1e-9).any():
        failures.append("FAIL: max_drawdown > 0")
    required_bench = {"BTC", "ETH", "BTC_ETH_50_50", "equal_weight_universe"}
    missing_bench = required_bench - set(bench["strategy_name"].astype(str))
    for name in sorted(missing_bench):
        failures.append(f"FAIL: missing benchmark {name}")
    bench_subset = bench[bench["strategy_name"].isin(["BTC", "ETH", "BTC_ETH_50_50"])]
    if not bench_subset.empty:
        start_dates = set(bench_subset["start_date"].astype(str))
        end_dates = set(bench_subset["end_date"].astype(str))
        if len(start_dates) > 1 or len(end_dates) > 1:
            failures.append("FAIL: BTC, ETH, and BTC_ETH_50_50 have inconsistent date ranges")
    btc_row = bench[bench["strategy_name"] == "BTC"]
    eth_row = bench[bench["strategy_name"] == "ETH"]
    mix_row = bench[bench["strategy_name"] == "BTC_ETH_50_50"]
    if not btc_row.empty and not eth_row.empty and not mix_row.empty:
        btc_ret = float(btc_row.iloc[0]["total_return"])
        eth_ret = float(eth_row.iloc[0]["total_return"])
        mix_ret = float(mix_row.iloc[0]["total_return"])
        if btc_ret < 0 and eth_ret < 0 and mix_ret > 0.25:
            failures.append("FAIL: BTC_ETH_50_50 return is impossible relative to BTC and ETH over same window")
    ew_sanity = sanity[sanity["benchmark_name"] == "equal_weight_universe"]
    if not ew_sanity.empty and float(ew_sanity.iloc[0]["max_abs_daily_return"]) > 10.0:
        failures.append("FAIL: equal_weight_universe has absurd daily return magnitude")
    if not sanity["passed_sanity"].fillna(False).all():
        failures.append("FAIL: benchmark_sanity_report contains failed sanity rows")
    expected_costs = {float(x) for x in btcfg.get("cost_sweep_bps", [0, 10, 20, 50, 100])}
    actual_costs = {float(x) for x in pd.to_numeric(cost["cost_bps"], errors="coerce").dropna().unique()}
    if expected_costs - actual_costs:
        failures.append("FAIL: cost_sweep missing configured cost levels")
    if manifest.get("strategies_backtested") and not set(manifest["strategies_backtested"]).issubset(set(summary["strategy_name"])):
        failures.append("FAIL: manifest strategies_backtested does not match summary strategies")
    if "same_day_lookahead" in eq.columns and eq["same_day_lookahead"].fillna(False).any():
        failures.append("FAIL: strategy has same-day lookahead recorded")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    parser.add_argument("--section", default="backtesting")
    args = parser.parse_args()
    cfg = _merge(load_config(Path(args.config)), args.section)
    failures = validate_backtest_outputs(cfg)
    if failures:
        print("Backtest validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Backtest validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
