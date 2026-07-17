"""Daily paper-trading engine for CHF.

Reads the ``papertrade:`` section of configs/run_config.yaml, maintains one
simulated book per configured strategy, fetches reference prices once per run,
and appends idempotent daily equity / fills rows under
``data/papertrade/<book_name>/``.

Nothing here places real orders: the engine drives InternalSimulatorBroker
instances only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from .broker import Fill
from .funding import DEFAULT_DERIVATIVES_PATH, daily_funding_rates
from .prices import fetch_prices
from .simulator import InternalSimulatorBroker

logger = logging.getLogger(__name__)

DEFAULT_COST_BPS = 20.0
DEFAULT_STARTING_CASH = 100_000.0
DEFAULT_OUTPUT_DIR = "data/papertrade"
WEIGHTS_HASH_FILENAME = "weights_hash.json"
EQUITY_FILENAME = "equity.parquet"
FILLS_FILENAME = "fills.parquet"
MANIFEST_FILENAME = "papertrade_manifest.json"
VALID_REBALANCE = ("daily", "weekly", "on_change")


@dataclass
class BookConfig:
    """One configured paper-trading book."""

    name: str
    weights_path: Optional[str] = None
    static_weights: Optional[Dict[str, float]] = None
    strategy_name: Optional[str] = None
    rebalance: str = "weekly"
    cost_bps: float = DEFAULT_COST_BPS
    starting_cash: float = DEFAULT_STARTING_CASH
    allow_short: bool = False
    margin_requirement: Optional[float] = None


def load_book_configs(cfg: dict) -> List[BookConfig]:
    """Parse the ``papertrade:`` section of the run config into BookConfigs."""
    section = cfg.get("papertrade") or {}
    raw_books = section.get("books") or []
    books: List[BookConfig] = []
    seen = set()
    for raw in raw_books:
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("papertrade book missing 'name'")
        if name in seen:
            raise ValueError(f"duplicate papertrade book name: {name}")
        seen.add(name)
        weights_path = raw.get("weights_path")
        static_weights = raw.get("static_weights")
        if bool(weights_path) == bool(static_weights):
            raise ValueError(
                f"papertrade book '{name}' must define exactly one of "
                "'weights_path' or 'static_weights'"
            )
        rebalance = str(raw.get("rebalance", "weekly")).lower()
        if rebalance not in VALID_REBALANCE:
            raise ValueError(
                f"papertrade book '{name}' has invalid rebalance '{rebalance}' "
                f"(expected one of {VALID_REBALANCE})"
            )
        margin_req = raw.get("margin_requirement")
        books.append(
            BookConfig(
                name=name,
                weights_path=str(weights_path) if weights_path else None,
                static_weights=(
                    {str(k).upper(): float(v) for k, v in static_weights.items()}
                    if static_weights
                    else None
                ),
                strategy_name=raw.get("strategy_name"),
                rebalance=rebalance,
                cost_bps=float(raw.get("cost_bps", DEFAULT_COST_BPS)),
                starting_cash=float(raw.get("starting_cash", DEFAULT_STARTING_CASH)),
                allow_short=bool(raw.get("allow_short", False)),
                margin_requirement=(float(margin_req) if margin_req is not None else None),
            )
        )
    return books


def load_target_weights(book: BookConfig) -> Dict[str, float]:
    """Latest target weights for a book as {SYMBOL: weight}, positives only."""
    if book.static_weights is not None:
        return {s: w for s, w in book.static_weights.items() if w > 0.0}

    path = Path(book.weights_path)
    if not path.exists():
        raise FileNotFoundError(f"weights_path not found for book '{book.name}': {path}")
    df = pd.read_parquet(path)
    if "weight" not in df.columns:
        # SAFETY: never guess which column holds allocations.
        raise ValueError(
            f"weights parquet for book '{book.name}' has no 'weight' column: {path}"
        )
    if "symbol" not in df.columns:
        raise ValueError(
            f"weights parquet for book '{book.name}' has no 'symbol' column: {path}"
        )
    if book.strategy_name and "strategy_name" in df.columns:
        df = df[df["strategy_name"] == book.strategy_name]
        if df.empty:
            raise ValueError(
                f"no rows for strategy_name='{book.strategy_name}' in {path}"
            )
    date_col = "execution_date" if "execution_date" in df.columns else "date_ts"
    if date_col not in df.columns:
        raise ValueError(
            f"weights parquet for book '{book.name}' has neither "
            f"'execution_date' nor 'date_ts': {path}"
        )
    latest = df[date_col].max()
    df = df[df[date_col] == latest]
    out: Dict[str, float] = {}
    for _, row in df.iterrows():
        weight = float(row["weight"])
        if weight <= 0.0:
            continue
        out[str(row["symbol"]).upper()] = weight
    return out


def load_target_legs(book: BookConfig) -> List[tuple]:
    """Signed multi-instrument legs for a short-capable book.

    Reads the latest execution_date and returns ``(symbol, instrument_type,
    weight)`` with SIGNED weight. ``CASH`` / zero-weight rows are dropped, so an
    all-cash rebalance yields an empty list (the book flattens to cash).
    """
    if book.weights_path is None:
        raise ValueError(f"short book '{book.name}' requires a weights_path")
    path = Path(book.weights_path)
    if not path.exists():
        raise FileNotFoundError(f"weights_path not found for book '{book.name}': {path}")
    df = pd.read_parquet(path)
    for col in ("weight", "symbol"):
        if col not in df.columns:
            raise ValueError(f"weights parquet for book '{book.name}' has no '{col}' column: {path}")
    date_col = "execution_date" if "execution_date" in df.columns else "date_ts"
    if date_col not in df.columns:
        raise ValueError(
            f"weights parquet for book '{book.name}' has neither "
            f"'execution_date' nor 'date_ts': {path}"
        )
    latest = df[date_col].max()
    df = df[df[date_col] == latest]
    legs: List[tuple] = []
    for _, row in df.iterrows():
        symbol = str(row["symbol"]).upper()
        weight = float(row["weight"])
        if symbol == "CASH" or abs(weight) == 0.0:
            continue
        itype = str(row.get("instrument_type", "spot")).lower() or "spot"
        legs.append((symbol, itype, weight))
    return legs


# -- rebalance decision ------------------------------------------------------


def _weights_hash(weights: Dict[str, float]) -> str:
    canonical = json.dumps(
        {k: round(float(v), 12) for k, v in sorted(weights.items())}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _should_rebalance(
    book: BookConfig,
    broker: InternalSimulatorBroker,
    targets: Dict[str, float],
    as_of: str,
) -> bool:
    if broker.last_rebalance is None:
        return True
    if book.rebalance == "daily":
        return True
    if book.rebalance == "weekly":
        elapsed = (_parse_date(as_of) - _parse_date(broker.last_rebalance)).days
        return elapsed >= 7
    # on_change: rebalance only if the target weights differ from last applied.
    hash_path = broker.book_dir / WEIGHTS_HASH_FILENAME
    if not hash_path.exists():
        return True
    try:
        stored = json.loads(hash_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    return stored.get("weights_hash") != _weights_hash(targets)


def _record_applied_weights(
    broker: InternalSimulatorBroker, targets: Dict[str, float], as_of: str
) -> None:
    payload = {
        "weights_hash": _weights_hash(targets),
        "weights": {k: float(v) for k, v in sorted(targets.items())},
        "applied_date": as_of,
    }
    (broker.book_dir / WEIGHTS_HASH_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )


# -- idempotent parquet appends ----------------------------------------------


def _append_equity_row(
    book_dir: Path,
    as_of: str,
    nav: float,
    cash: float,
    positions_value: float,
    starting_cash: float,
) -> Dict[str, float]:
    equity_path = book_dir / EQUITY_FILENAME
    if equity_path.exists():
        equity = pd.read_parquet(equity_path)
        equity = equity[equity["date"] != as_of]  # re-run same day: overwrite row
    else:
        equity = pd.DataFrame(
            columns=["date", "nav", "cash", "positions_value", "daily_return", "cum_return"]
        )
    prior = equity[equity["date"] < as_of].sort_values("date")
    if len(prior) > 0:
        prev_nav = float(prior.iloc[-1]["nav"])
        daily_return = nav / prev_nav - 1.0 if prev_nav > 0 else 0.0
    else:
        daily_return = 0.0
    cum_return = nav / starting_cash - 1.0
    row = {
        "date": as_of,
        "nav": float(nav),
        "cash": float(cash),
        "positions_value": float(positions_value),
        "daily_return": float(daily_return),
        "cum_return": float(cum_return),
    }
    new_row = pd.DataFrame([row])
    equity = pd.concat([equity, new_row], ignore_index=True) if len(equity) else new_row
    equity = equity.sort_values("date").reset_index(drop=True)
    equity.to_parquet(equity_path, index=False)
    return row


def _append_fills(book_dir: Path, as_of: str, fills: List[Fill]) -> None:
    fills_path = book_dir / FILLS_FILENAME
    if fills_path.exists():
        existing = pd.read_parquet(fills_path)
        existing = existing[existing["date"] != as_of]  # drop this date's old fills
    else:
        if not fills:
            return
        existing = pd.DataFrame(
            columns=["date", "symbol", "side", "qty", "price", "notional", "cost_usd",
                     "reason", "instrument_type"]
        )
    new = pd.DataFrame([asdict(f) for f in fills])
    if len(new) and len(existing):
        combined = pd.concat([existing, new], ignore_index=True)
    elif len(new):
        combined = new
    else:
        combined = existing
    combined = combined.sort_values(["date", "symbol"]).reset_index(drop=True)
    combined.to_parquet(fills_path, index=False)


# -- main entrypoint -----------------------------------------------------------


def run_papertrade(
    config_path: str,
    as_of: Optional[str] = None,
    books: Optional[List[str]] = None,
) -> dict:
    """Run one daily paper-trading cycle for every configured book."""
    as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _parse_date(as_of)  # validate format early

    cfg = yaml.safe_load(Path(config_path).read_text())
    section = cfg.get("papertrade") or {}
    if not section:
        raise ValueError(f"no 'papertrade' section in config: {config_path}")
    broker_kind = str(section.get("broker", "simulator")).lower()
    if broker_kind != "simulator":
        logger.warning(
            "papertrade: broker '%s' configured; engine drives the internal "
            "simulator only (Alpaca adapter is standalone, paper-only)",
            broker_kind,
        )
    output_dir = Path(section.get("output_dir", DEFAULT_OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)
    derivatives_path = section.get("derivatives_path", DEFAULT_DERIVATIVES_PATH)

    book_cfgs = load_book_configs(cfg)
    if books is not None:
        wanted = set(books)
        unknown = wanted - {b.name for b in book_cfgs}
        if unknown:
            raise ValueError(f"unknown papertrade book(s): {','.join(sorted(unknown))}")
        book_cfgs = [b for b in book_cfgs if b.name in wanted]

    # Pass 1: open brokers, load targets, gather the symbol union for one fetch.
    prepared = []  # (book, broker, targets | None, legs | None, load_error | None)
    all_symbols: set = set()
    for book in book_cfgs:
        broker = InternalSimulatorBroker(
            output_dir / book.name,
            starting_cash=book.starting_cash,
            cost_bps=book.cost_bps,
            allow_short=book.allow_short,
            margin_requirement=book.margin_requirement,
        )
        targets = None
        legs = None
        try:
            if book.allow_short:
                legs = load_target_legs(book)
            else:
                targets = load_target_weights(book)
            load_error = None
        except Exception as exc:  # noqa: BLE001 - one bad book must not kill the run
            load_error = f"weights_load_failed: {exc}"
            logger.warning("papertrade[%s]: %s", book.name, load_error)
        all_symbols.update(broker.priceable_symbols())
        if targets:
            all_symbols.update(targets)
        if legs:
            all_symbols.update(sym for sym, _, _ in legs)
        prepared.append((book, broker, targets, legs, load_error))

    prices = fetch_prices(sorted(all_symbols)) if all_symbols else {}

    manifest: dict = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "books": {},
    }

    for book, broker, targets, legs, load_error in prepared:
        entry = {
            "status": "skipped",
            "nav": None,
            "n_fills": 0,
            "skip_reason": None,
            "rebalanced": False,
        }
        manifest["books"][book.name] = entry
        if load_error is not None:
            entry["skip_reason"] = load_error
            continue

        # Underlyings that must be priced to mark the book (spot + perp legs).
        held = broker.priceable_symbols()
        missing_held = sorted(
            s for s in held if prices.get(s) is None or prices[s] <= 0
        )
        if missing_held:
            # Never fabricate a mark for a held symbol.
            entry["skip_reason"] = f"missing_prices_for_held: {','.join(missing_held)}"
            logger.warning("papertrade[%s]: skipping — %s", book.name, entry["skip_reason"])
            continue

        # Hash object for the on_change decision (dict for long, legs for short).
        hash_obj = targets if not book.allow_short else {
            f"{sym}|{it}": w for sym, it, w in (legs or [])
        }
        target_symbols = (
            set(targets or {}) if not book.allow_short
            else {sym for sym, _, _ in (legs or [])}
        )

        try:
            rebalance = _should_rebalance(book, broker, hash_obj, as_of)
            fills: List[Fill] = []
            if rebalance:
                missing_targets = sorted(
                    s for s in target_symbols if prices.get(s) is None or prices[s] <= 0
                )
                if missing_targets:
                    entry["skip_reason"] = (
                        f"missing_prices_for_targets: {','.join(missing_targets)}"
                    )
                    logger.warning(
                        "papertrade[%s]: skipping — %s", book.name, entry["skip_reason"]
                    )
                    continue
                if book.allow_short:
                    # Short/margin books size against equity + the margin guard;
                    # the long-only cost-headroom gross scaling does not apply.
                    fills = broker.submit_target_legs(legs or [], prices, as_of)
                    _record_applied_weights(broker, hash_obj, as_of)
                else:
                    # Fully-invested books (gross = 1.0) cannot fund proportional
                    # costs from cash, so reserve headroom for worst-case two-way
                    # turnover (sell everything + buy everything = 2x gross cost).
                    gross = sum(targets.values())
                    max_gross = 1.0 - 2.0 * (book.cost_bps / 10_000.0) - 1e-9
                    if gross > max_gross > 0:
                        scale = max_gross / gross
                        exec_targets = {s: w * scale for s, w in targets.items()}
                        logger.info(
                            "papertrade[%s]: scaling gross %.6f -> %.6f for cost headroom",
                            book.name,
                            gross,
                            max_gross,
                        )
                    else:
                        exec_targets = targets
                    fills = broker.submit_target_weights(exec_targets, prices, as_of)
                    # Hash the *raw* targets: the on_change decision compares
                    # configured weights, not the cost-scaled execution weights.
                    _record_applied_weights(broker, targets, as_of)
                entry["rebalanced"] = True
            # Perp funding accrues once per day (idempotent), before marking.
            if broker.get_perp_positions():
                funding = daily_funding_rates(
                    derivatives_path, as_of, broker.priceable_symbols()
                )
                accrued = broker.accrue_perp_funding(funding, prices, as_of)
                if accrued:
                    entry["funding_accrued"] = float(accrued)
            snapshot = broker.mark_to_market(prices, as_of)
        except ValueError as exc:
            entry["skip_reason"] = str(exc)
            logger.warning("papertrade[%s]: skipping — %s", book.name, exc)
            continue

        row = _append_equity_row(
            broker.book_dir,
            as_of,
            nav=snapshot.nav,
            cash=snapshot.cash,
            positions_value=snapshot.positions_value,
            starting_cash=book.starting_cash,
        )
        if entry["rebalanced"]:
            # Re-running the same date replaces that date's fills; a
            # no-rebalance mark-to-market pass leaves the fills log untouched.
            _append_fills(broker.book_dir, as_of, fills)
        entry.update(
            status="ok",
            nav=row["nav"],
            n_fills=len(fills),
        )
        logger.info(
            "papertrade[%s]: %s nav=%.2f fills=%d rebalanced=%s",
            book.name,
            as_of,
            row["nav"],
            len(fills),
            entry["rebalanced"],
        )

    (output_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2))
    return manifest
