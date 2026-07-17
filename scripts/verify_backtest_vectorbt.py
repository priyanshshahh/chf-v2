"""Independent vectorbt cross-check of the BacktestAgent net-return engine.

Correctness check only — `BacktestAgent` remains the SOLE alpha authority
(docs/NEXT_STEPS.md item [7]). This script never writes into data/.

What it does
------------
1. Reads the same inputs `BacktestAgent.prepare()` reads (paths taken from
   `configs/run_config.yaml -> backtesting`, with the agent's defaults as
   fallback): the allocation file and the market OHLCV file. Picks the *best*
   strategy from `data/backtests/backtest_summary.json` (highest total_return).
2. Check A — engine replication (same methodology, tolerance 1e-6 relative):
   re-derives the daily target-weight schedule and net returns exactly per the
   engine semantics in agents/backtest_agent.py::_run_strategy — weights pivot
   on execution_date, ffilled daily, zeroed on invalid prices, applied with a
   one-day shift; turnover = sum|delta target weight|; cost = turnover x
   transaction_cost_bps (20 bps); net = gross - cost; equity compounds
   (1+net).cumprod() from initial_capital — and compares total return / final
   value to backtest_summary.json.
3. Check B — vectorbt gross cross-engine (tolerance 1e-6 relative): rebuilds
   the portfolio with `vectorbt.Portfolio.from_orders` (size_type=
   'targetpercent', daily rebalance to the same effective weights, fees=0,
   cash_sharing=True, group_by=True, call_seq='auto') and compares against the
   in-house engine run with costs set to 0. With fees out of the picture the
   two engines implement the *same* portfolio (orders execute at the close of
   the schedule day; positions earn the next day's return), so this is a
   machine-precision check of the return/compounding/timing math.
4. Check C — vectorbt net (20 bps) vs backtest_summary.json: prints BOTH
   engines' total return and final value. The naive relative difference is
   NOT expected to sit within 1e-6 — or even 1% on total return — because the
   fee methodologies differ (below). PASS criterion is therefore an explicit
   fee-attribution bound: vectorbt must come out *lower* (it pays strictly
   more fees) and the final-value gap must be bounded by the measured
   fee-model delta (extra fees vectorbt paid, x1.5 compounding slack).

Fee-methodology difference (investigated, quantified — NOT a fudge)
-------------------------------------------------------------------
The in-house engine holds *weights*: it implicitly rebalances back to the
target every day but charges costs only on target-weight changes
(|delta w_target| x 20 bps of NAV). vectorbt holds *units*: daily
targetpercent orders also trade back to target after price drift, paying fees
on those drift-correction trades, with fees charged on traded value at
execution. A secondary difference: the in-house engine zeroes weights on
NaN/non-positive prices and treats missing held returns as 0, while vectorbt
requires a filled price matrix (we ffill/bfill).

Findings on the frozen run of 2026-07-05 (strategy `top_5_vol_scaled`,
414 days, 20 bps):
  - Check A: relative diff 0.0 (exact).
  - Check B (both engines, zero fees): relative diff ~3.2e-15 — the gross
    return engines are identical to machine precision; there is NO hidden
    execution-lag or compounding discrepancy.
  - Check C: in-house final value 94,545.58 vs vectorbt 93,360.06
    (gap $1,185.52, 1.25e-2 relative — would fail a naive 1% gate).
    Measured fees: vectorbt paid $3,618.74; the in-house cost drag was
    $2,214.80 in currency terms (cost_t x NAV_{t-1}). The extra $1,403.95 of
    drift-rebalancing fees fully explains (bounds) the gap; the gap is smaller
    than the extra fees because this run compounds at <1 (negative return
    path) after each fee payment.
Conclusion: the discrepancy is 100% fee-accounting methodology, not an error
in the net-return math. The in-house engine's cost model (turnover on target
changes only) is the disclosed research assumption; vectorbt's is a stricter
implementation-cost view.

Exit status: 0 if all checks PASS, 1 on any FAIL, 2 on missing inputs.

Usage:
    .venv/bin/python scripts/verify_backtest_vectorbt.py
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "run_config.yaml"
SUMMARY_PATH = PROJECT_ROOT / "data" / "backtests" / "backtest_summary.json"

# Must match agents/backtest_agent.py defaults
DEFAULT_ALLOCATION_PATH = "data/allocations/allocations_from_predictions.parquet"
DEFAULT_MARKET_PATH = "data/raw/market/market_ohlcv.parquet"
DEFAULT_COST_BPS = 20.0
DEFAULT_INITIAL_CAPITAL = 100_000.0

TOL_SAME_METHODOLOGY = 1e-6   # Checks A and B: identical semantics, must match to precision
FEE_GAP_SLACK = 1.5           # Check C: gap must be within extra-fees-paid x slack (compounding)


def _load_backtesting_cfg() -> Dict[str, Any]:
    try:
        import yaml

        with open(CONFIG_PATH, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
        return dict(cfg.get("backtesting", {}) or {})
    except Exception as exc:  # noqa: BLE001 - fall back to agent defaults
        print(f"[verify-vbt] WARNING: could not read {CONFIG_PATH} ({exc}); using defaults")
        return {}


def _rel_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


def _inhouse_equity(effective_weights, returns, cost_rate: float, initial_capital: float):
    """Replicate agents/backtest_agent.py::_run_strategy net-return equity."""
    prev_weights = effective_weights.shift(1).fillna(0.0)
    turnover = (effective_weights - prev_weights).abs().sum(axis=1)
    gross = (prev_weights * returns.fillna(0.0)).sum(axis=1)
    costs = turnover * cost_rate
    net = gross - costs
    equity = initial_capital * (1.0 + net).cumprod()
    # Currency value of the cost drag: cost_t is a fraction of NAV_{t-1}
    nav_prev = equity.shift(1).fillna(initial_capital)
    fee_currency = float((costs * nav_prev).sum())
    return equity, fee_currency


def _vbt_portfolio(prices, effective_weights, fee_rate: float, initial_capital: float):
    import vectorbt as vbt

    close_filled = prices.ffill().bfill()
    return vbt.Portfolio.from_orders(
        close=close_filled,
        size=effective_weights,
        size_type="targetpercent",
        fees=fee_rate,
        init_cash=initial_capital,
        cash_sharing=True,
        group_by=True,
        call_seq="auto",
        freq="1D",
    )


def main() -> int:
    try:
        import numpy as np  # noqa: F401
        import pandas as pd
    except ImportError as exc:
        print(f"[verify-vbt] FATAL: pandas/numpy unavailable: {exc}")
        return 2

    bt_cfg = _load_backtesting_cfg()
    cost_bps = float(bt_cfg.get("transaction_cost_bps", DEFAULT_COST_BPS))
    cost_rate = cost_bps / 10_000.0
    initial_capital = float(bt_cfg.get("initial_capital", DEFAULT_INITIAL_CAPITAL))
    allocation_path = PROJECT_ROOT / str(bt_cfg.get("allocation_path", DEFAULT_ALLOCATION_PATH))
    market_path = PROJECT_ROOT / str(bt_cfg.get("market_path", DEFAULT_MARKET_PATH))

    for path in (SUMMARY_PATH, allocation_path, market_path):
        if not path.exists():
            print(f"[verify-vbt] FATAL: required input missing: {path}")
            return 2

    with open(SUMMARY_PATH, "r") as fh:
        summary = json.load(fh)
    if not isinstance(summary, list) or not summary:
        print(f"[verify-vbt] FATAL: {SUMMARY_PATH} is empty or malformed")
        return 2

    best = max(summary, key=lambda row: float(row.get("total_return", float("-inf"))))
    strategy = str(best["strategy_name"])
    ref_total_return = float(best["total_return"])
    ref_final_value = float(best["final_value"])
    print(f"[verify-vbt] best strategy by total_return: {strategy}")
    print(f"[verify-vbt] reference (backtest_summary.json): total_return={ref_total_return:.10f} "
          f"final_value={ref_final_value:.6f} cost_bps={best.get('transaction_cost_bps', cost_bps)}")

    # ------------------------------------------------------------- load inputs
    allocations = pd.read_parquet(allocation_path)
    required_cols = {"date_ts", "execution_date", "symbol", "strategy_name", "weight"}
    missing = required_cols - set(allocations.columns)
    if missing:
        print(f"[verify-vbt] FATAL: allocation file missing columns {sorted(missing)}")
        return 2
    for col in ("date_ts", "execution_date"):
        allocations[col] = pd.to_datetime(allocations[col], utc=True).dt.normalize()

    # Price window derives from the FULL allocation file (all strategies),
    # exactly as BacktestAgent.prepare() does.
    strategy_start = allocations["date_ts"].min()
    strategy_end = allocations["date_ts"].max()

    market = pd.read_parquet(market_path)
    market["date_ts"] = pd.to_datetime(market["date_ts"], utc=True).dt.normalize()
    market = market.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
    prices_all = market.pivot_table(index="date_ts", columns="symbol", values="close", aggfunc="last").sort_index()
    prices_all = prices_all.loc[(prices_all.index >= strategy_start) & (prices_all.index <= strategy_end)]
    if prices_all.empty:
        print("[verify-vbt] FATAL: price matrix empty over allocation window")
        return 2

    alloc = allocations[allocations["strategy_name"] == strategy]
    if alloc.empty:
        print(f"[verify-vbt] FATAL: no allocation rows for strategy {strategy}")
        return 2

    # --------------------------------------------- replicate engine weight prep
    strategy_symbols = sorted(alloc["symbol"].astype(str).drop_duplicates())
    prices = prices_all.reindex(columns=strategy_symbols)
    returns = prices.pct_change(fill_method=None)
    schedule = (
        alloc.pivot_table(index="execution_date", columns="symbol", values="weight", aggfunc="sum")
        .sort_index()
        .reindex(columns=strategy_symbols)
        .fillna(0.0)
    )
    daily_target = schedule.reindex(prices.index).ffill().fillna(0.0)
    valid_prices = prices.notna() & (prices > 0)
    effective_weights = daily_target.where(valid_prices, 0.0).fillna(0.0)

    # -------------------------------------------- Check A: engine replication
    equity_net, inhouse_fee_currency = _inhouse_equity(effective_weights, returns, cost_rate, initial_capital)
    rep_final_value = float(equity_net.iloc[-1])
    rep_total_return = rep_final_value / initial_capital - 1.0

    # ------------------------- Checks B & C: vectorbt gross and net portfolios
    vbt_results: Dict[str, Tuple[Optional[float], Optional[float], str]] = {}
    equity_gross, _ = _inhouse_equity(effective_weights, returns, 0.0, initial_capital)
    gross_final_value = float(equity_gross.iloc[-1])
    vbt_fees_paid: Optional[float] = None
    for label, fee in (("gross", 0.0), ("net", cost_rate)):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pf = _vbt_portfolio(prices, effective_weights, fee, initial_capital)
                fv = float(pf.final_value())
                tr = float(pf.total_return())
                if label == "net":
                    vbt_fees_paid = float(pf.orders.fees.sum())
            vbt_results[label] = (fv, tr, "")
        except ImportError as exc:
            vbt_results[label] = (None, None, f"vectorbt unavailable: {exc}")
        except Exception as exc:  # noqa: BLE001
            vbt_results[label] = (None, None, f"vectorbt run failed: {exc}")

    # ------------------------------------------------------------------ report
    rows: List[Dict[str, Any]] = []

    diff_a = max(_rel_diff(rep_total_return, ref_total_return), _rel_diff(rep_final_value, ref_final_value))
    rows.append({
        "check": "A engine replication vs summary.json",
        "total_return": rep_total_return,
        "final_value": rep_final_value,
        "metric": diff_a,
        "criterion": f"rel_diff<={TOL_SAME_METHODOLOGY:.0e}",
        "status": "PASS" if diff_a <= TOL_SAME_METHODOLOGY else "FAIL",
    })

    fv_g, tr_g, err_g = vbt_results["gross"]
    if err_g:
        rows.append({"check": "B vbt gross (fees=0) vs in-house gross", "total_return": float("nan"),
                     "final_value": float("nan"), "metric": float("nan"),
                     "criterion": f"rel_diff<={TOL_SAME_METHODOLOGY:.0e}", "status": f"FAIL ({err_g})"})
    else:
        diff_b = _rel_diff(fv_g, gross_final_value)
        rows.append({
            "check": "B vbt gross (fees=0) vs in-house gross",
            "total_return": tr_g,
            "final_value": fv_g,
            "metric": diff_b,
            "criterion": f"rel_diff<={TOL_SAME_METHODOLOGY:.0e}",
            "status": "PASS" if diff_b <= TOL_SAME_METHODOLOGY else "FAIL",
        })

    fv_n, tr_n, err_n = vbt_results["net"]
    if err_n:
        rows.append({"check": "C vbt net (20bps) vs summary.json", "total_return": float("nan"),
                     "final_value": float("nan"), "metric": float("nan"),
                     "criterion": "fee-attribution bound", "status": f"FAIL ({err_n})"})
        fee_note = ""
    else:
        gap = ref_final_value - fv_n
        extra_fees = (vbt_fees_paid or 0.0) - inhouse_fee_currency
        # vectorbt pays fees on drift-rebalance trades too, so it must come out
        # lower, and the gap must be explained (bounded) by the extra fees paid.
        bound_ok = (gap >= -1e-6 * initial_capital) and (gap <= max(extra_fees, 0.0) * FEE_GAP_SLACK)
        rows.append({
            "check": "C vbt net (20bps) vs summary.json",
            "total_return": tr_n,
            "final_value": fv_n,
            "metric": _rel_diff(fv_n, ref_final_value),
            "criterion": f"0<=gap<=extra_fees*{FEE_GAP_SLACK:g}",
            "status": "PASS" if bound_ok else "FAIL",
        })
        fee_note = (
            f"[verify-vbt] check C fee attribution: final-value gap ${gap:,.2f} | "
            f"vbt fees paid ${vbt_fees_paid:,.2f} vs in-house cost drag ${inhouse_fee_currency:,.2f} "
            f"(extra drift-rebalance fees ${extra_fees:,.2f})\n"
            f"[verify-vbt] methodology: in-house charges 20bps only on target-weight changes; "
            f"vectorbt also pays fees on daily drift-correction trades (see module docstring)"
        )

    print(f"\n[verify-vbt] reconciliation table — strategy: {strategy} "
          f"(n_days={len(equity_net)}, cost_bps={cost_bps:g})")
    header = f"{'check':<42} {'total_return':>13} {'final_value':>13} {'rel_diff':>11} {'criterion':>26} {'status':<6}"
    print(header)
    print("-" * len(header))
    print(f"{'reference: backtest_summary.json':<42} {ref_total_return:>13.8f} {ref_final_value:>13.2f} "
          f"{'-':>11} {'-':>26} {'-':<6}")
    failed = False
    for row in rows:
        status = str(row["status"])
        if status != "PASS":
            failed = True
        print(f"{row['check']:<42} {row['total_return']:>13.8f} {row['final_value']:>13.2f} "
              f"{row['metric']:>11.3e} {row['criterion']:>26} {status:<6}")
    if fee_note:
        print()
        print(fee_note)

    if failed:
        print("\n[verify-vbt] RESULT: FAIL — engines do not reconcile within tolerance")
        return 1
    print("\n[verify-vbt] RESULT: PASS — BacktestAgent net-return engine reconciles with vectorbt")
    print("[verify-vbt] note: correctness check only; BacktestAgent remains the sole alpha authority")
    return 0


if __name__ == "__main__":
    sys.exit(main())
