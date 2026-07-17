"""Append-only immutable event ledger for CHF fund accounting.

Every economically meaningful event — trade fills, cash movements, fee
accruals, fee crystallizations, and daily price marks — is stored as one
typed row in a single parquet ledger (default
``data/accounting/ledger.parquet``).

Rows are identified by a caller-supplied ``event_id`` (use
:func:`make_event_id` for deterministic ids) and appends are idempotent:
an event_id that already exists in the ledger is silently skipped, and
existing rows are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd

DEFAULT_LEDGER_PATH = Path("data/accounting/ledger.parquet")

EVENT_TYPES = ("fill", "mark", "fee_accrual", "fee_crystallization", "cash_flow")

LEDGER_COLUMNS = [
    "event_id",
    "ts_utc",
    "book",
    "event_type",
    "symbol",
    "qty",
    "price",
    "notional",
    "details_json",
]


@dataclass(frozen=True)
class LedgerEvent:
    """One immutable ledger row.

    ``ts_utc`` must be an ISO-8601 UTC timestamp whose first 10 characters
    are the accounting date (``YYYY-MM-DD``); daily processing keys off that
    prefix. ``details_json`` carries event-specific fields (e.g. ``cost_usd``
    and ``side`` for fills, ``source`` for marks) as a JSON object string.
    """

    event_id: str
    ts_utc: str
    book: str
    event_type: str
    symbol: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
    notional: Optional[float] = None
    details_json: str = "{}"

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(
                f"invalid event_type '{self.event_type}' (expected one of {EVENT_TYPES})"
            )
        if not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if not self.book:
            raise ValueError("book must be a non-empty string")
        json.loads(self.details_json)  # must be valid JSON


def make_event_id(*parts: object) -> str:
    """Deterministic event id: SHA-256 over the canonical part string."""
    canonical = "|".join(str(p) for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def details(**kwargs: object) -> str:
    """Canonical JSON string for the ``details_json`` column."""
    return json.dumps(kwargs, sort_keys=True, separators=(",", ":"))


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def read_ledger(
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    book: Optional[str] = None,
    event_types: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Read the ledger (optionally filtered), sorted by (ts_utc, event_id)."""
    path = Path(ledger_path)
    if not path.exists():
        return _empty_ledger()
    df = pd.read_parquet(path)
    if book is not None:
        df = df[df["book"] == book]
    if event_types is not None:
        df = df[df["event_type"].isin(list(event_types))]
    return df.sort_values(["ts_utc", "event_id"]).reset_index(drop=True)


def append(
    events: Iterable[LedgerEvent],
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> int:
    """Append events to the ledger, skipping event_ids already present.

    Returns the number of rows actually written. Existing rows are never
    modified or deleted — the ledger is strictly append-only.
    """
    new_events: List[LedgerEvent] = list(events)
    if not new_events:
        return 0
    batch = pd.DataFrame([asdict(e) for e in new_events], columns=LEDGER_COLUMNS)
    for col in ("qty", "price", "notional"):
        batch[col] = pd.to_numeric(batch[col], errors="coerce").astype("float64")
    batch["symbol"] = batch["symbol"].astype("string")
    dup_in_batch = batch["event_id"].duplicated()
    if dup_in_batch.any():
        batch = batch[~dup_in_batch]

    path = Path(ledger_path)
    if path.exists():
        existing = pd.read_parquet(path)
        batch = batch[~batch["event_id"].isin(set(existing["event_id"]))]
        if batch.empty:
            return 0
        combined = pd.concat([existing, batch], ignore_index=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        combined = batch
    combined.to_parquet(path, index=False)
    return int(len(batch))
