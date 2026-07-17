"""Management and performance fee accounting for CHF books.

Shadow fee schedule applied on top of the ledger-derived gross NAV series
(papertrade books themselves never pay fees, so fees are accrued here and
netted out of the reported NAV):

- Management fee: ``management_fee_annual`` (default 2%/yr) accrued per
  calendar day as ``gross_nav × rate / day_count`` (day_count default 365).
- Performance fee: ``performance_fee_rate`` (default 20%) crystallized
  monthly, ONLY on gains above the book's high-water mark. The HWM ratchets
  up at crystallization and never comes down (no clawback).

Fee parameters live in ``configs/accounting.yaml``; per-book HWM state is
persisted to ``data/accounting/hwm.json``.
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("configs/accounting.yaml")

DEFAULT_CONFIG: Dict[str, object] = {
    "management_fee_annual": 0.02,
    "performance_fee_rate": 0.20,
    "day_count": 365,
    "crystallization": "monthly",
    "tolerance_bps": 1.0,
    "hwm_path": "data/accounting/hwm.json",
}


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, object]:
    """Fee/accounting config with defaults for any missing key."""
    cfg = dict(DEFAULT_CONFIG)
    path = Path(config_path)
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        section = raw.get("accounting", raw) or {}
        for key in DEFAULT_CONFIG:
            if key in section and section[key] is not None:
                cfg[key] = section[key]
    cfg["management_fee_annual"] = float(cfg["management_fee_annual"])
    cfg["performance_fee_rate"] = float(cfg["performance_fee_rate"])
    cfg["day_count"] = int(cfg["day_count"])
    cfg["tolerance_bps"] = float(cfg["tolerance_bps"])
    return cfg


# -- high-water-mark state -----------------------------------------------------


def load_hwm_state(hwm_path: Path) -> Dict[str, dict]:
    path = Path(hwm_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("accounting.fees: unreadable HWM state at %s; starting fresh", path)
        return {}


def update_hwm_state(book: str, entry: dict, hwm_path: Path) -> None:
    """Merge one book's HWM entry into hwm.json (atomic write)."""
    path = Path(hwm_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = load_hwm_state(path)
    state[book] = entry
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


# -- fee engine ------------------------------------------------------------------


def _parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _is_month_end(d: date_cls) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _crystallization_dates(dates: List[date_cls]) -> set:
    """Dates on which the monthly performance fee crystallizes.

    The last observed date of each (year, month) crystallizes if the month is
    complete: either that date is the calendar month-end, or the series
    continues into a later month (the engine may skip the literal month-end
    day). A trailing partial month never crystallizes.
    """
    last_of_month: Dict[Tuple[int, int], date_cls] = {}
    for d in dates:
        key = (d.year, d.month)
        if key not in last_of_month or d > last_of_month[key]:
            last_of_month[key] = d
    max_key = max(last_of_month)
    out = set()
    for key, d in last_of_month.items():
        if _is_month_end(d) or key < max_key:
            out.add(d)
    return out


def apply_fees(
    gross_nav: pd.DataFrame,
    book: str,
    config: Dict[str, object],
    initial_hwm: Optional[float] = None,
) -> Tuple[pd.DataFrame, dict]:
    """Compute the fee-adjusted (net) NAV series from a gross NAV series.

    ``gross_nav`` must have columns [date, nav] sorted by date. Management
    fee accrues per calendar day elapsed between observations (first
    observation counts one day). Performance fee crystallizes at month end,
    only above the HWM, which then ratchets to the post-fee net NAV.

    The whole history is recomputed deterministically on every run, so the
    persisted HWM entry is a published artifact, not an input; pass
    ``initial_hwm`` to seed a book whose pre-history is not in the ledger.

    Returns (series, hwm_entry): the input frame plus columns
    [mgmt_fee_accrual, mgmt_fee_cum, perf_fee, perf_fee_cum, net_nav, hwm],
    and the final HWM entry for hwm.json.
    """
    df = gross_nav.sort_values("date").reset_index(drop=True)
    rate = float(config["management_fee_annual"])
    perf_rate = float(config["performance_fee_rate"])
    day_count = int(config["day_count"])

    dates = [_parse_date(str(d)) for d in df["date"]]
    crystallize_on = _crystallization_dates(dates)

    hwm: Optional[float] = float(initial_hwm) if initial_hwm is not None else None
    cum_mgmt = 0.0
    cum_perf = 0.0
    last_crystallization: Optional[str] = None
    rows: List[dict] = []
    prev_date: Optional[date_cls] = None
    for d, gross in zip(dates, df["nav"].astype(float)):
        days = (d - prev_date).days if prev_date is not None else 1
        accrual = gross * rate / day_count * days
        cum_mgmt += accrual
        net_before_perf = gross - cum_mgmt - cum_perf
        if hwm is None:
            # Inception HWM: the first net-of-fee NAV observed.
            hwm = net_before_perf
        perf_fee = 0.0
        if d in crystallize_on and net_before_perf > hwm:
            perf_fee = perf_rate * (net_before_perf - hwm)
            cum_perf += perf_fee
            hwm = net_before_perf - perf_fee  # ratchet; never lowered
            last_crystallization = d.isoformat()
        rows.append(
            {
                "mgmt_fee_accrual": accrual,
                "mgmt_fee_cum": cum_mgmt,
                "perf_fee": perf_fee,
                "perf_fee_cum": cum_perf,
                "net_nav": gross - cum_mgmt - cum_perf,
                "hwm": hwm,
            }
        )
        prev_date = d

    out = pd.concat([df, pd.DataFrame(rows)], axis=1)
    hwm_entry = {
        "hwm": float(hwm) if hwm is not None else None,
        "as_of": dates[-1].isoformat() if dates else None,
        "last_crystallization": last_crystallization,
        "mgmt_fee_cum": cum_mgmt,
        "perf_fee_cum": cum_perf,
    }
    return out, hwm_entry


def append_fee_events(
    fee_series: pd.DataFrame,
    book: str,
    ledger_path: Path = ledger_mod.DEFAULT_LEDGER_PATH,
) -> int:
    """Record fee accruals / crystallizations in the ledger (idempotent).

    Event ids include the amount, so a recomputed history with different
    amounts appends new audit rows rather than silently replacing old ones —
    fee events are an audit record and are never used by the NAV rebuild.
    """
    events: List[ledger_mod.LedgerEvent] = []
    for _, row in fee_series.iterrows():
        date = str(row["date"])
        ts = f"{date}T00:00:00+00:00"
        accrual = float(row["mgmt_fee_accrual"])
        events.append(
            ledger_mod.LedgerEvent(
                event_id=ledger_mod.make_event_id(
                    "fee_accrual", book, date, f"{accrual:.10f}"
                ),
                ts_utc=ts,
                book=book,
                event_type="fee_accrual",
                notional=accrual,
                details_json=ledger_mod.details(fee="management", basis="daily_365"),
            )
        )
        perf = float(row["perf_fee"])
        if perf > 0.0:
            events.append(
                ledger_mod.LedgerEvent(
                    event_id=ledger_mod.make_event_id(
                        "fee_crystallization", book, date, f"{perf:.10f}"
                    ),
                    ts_utc=ts,
                    book=book,
                    event_type="fee_crystallization",
                    notional=perf,
                    details_json=ledger_mod.details(
                        fee="performance", hwm=float(row["hwm"])
                    ),
                )
            )
    return ledger_mod.append(events, ledger_path)
