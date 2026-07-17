#!/usr/bin/env python3
"""global_metrics.py — daily historical global market metrics (USD).

Endpoint: /v1/global-metrics/quotes/historical (interval=daily). One row per day:
total market cap, total 24h volume, BTC/ETH dominance, active cryptocurrencies,
altcoin market cap. Keyed on ``date``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

import pandas as pd

from ..client import CMCClientError
from .base import coerce_float as _f, finalize, save_raw_sample

DATASET = "pro_api_global_metrics_historical"
FILENAME = "cmc_global_metrics.parquet"
SOURCE = "pro_api:/v1/global-metrics/quotes/historical"
KEY = ["date"]
GENERATED_BY = "src/cmc/collectors/global_metrics.py"
DEFAULT_START = "2013-04-28"


def _parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    quotes = (payload.get("data") or {}).get("quotes") or []
    recs = []
    for q in quotes:
        ts = pd.to_datetime(q.get("timestamp"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        usd = (q.get("quote") or {}).get("USD") or {}
        recs.append(
            {
                "date": ts.strftime("%Y-%m-%d"),
                "total_market_cap": _f(usd.get("total_market_cap")),
                "total_volume_24h": _f(usd.get("total_volume_24h")),
                "altcoin_market_cap": _f(usd.get("altcoin_market_cap")),
                "btc_dominance": _f(q.get("btc_dominance")),
                "eth_dominance": _f(q.get("eth_dominance")),
                "active_cryptocurrencies": q.get("active_cryptocurrencies"),
            }
        )
    return recs


def run(client, base_dir: Path, start=None, end=None) -> dict:
    out_dir = Path(base_dir) / DATASET
    time_start = start or DEFAULT_START
    time_end = end or datetime.now(timezone.utc).date().isoformat()

    recs: List[Dict[str, Any]] = []
    sample_payload = None
    cursor = time_start
    notes = ""
    achieved_full_history = True
    # Page forward in time; the endpoint returns up to `count` daily points/call.
    while cursor <= time_end:
        try:
            payload = client.get(
                "/v1/global-metrics/quotes/historical",
                params={
                    "time_start": cursor,
                    "time_end": time_end,
                    "interval": "daily",
                    "count": 10000,
                    "convert": "USD",
                },
            )
        except CMCClientError as exc:
            # Graceful degradation (FR-013): plan caps historical depth. Retry from
            # the newest allowed startDate the API quotes back to us, and record it.
            msg = str(exc)
            m = re.search(r"newer than (\d{4}-\d{2}-\d{2})", msg)
            if m and m.group(1) >= cursor:
                allowed = (pd.to_datetime(m.group(1)) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                achieved_full_history = False
                notes = (f"Plan-limited: full history from {time_start} unavailable; "
                         f"degraded to start {allowed}. {msg}")
                cursor = allowed
                continue
            notes = f"Aborted at {cursor}: {msg}"
            break
        if sample_payload is None:
            data = payload.get("data") or {}
            q = (data.get("quotes") or [])[:3]
            sample_payload = {"status": {"error_code": 0}, "data": {"quotes": q}}
        chunk = _parse(payload)
        if not chunk:
            break
        recs.extend(chunk)
        last = max(r["date"] for r in chunk)
        if last <= cursor and len(chunk) < 2:
            break
        nxt = (pd.to_datetime(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt <= cursor:
            break
        cursor = nxt

    df = pd.DataFrame(recs).drop_duplicates(subset=["date"])
    if sample_payload is not None:
        save_raw_sample(out_dir, "global_metrics_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="date",
        run={"interval": "daily", "convert": "USD",
             "requested_start": time_start, "requested_end": time_end,
             "achieved_full_history": achieved_full_history, "notes": notes},
    )
