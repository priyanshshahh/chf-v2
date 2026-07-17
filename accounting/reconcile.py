"""Dual-book reconciliation: papertrade engine vs independent shadow NAV.

The institutional control this module implements: import every papertrade
fill into the append-only accounting ledger (idempotent), rebuild each
book's NAV purely from the ledger with independently sourced marks, and
compare it to the engine's own ``equity.parquet`` NAV within a tolerance
(default 1 bp). Breaks are classified as ``missing_fill``,
``price_mismatch``, ``cash_drift``, or ``unknown``.

Mark prices are sourced offline, in order of preference:

1. the book's own fill prices on that date (exact on rebalance days),
2. cached CoinGecko market snapshots (``data/cache/coingecko/markets_<YYYYMMDD>_*.json``),
3. daily closes in ``data/external/coingecko_prices.parquet``.

CLI::

    python -m accounting.reconcile [--books A,B] [--tolerance-bps X]
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from . import fees as fees_mod
from . import ledger as ledger_mod
from . import nav as nav_mod
from .audit_log import DEFAULT_AUDIT_LOG_PATH, log_event

logger = logging.getLogger(__name__)

DEFAULT_PAPERTRADE_DIR = Path("data/papertrade")
DEFAULT_OUT_DIR = Path("data/accounting")
DEFAULT_RUN_CONFIG_PATH = Path("configs/run_config.yaml")
COINGECKO_CACHE_DIR = Path("data/cache/coingecko")
EXTERNAL_PRICES_PATH = Path("data/external/coingecko_prices.parquet")
REPORT_FILENAME = "reconciliation_report.json"
DEFAULT_STARTING_CASH = 100_000.0

BREAK_CLASSES = ("missing_fill", "price_mismatch", "cash_drift", "unknown")


def discover_books(papertrade_dir: Path = DEFAULT_PAPERTRADE_DIR) -> List[str]:
    """Book directories that have both an equity curve and a fills log."""
    root = Path(papertrade_dir)
    if not root.exists():
        return []
    return sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "equity.parquet").exists() and (d / "fills.parquet").exists()
    )


def _starting_cash(book: str, run_config_path: Path = DEFAULT_RUN_CONFIG_PATH) -> float:
    """Contributed capital per book from the papertrade config (read-only)."""
    path = Path(run_config_path)
    if path.exists():
        try:
            cfg = yaml.safe_load(path.read_text()) or {}
            for raw in (cfg.get("papertrade") or {}).get("books") or []:
                if str(raw.get("name")) == book:
                    return float(raw.get("starting_cash", DEFAULT_STARTING_CASH))
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            logger.warning("accounting.reconcile: could not read %s", path)
    return DEFAULT_STARTING_CASH


# -- independent mark sources ----------------------------------------------------


def _cached_coingecko_prices(date: str, cache_dir: Path = COINGECKO_CACHE_DIR) -> Dict[str, float]:
    """USD prices from cached CoinGecko market snapshots for one date.

    Multiple snapshot files can exist per day (different query params);
    merge smallest-first so the largest (top-N markets) snapshot wins.
    """
    day = date.replace("-", "")
    files = sorted(Path(cache_dir).glob(f"markets_{day}_*.json")) if cache_dir else []
    parsed: List[List[dict]] = []
    for f in files:
        try:
            rows = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rows, list):
            parsed.append([r for r in rows if isinstance(r, dict)])
    out: Dict[str, float] = {}
    for rows in sorted(parsed, key=len):
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            price = row.get("price_usd", row.get("current_price"))
            if symbol and price is not None and float(price) > 0:
                out[symbol] = float(price)
    return out


def _external_close_prices(
    date: str, external_path: Path = EXTERNAL_PRICES_PATH
) -> Dict[str, float]:
    """USD closes from the external daily price parquet for one date."""
    path = Path(external_path)
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    day = df[df["date"].astype(str) == date]
    out: Dict[str, float] = {}
    for _, row in day.iterrows():
        price = float(row["close"])
        if price > 0:
            out[str(row["symbol"]).upper()] = price
    return out


# -- ledger import ---------------------------------------------------------------


def import_book(
    book: str,
    book_dir: Path,
    ledger_path: Path = ledger_mod.DEFAULT_LEDGER_PATH,
    run_config_path: Path = DEFAULT_RUN_CONFIG_PATH,
    coingecko_cache_dir: Path = COINGECKO_CACHE_DIR,
    external_prices_path: Path = EXTERNAL_PRICES_PATH,
) -> int:
    """Import one papertrade book into the ledger (idempotent).

    Emits: one initial-funding ``cash_flow`` event, one ``fill`` event per
    fills.parquet row, and one ``mark`` event per (equity date, held symbol)
    using the independent price waterfall. Returns rows appended.
    """
    book_dir = Path(book_dir)
    fills = pd.read_parquet(book_dir / "fills.parquet").sort_values(["date", "symbol"])
    equity = pd.read_parquet(book_dir / "equity.parquet").sort_values("date")
    first_date = min(
        [str(equity.iloc[0]["date"])] + ([str(fills.iloc[0]["date"])] if len(fills) else [])
    )
    starting_cash = _starting_cash(book, run_config_path)

    events: List[ledger_mod.LedgerEvent] = [
        ledger_mod.LedgerEvent(
            event_id=ledger_mod.make_event_id("cash_flow", book, "initial_funding"),
            ts_utc=f"{first_date}T00:00:00+00:00",
            book=book,
            event_type="cash_flow",
            notional=float(starting_cash),
            details_json=ledger_mod.details(reason="initial_funding", source="run_config"),
        )
    ]
    for _, f in fills.iterrows():
        date = str(f["date"])
        events.append(
            ledger_mod.LedgerEvent(
                event_id=ledger_mod.make_event_id(
                    "fill", book, date, f["symbol"], f["side"], f"{float(f['qty']):.12f}"
                ),
                ts_utc=f"{date}T00:00:00+00:00",
                book=book,
                event_type="fill",
                symbol=str(f["symbol"]).upper(),
                qty=float(f["qty"]),
                price=float(f["price"]),
                notional=float(f["notional"]),
                details_json=ledger_mod.details(
                    side=str(f["side"]),
                    cost_usd=float(f["cost_usd"]),
                    reason=str(f.get("reason", "")),
                ),
            )
        )

    # Mark events: rebuild held symbols date by date, then price each date
    # via the independent waterfall (fill prices -> cache -> external closes).
    positions: Dict[str, float] = {}
    fill_dates = fills.groupby("date") if len(fills) else []
    fills_by_date = {str(d): g for d, g in fill_dates}
    for date in [str(d) for d in equity["date"]]:
        day_fills = fills_by_date.get(date)
        fill_prices: Dict[str, float] = {}
        if day_fills is not None:
            for _, f in day_fills.iterrows():
                symbol = str(f["symbol"]).upper()
                new_qty = positions.get(symbol, 0.0) + float(f["qty"])
                if abs(new_qty) < 1e-12:
                    positions.pop(symbol, None)
                else:
                    positions[symbol] = new_qty
                fill_prices[symbol] = float(f["price"])
        cached: Optional[Dict[str, float]] = None
        external: Optional[Dict[str, float]] = None
        for symbol in sorted(positions):
            price = fill_prices.get(symbol)
            source = "fill"
            if price is None:
                if cached is None:
                    cached = _cached_coingecko_prices(date, coingecko_cache_dir)
                price = cached.get(symbol)
                source = "coingecko_cache"
            if price is None:
                if external is None:
                    external = _external_close_prices(date, external_prices_path)
                price = external.get(symbol)
                source = "external_close"
            if price is None:
                logger.warning(
                    "accounting.reconcile[%s]: no independent mark for %s on %s",
                    book,
                    symbol,
                    date,
                )
                continue
            events.append(
                ledger_mod.LedgerEvent(
                    event_id=ledger_mod.make_event_id("mark", book, date, symbol),
                    ts_utc=f"{date}T23:59:59+00:00",
                    book=book,
                    event_type="mark",
                    symbol=symbol,
                    price=float(price),
                    details_json=ledger_mod.details(source=source),
                )
            )
    return ledger_mod.append(events, ledger_path)


# -- reconciliation --------------------------------------------------------------


def _classify_break(
    cash_bad: bool,
    positions_value_bad: bool,
) -> str:
    """Map which NAV leg diverges to a break class.

    A missed fill moves both cash and holdings; a bad mark moves only the
    positions value; unexplained cash movement with matching holdings is
    cash drift.
    """
    if cash_bad and positions_value_bad:
        return "missing_fill"
    if positions_value_bad:
        return "price_mismatch"
    if cash_bad:
        return "cash_drift"
    return "unknown"


def reconcile_book(
    book: str,
    book_dir: Path,
    ledger: pd.DataFrame,
    tolerance_bps: float = 1.0,
) -> dict:
    """Compare the shadow (ledger) NAV with the engine's equity curve."""
    engine = pd.read_parquet(Path(book_dir) / "equity.parquet").sort_values("date")
    try:
        shadow = nav_mod.compute_nav_series(ledger, book)
    except ValueError as exc:
        return {
            "date": str(engine.iloc[-1]["date"]) if len(engine) else None,
            "engine_nav": float(engine.iloc[-1]["nav"]) if len(engine) else None,
            "shadow_nav": None,
            "divergence_bps": None,
            "status": "BREAK",
            "break_classification": "unknown",
            "error": str(exc),
        }
    shadow = shadow.set_index("date")

    daily: List[dict] = []
    for _, row in engine.iterrows():
        date = str(row["date"])
        engine_nav = float(row["nav"])
        if date not in shadow.index:
            daily.append(
                {
                    "date": date,
                    "engine_nav": engine_nav,
                    "shadow_nav": None,
                    "divergence_bps": None,
                    "status": "BREAK",
                    "break_classification": "missing_fill",
                }
            )
            continue
        srow = shadow.loc[date]
        shadow_nav = float(srow["nav"])
        divergence_bps = abs(shadow_nav - engine_nav) / engine_nav * 1e4 if engine_nav else 0.0
        tol_usd = abs(engine_nav) * tolerance_bps / 1e4
        if divergence_bps <= tolerance_bps:
            status, break_class = "OK", None
        else:
            status = "BREAK"
            break_class = _classify_break(
                cash_bad=abs(float(srow["cash"]) - float(row["cash"])) > tol_usd,
                positions_value_bad=abs(
                    float(srow["positions_value"]) - float(row["positions_value"])
                )
                > tol_usd,
            )
        daily.append(
            {
                "date": date,
                "engine_nav": engine_nav,
                "shadow_nav": shadow_nav,
                "divergence_bps": divergence_bps,
                "status": status,
                "break_classification": break_class,
            }
        )

    worst = max(
        daily,
        key=lambda d: (d["status"] == "BREAK", d["divergence_bps"] or 0.0),
    )
    latest = daily[-1]
    summary = dict(latest if latest["status"] == "BREAK" or worst["status"] == "OK" else worst)
    summary["days_checked"] = len(daily)
    summary["days_ok"] = sum(1 for d in daily if d["status"] == "OK")
    summary["max_divergence_bps"] = max((d["divergence_bps"] or 0.0) for d in daily)
    summary["daily"] = daily
    return summary


