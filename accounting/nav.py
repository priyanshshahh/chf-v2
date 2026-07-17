"""Administrator-style independent NAV for CHF paper-trading books.

Rebuilds each book's cash and positions purely from the accounting ledger
(``cash_flow`` and ``fill`` events) — never from papertrade's ``state.json``
— marks positions with a price map (explicit argument, or ``mark`` events in
the ledger), and writes a daily gross + net NAV series to
``data/accounting/nav_<book>.parquet``.

CLI::

    python -m accounting.nav [--book NAME ...] [--ledger PATH] [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import pandas as pd

from . import fees as fees_mod
from . import ledger as ledger_mod
from .audit_log import DEFAULT_AUDIT_LOG_PATH, log_event

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = Path("data/accounting")

PricesByDate = Mapping[str, Mapping[str, float]]


def _event_date(ts_utc: str) -> str:
    """Accounting date of an event: the YYYY-MM-DD prefix of ts_utc."""
    return str(ts_utc)[:10]


def _fill_cost_usd(details_json: str) -> float:
    try:
        return float(json.loads(details_json).get("cost_usd", 0.0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def ledger_marks_by_date(ledger: pd.DataFrame, book: str) -> Dict[str, Dict[str, float]]:
    """{date: {symbol: price}} from the book's ``mark`` events."""
    marks = ledger[(ledger["book"] == book) & (ledger["event_type"] == "mark")]
    out: Dict[str, Dict[str, float]] = {}
    for _, row in marks.iterrows():
        out.setdefault(_event_date(row["ts_utc"]), {})[str(row["symbol"])] = float(row["price"])
    return out


def compute_nav_series(
    ledger: pd.DataFrame,
    book: str,
    prices_by_date: Optional[PricesByDate] = None,
) -> pd.DataFrame:
    """Rebuild the book's daily gross NAV series from ledger events only.

    Cash starts at zero and moves with ``cash_flow`` events
    (``notional`` = signed USD amount) and ``fill`` events (papertrade cash
    convention: ``cash -= notional + cost_usd``). Position quantities
    accumulate signed fill ``qty``. Each date with any economic or mark
    event is valued with that date's price map; a held symbol with no price
    raises rather than fabricating a mark.

    Returns columns [date, nav, cash, positions_value, n_positions].
    """
    rows = ledger[
        (ledger["book"] == book)
        & (ledger["event_type"].isin(["cash_flow", "fill", "mark"]))
    ].copy()
    if rows.empty:
        return pd.DataFrame(columns=["date", "nav", "cash", "positions_value", "n_positions"])
    if prices_by_date is None:
        prices_by_date = ledger_marks_by_date(ledger, book)

    rows["date"] = rows["ts_utc"].map(_event_date)
    rows = rows.sort_values(["date", "ts_utc", "event_id"])

    cash = 0.0
    positions: Dict[str, float] = {}
    out: List[dict] = []
    for date, day in rows.groupby("date", sort=True):
        for _, ev in day.iterrows():
            etype = ev["event_type"]
            if etype == "cash_flow":
                cash += float(ev["notional"])
            elif etype == "fill":
                cash -= float(ev["notional"]) + _fill_cost_usd(ev["details_json"])
                symbol = str(ev["symbol"])
                new_qty = positions.get(symbol, 0.0) + float(ev["qty"])
                if abs(new_qty) < 1e-12:
                    positions.pop(symbol, None)
                else:
                    positions[symbol] = new_qty
        day_prices = prices_by_date.get(date, {})
        positions_value = 0.0
        for symbol, qty in positions.items():
            price = day_prices.get(symbol)
            if price is None or float(price) <= 0:
                raise ValueError(f"missing_price:{book}:{date}:{symbol}")
            positions_value += qty * float(price)
        out.append(
            {
                "date": date,
                "nav": cash + positions_value,
                "cash": cash,
                "positions_value": positions_value,
                "n_positions": len(positions),
            }
        )
    return pd.DataFrame(out)


def write_nav_series(
    book: str,
    nav_df: pd.DataFrame,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> Path:
    """Write the (gross + net) NAV series for a book to nav_<book>.parquet."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"nav_{book}.parquet"
    nav_df.to_parquet(path, index=False)
    return path


def build_book_nav(
    book: str,
    ledger_path: Path = ledger_mod.DEFAULT_LEDGER_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    config_path: Optional[Path] = None,
    audit_log_path: Path = DEFAULT_AUDIT_LOG_PATH,
) -> pd.DataFrame:
    """Full pipeline for one book: rebuild gross NAV, apply fees, persist."""
    config = fees_mod.load_config(config_path) if config_path else fees_mod.load_config()
    ledger = ledger_mod.read_ledger(ledger_path)
    gross = compute_nav_series(ledger, book)
    if gross.empty:
        logger.warning("accounting.nav[%s]: no ledger events; nothing to do", book)
        return gross
    full, hwm_entry = fees_mod.apply_fees(gross, book, config)
    fees_mod.update_hwm_state(book, hwm_entry, Path(config["hwm_path"]))
    fees_mod.append_fee_events(full, book, ledger_path)
    path = write_nav_series(book, full, out_dir)
    log_event(
        actor="accounting.nav",
        action="nav_series_written",
        payload={
            "book": book,
            "path": str(path),
            "n_days": int(len(full)),
            "last_date": str(full.iloc[-1]["date"]),
            "last_gross_nav": float(full.iloc[-1]["nav"]),
            "last_net_nav": float(full.iloc[-1]["net_nav"]),
        },
        log_path=audit_log_path,
    )
    return full


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Independent NAV from the accounting ledger")
    parser.add_argument("--book", action="append", default=None, help="book name (repeatable)")
    parser.add_argument("--ledger", default=str(ledger_mod.DEFAULT_LEDGER_PATH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--config", default=str(fees_mod.DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ledger_path = Path(args.ledger)
    ledger = ledger_mod.read_ledger(ledger_path)
    books = args.book or sorted(ledger["book"].unique())
    if not books:
        logger.warning("accounting.nav: ledger is empty; run accounting.reconcile first")
        return 1
    for book in books:
        series = build_book_nav(
            book,
            ledger_path=ledger_path,
            out_dir=Path(args.out_dir),
            config_path=Path(args.config),
        )
        if not series.empty:
            last = series.iloc[-1]
            logger.info(
                "accounting.nav[%s]: %s gross=%.2f net=%.2f (%d days)",
                book,
                last["date"],
                last["nav"],
                last["net_nav"],
                len(series),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
