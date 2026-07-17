"""Model decay monitor: live paper-trade returns vs backtest expectation.

Compares the canonical paper-trade book's daily NAV returns against the
matched backtest strategy's return distribution: rolling tracking error,
rolling 30d Sharpe delta and a two-sided CUSUM drift statistic (with reset).
Exit 1 when CUSUM crosses its threshold or the Sharpe delta breaches the
configured alert level.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from monitoring.common import (
    ANNUALIZATION_DAYS,
    annualized_sharpe,
    build_arg_parser,
    exit_code,
    load_config,
    read_parquet_safe,
    resolve_root,
    write_report,
)


def cusum_drift(
    z: np.ndarray, k: float = 0.5, h: float = 5.0
) -> Tuple[np.ndarray, np.ndarray, List[int]]:
    """Two-sided CUSUM on standardized deviations ``z`` with reset on alarm.

    Returns (cusum_pos, cusum_neg, alarm_indices). ``k`` is the slack and
    ``h`` the decision threshold, both in sigma units.
    """
    n = len(z)
    pos = np.zeros(n)
    neg = np.zeros(n)
    alarms: List[int] = []
    cp = cn = 0.0
    for i in range(n):
        cp = max(0.0, cp + z[i] - k)
        cn = max(0.0, cn - z[i] - k)
        pos[i], neg[i] = cp, cn
        if cp > h or cn > h:
            alarms.append(i)
            cp = cn = 0.0  # reset after an alarm
    return pos, neg, alarms


def evaluate(
    live_returns: pd.Series, expected_returns: pd.Series, cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare live daily returns against the backtest return distribution."""
    live = pd.Series(live_returns).dropna()
    expected = pd.Series(expected_returns).dropna()
    min_days = int(cfg.get("min_days", 10))
    out: Dict[str, Any] = {
        "n_live_days": int(len(live)),
        "n_backtest_days": int(len(expected)),
    }
    if len(expected) < 30:
        out["status"] = "missing_backtest_reference"
        out["alerts"] = []
        return out

    mu_bt = float(expected.mean())
    sigma_bt = float(expected.std(ddof=1))
    bt_sharpe = annualized_sharpe(expected)
    out.update(
        {
            "backtest_daily_mean": mu_bt,
            "backtest_daily_vol": sigma_bt,
            "backtest_sharpe": bt_sharpe,
        }
    )
    if len(live) < min_days or sigma_bt <= 0:
        out["status"] = "insufficient_data"
        out["alerts"] = []
        return out

    alerts: List[str] = []
    te_window = int(cfg.get("tracking_error_window", 30))
    sharpe_window = int(cfg.get("sharpe_window", 30))

    excess = live - mu_bt
    te = excess.tail(te_window).std(ddof=1) * np.sqrt(ANNUALIZATION_DAYS)
    out["tracking_error_annualized"] = float(te) if pd.notna(te) else None

    live_sharpe = annualized_sharpe(live.tail(sharpe_window))
    out["live_sharpe_30d"] = live_sharpe
    delta = None
    if live_sharpe is not None and bt_sharpe is not None:
        delta = live_sharpe - bt_sharpe
    out["sharpe_delta_30d"] = delta
    sharpe_alert_level = float(cfg.get("sharpe_delta_alert", -1.0))
    if delta is not None and delta < sharpe_alert_level:
        alerts.append(f"30d Sharpe delta {delta:.2f} < {sharpe_alert_level:.2f}")

    z = ((live - mu_bt) / sigma_bt).to_numpy(dtype=float)
    k = float(cfg.get("cusum_k", 0.5))
    h = float(cfg.get("cusum_h", 5.0))
    pos, neg, alarms = cusum_drift(z, k=k, h=h)
    out.update(
        {
            "cusum_k": k,
            "cusum_h": h,
            "cusum_pos_last": float(pos[-1]),
            "cusum_neg_last": float(neg[-1]),
            "cusum_alarm_count": len(alarms),
            "cusum_alarm_on_last_day": bool(alarms and alarms[-1] == len(z) - 1),
        }
    )
    if alarms and alarms[-1] >= len(z) - 5:
        alerts.append(
            f"CUSUM drift alarm within last 5 live days (threshold h={h}, {len(alarms)} alarm(s) total)"
        )

    out["alerts"] = alerts
    out["status"] = "alert" if alerts else "ok"
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF model decay (live vs expected) monitor").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("model_decay", {})
    book = str(cfg.get("book", "canonical_best_model"))
    strategy = str(cfg.get("backtest_strategy", "top_5_equal_weight"))

    equity = read_parquet_safe(root / paths["papertrade_dir"] / book / "equity.parquet")
    curves = read_parquet_safe(
        root / paths["equity_curves"], columns=["date_ts", "strategy_name", "net_return"]
    )

    summary: Dict[str, Any] = {"book": book, "backtest_strategy": strategy}
    live = pd.Series(dtype=float)
    if equity is not None and not equity.empty and "daily_return" in equity.columns:
        eq = equity.sort_values("date")
        live = pd.Series(eq["daily_return"].to_numpy(dtype=float), index=pd.to_datetime(eq["date"]))
        # first row of a new book carries a placeholder 0.0 return
        if len(live) > 1:
            live = live.iloc[1:]
    else:
        summary["live_input"] = "missing_or_empty_equity"

    expected = pd.Series(dtype=float)
    if curves is not None and not curves.empty:
        strat_rows = curves[curves["strategy_name"] == strategy].sort_values("date_ts")
        expected = strat_rows["net_return"].astype(float)
        if expected.empty:
            summary["backtest_input"] = f"strategy '{strategy}' not found in equity_curves"
    else:
        summary["backtest_input"] = "missing_equity_curves"

    summary.update(evaluate(live, expected, cfg))
    table = pd.DataFrame(
        {
            "date": live.index,
            "live_return": live.to_numpy(dtype=float),
        }
    )
    written = write_report(root, paths["reports_dir"], "model_decay", summary, table)
    print(
        f"model_decay: status={summary['status']} book={book} live_days={summary.get('n_live_days')} "
        f"-> {written['json']}"
    )
    for alert in summary.get("alerts", []):
        print(f"  ALERT: {alert}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