def run_reconciliation(
    books: Optional[List[str]] = None,
    papertrade_dir: Path = DEFAULT_PAPERTRADE_DIR,
    ledger_path: Path = ledger_mod.DEFAULT_LEDGER_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    config_path: Path = fees_mod.DEFAULT_CONFIG_PATH,
    run_config_path: Path = DEFAULT_RUN_CONFIG_PATH,
    coingecko_cache_dir: Path = COINGECKO_CACHE_DIR,
    external_prices_path: Path = EXTERNAL_PRICES_PATH,
    audit_log_path: Path = DEFAULT_AUDIT_LOG_PATH,
) -> dict:
    """Import all books into the ledger, reconcile, and write the report."""
    config = fees_mod.load_config(config_path)
    tolerance_bps = float(config["tolerance_bps"])
    papertrade_dir = Path(papertrade_dir)
    books = books or discover_books(papertrade_dir)
    report: dict = {}
    for book in books:
        book_dir = papertrade_dir / book
        appended = import_book(
            book,
            book_dir,
            ledger_path=ledger_path,
            run_config_path=run_config_path,
            coingecko_cache_dir=coingecko_cache_dir,
            external_prices_path=external_prices_path,
        )
        ledger = ledger_mod.read_ledger(ledger_path)
        result = reconcile_book(book, book_dir, ledger, tolerance_bps=tolerance_bps)
        result["ledger_rows_appended"] = appended
        report[book] = result
        logger.info(
            "accounting.reconcile[%s]: %s divergence=%.4f bps (%d/%d days OK)",
            book,
            result["status"],
            result.get("max_divergence_bps") or 0.0,
            result.get("days_ok", 0),
            result.get("days_checked", 0),
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / REPORT_FILENAME
    payload = dict(report)
    payload["_meta"] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "tolerance_bps": tolerance_bps,
        "books": books,
    }
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    log_event(
        actor="accounting.reconcile",
        action="reconciliation_run",
        payload={
            book: {
                "status": r["status"],
                "divergence_bps": r.get("divergence_bps"),
                "break_classification": r.get("break_classification"),
            }
            for book, r in report.items()
        },
        log_path=audit_log_path,
    )
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-book NAV reconciliation")
    parser.add_argument("--books", default=None, help="comma-separated book names")
    parser.add_argument("--papertrade-dir", default=str(DEFAULT_PAPERTRADE_DIR))
    parser.add_argument("--ledger", default=str(ledger_mod.DEFAULT_LEDGER_PATH))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--config", default=str(fees_mod.DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    books = [b.strip() for b in args.books.split(",") if b.strip()] if args.books else None
    payload = run_reconciliation(
        books=books,
        papertrade_dir=Path(args.papertrade_dir),
        ledger_path=Path(args.ledger),
        out_dir=Path(args.out_dir),
        config_path=Path(args.config),
    )
    breaks = [b for b, r in payload.items() if b != "_meta" and r["status"] != "OK"]
    if breaks:
        logger.error("accounting.reconcile: BREAK in %s", ",".join(breaks))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
