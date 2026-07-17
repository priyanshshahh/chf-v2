"""Shadow NAV: independent recomputation of each paper-trade book's NAV.

Recomputes NAV from ``state.json`` positions x latest market-file closing
prices — deliberately sharing no code with papertrade/ — and compares it with
the book's reported ``equity.parquet`` NAV. Exit 1 when any book diverges by
more than the configured bps threshold.
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


def shadow_nav(state: Dict[str, Any], prices: pd.Series) -> Dict[str, Any]:
    """Recompute NAV = cash + sum(qty * latest close). Pure and papertrade-free."""
    cash = float(state.get("cash", 0.0))
    positions = {str(s): float(q) for s, q in (state.get("positions") or {}).items()}
    value = 0.0
    unpriced: List[str] = []
    for sym, qty in positions.items():
        px = prices.get(sym)
        if px is None or pd.isna(px):
            unpriced.append(sym)
        else:
            value += qty * float(px)
    return {
        "shadow_nav": cash + value,
        "cash": cash,
        "positions_value": value,
        "n_positions": len(positions),
        "unpriced_symbols": unpriced,
    }


def divergence_bps(shadow: float, reported: float) -> Optional[float]:
    if reported == 0:
        return None
    return (shadow - reported) / reported * 1e4


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF shadow NAV recomputation").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("shadow_nav", {})
    threshold = float(cfg.get("divergence_alert_bps", 10.0))

    market = read_parquet_safe(root / paths["market"], columns=["date_ts", "symbol", "close"])
    if market is None or market.empty:
        write_report(root, paths["reports_dir"], "shadow_nav",
                     {"status": "missing_input", "detail": paths["market"], "alerts": []})
        print("shadow_nav: market data missing; cannot recompute NAVs")
        return 0
    market = market.dropna(subset=["close"]).sort_values("date_ts")
    prices = market.groupby("symbol")["close"].last()

    alerts: List[str] = []
    books_out: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    for book in list_papertrade_books(root, paths["papertrade_dir"]):
        book_dir = root / paths["papertrade_dir"] / book
        state = read_json_safe(book_dir / "state.json")
        if not isinstance(state, dict):
            books_out[book] = {"status": "missing_state"}
            continue
        entry = shadow_nav(state, prices)
        equity = read_parquet_safe(book_dir / "equity.parquet")
        if equity is not None and not equity.empty and "nav" in equity.columns:
            eq = equity.sort_values("date")
            reported = float(eq["nav"].iloc[-1])
            entry["reported_nav"] = reported
            entry["reported_nav_date"] = str(eq["date"].iloc[-1])
            div = divergence_bps(entry["shadow_nav"], reported)
            entry["divergence_bps"] = div
            if entry["unpriced_symbols"]:
                entry["status"] = "unpriced_positions"
            elif div is not None and abs(div) > threshold:
                entry["status"] = "diverged"
                alerts.append(f"{book}: shadow NAV diverges {div:+.1f} bps (>|{threshold}|)")
            else:
                entry["status"] = "ok"
        else:
            entry["reported_nav"] = None
            entry["divergence_bps"] = None
            entry["status"] = "no_equity_history"
        books_out[book] = entry
        rows.append(
            {
                "book": book,
                "shadow_nav": entry["shadow_nav"],
                "reported_nav": entry.get("reported_nav"),
                "divergence_bps": entry.get("divergence_bps"),
                "n_positions": entry["n_positions"],
                "n_unpriced": len(entry["unpriced_symbols"]),
                "status": entry["status"],
            }
        )

    summary = {
        "status": "alert" if alerts else "ok",
        "divergence_alert_bps": threshold,
        "as_of_market_date": str(market["date_ts"].max().date()),
        "alerts": alerts,
        "books": books_out,
    }
    written = write_report(root, paths["reports_dir"], "shadow_nav", summary, pd.DataFrame(rows))
    print(f"shadow_nav: status={summary['status']} books={len(books_out)} -> {written['json']}")
    for book, entry in books_out.items():
        print(f"  {book}: shadow={entry.get('shadow_nav')} reported={entry.get('reported_nav')} "
              f"div_bps={entry.get('divergence_bps')} [{entry.get('status')}]")
    for alert in alerts:
        print(f"  ALERT: {alert}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
