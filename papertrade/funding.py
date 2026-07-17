"""Daily perp funding-rate loader for the paper engine.

Reads the OKX derivatives panel (long format
``{symbol, ts_utc, metric, value}`` with ``metric == "funding_rate"``) and
returns the funding rate to accrue for a given UTC day: the SUM of that day's
funding intervals (OKX pays funding three times per day). Symbols with no
funding row on the day are simply absent — the broker never fabricates
funding for them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DERIVATIVES_PATH = "data/external/derivatives_okx.parquet"


def load_funding_panel(path: str | Path) -> Optional[pd.DataFrame]:
    """Long funding frame with a normalized UTC ``day`` column, or None."""
    path = Path(path)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    # Accept either the OKX long format or a legacy {funding_time_utc} frame.
    if {"symbol", "ts_utc", "metric", "value"}.issubset(df.columns):
        df = df[df["metric"] == "funding_rate"].copy()
        df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
        df = df.rename(columns={"value": "funding_rate"})
    elif {"symbol", "funding_time_utc", "funding_rate"}.issubset(df.columns):
        df = df.copy()
        df["ts"] = pd.to_datetime(df["funding_time_utc"], utc=True)
    else:
        return None
    df["day"] = df["ts"].dt.normalize()
    return df[["symbol", "day", "funding_rate"]]


def daily_funding_rates(
    path: str | Path, as_of: str, symbols: Optional[Iterable[str]] = None
) -> Dict[str, float]:
    """Per-symbol funding rate to accrue on the ``as_of`` UTC day.

    Value is the sum of the day's funding intervals. Missing symbols/day -> not
    present in the returned dict (caller skips accrual for them).
    """
    panel = load_funding_panel(path)
    if panel is None or panel.empty:
        return {}
    day = pd.Timestamp(as_of, tz="UTC").normalize()
    rows = panel[panel["day"] == day]
    if rows.empty:
        return {}
    daily = rows.groupby("symbol")["funding_rate"].sum()
    out = {str(s).upper(): float(v) for s, v in daily.items()}
    if symbols is not None:
        wanted = {str(s).upper() for s in symbols}
        out = {s: v for s, v in out.items() if s in wanted}
    return out
