"""Execution quality monitor: turnover and realized cost drag.

Planned turnover comes from consecutive allocation snapshots (canonical
allocation parquet); live turnover and cost drag come from each book's
papertrade fills. Live per-rebalance turnover is compared to the matched
backtest strategy's turnover assumptions; exit 1 on >= configured creep
multiple (initial deployment fills are excluded from the creep check).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from monitoring.common import (
    build_arg_parser,
    exit_code,
    list_papertrade_books,
    load_config,
    read_json_safe,
    read_parquet_safe,
    resolve_root,
    write_report,
)


def allocation_turnover(alloc: pd.DataFrame) -> pd.DataFrame:
    """Per-rebalance one-sided turnover 0.5*sum(|dw|) from allocation snapshots."""
    wide = (
        alloc.pivot_table(index="date_ts", columns="symbol", values="weight", aggfunc="sum")
        .fillna(0.0)
        .sort_index()
    )
    if len(wide) < 2:
        return pd.DataFrame(columns=["date_ts", "turnover"])
    turn = wide.diff().abs().sum(axis=1).iloc[1:] * 0.5
    return pd.DataFrame({"date_ts": turn.index, "turnover": turn.to_numpy(dtype=float)})


def fills_turnover(fills: pd.DataFrame, nav: float) -> pd.DataFrame:
    """Per-rebalance-date live turnover 0.5*sum(|notional|)/NAV from fills."""
    if fills is None or fills.empty or nav <= 0:
        return pd.DataFrame(columns=["date", "turnover", "notional", "cost_usd"])
    grouped = fills.groupby("date").agg(
        notional=("notional", lambda s: s.abs().sum()),
        cost_usd=("cost_usd", "sum"),
    )
    grouped["turnover"] = grouped["notional"] * 0.5 / nav
    return grouped.reset_index()


def backtest_rebalance_turnover(turnover_report: pd.DataFrame, strategy: str) -> Optional[float]:
    """Mean per-rebalance (non-zero) turnover assumed by the backtest."""
    rows = turnover_report[turnover_report["strategy_name"] == strategy]
    active = rows[rows["turnover"] > 0]["turnover"]
    if active.empty:
        return None
    return float(active.mean())


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF execution quality monitor").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("execution_quality", {})
    creep_mult = float(cfg.get("turnover_creep_mult", 2.0))
    strategy_map: Dict[str, str] = cfg.get("book_strategy_map", {}) or {}

    alloc = read_parquet_safe(root / paths["allocations"], columns=["date_ts", "symbol", "weight"])
    planned = allocation_turnover(alloc) if alloc is not None and not alloc.empty else pd.DataFrame()
    turnover_report = read_parquet_safe(
        root / paths["turnover_report"], columns=["date_ts", "strategy_name", "turnover"]
    )

    alerts: List[str] = []
    books_out: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    for book in list_papertrade_books(root, paths["papertrade_dir"]):
        book_dir = root / paths["papertrade_dir"] / book
        fills = read_parquet_safe(book_dir / "fills.parquet")
        equity = read_parquet_safe(book_dir / "equity.parquet")
        nav = float(equity.sort_values("date")["nav"].iloc[-1]) if equity is not None and not equity.empty else None
        entry: Dict[str, Any] = {"nav": nav}
        if fills is None or fills.empty or nav is None:
            entry["status"] = "no_fills_or_nav"
            books_out[book] = entry
            continue
        live = fills_turnover(fills, nav)
        total_cost = float(fills["cost_usd"].sum())
        total_notional = float(fills["notional"].abs().sum())
        implied_bps = total_cost / total_notional * 1e4 if total_notional > 0 else None
        entry.update(
            {
                "n_fill_dates": int(len(live)),
                "total_cost_usd": total_cost,
                "total_notional_usd": total_notional,
                "implied_cost_bps": implied_bps,
                "realized_cost_drag": total_cost / nav,
            }
        )
        # exclude the earliest fill date: initial 0->full deployment is ~100% turnover
        comparable = live.sort_values("date").iloc[1:]
        entry["initial_deployment_date"] = str(live.sort_values("date")["date"].iloc[0])
        strategy = strategy_map.get(book)
        assumed = (
            backtest_rebalance_turnover(turnover_report, strategy)
            if strategy and turnover_report is not None and not turnover_report.empty
            else None
        )
        entry["backtest_strategy"] = strategy
        entry["backtest_rebalance_turnover"] = assumed
        if len(comparable) >= int(cfg.get("min_comparable_rebalances", 1)) and assumed:
            live_mean = float(comparable["turnover"].mean())
            entry["live_rebalance_turnover"] = live_mean
            entry["turnover_ratio"] = live_mean / assumed
            if live_mean > creep_mult * assumed:
                alerts.append(
                    f"{book}: live rebalance turnover {live_mean:.3f} > {creep_mult}x backtest {assumed:.3f}"
                )
        else:
            entry["live_rebalance_turnover"] = None
            entry["turnover_ratio"] = None
        entry["status"] = "ok"
        books_out[book] = entry
        rows.append({"book": book, **{k: v for k, v in entry.items() if not isinstance(v, (dict, list))}})

    summary = {
        "status": "alert" if alerts else "ok",
        "turnover_creep_mult": creep_mult,
        "planned_rebalances": int(len(planned)),
        "planned_mean_turnover": float(planned["turnover"].mean()) if len(planned) else None,
        "alerts": alerts,
        "books": books_out,
    }
    written = write_report(root, paths["reports_dir"], "execution_quality", summary, pd.DataFrame(rows))
    print(f"execution_quality: status={summary['status']} books={len(books_out)} -> {written['json']}")
    for alert in alerts:
        print(f"  ALERT: {alert}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
