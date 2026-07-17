#!/usr/bin/env python3
"""ohlcv.py — daily OHLCV historical quotes per coin (SAMPLE runner + full path).

Endpoint: /v2/cryptocurrency/ohlcv/historical (time_period=daily, convert=USD).
One row per (cmc_id, date): open/high/low/close/volume/market_cap. The date range
for each coin is driven from the map's first/last_historical_data.

``run_sample`` fetches only a few ids (BTC/ETH + one delisted) to prove the path
end-to-end. A full backfill of all ~12k coins is a separate long run (see manifest).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _f, finalize, save_raw_sample

DATASET = "pro_api_ohlcv_historical"
FILENAME = "cmc_ohlcv_sample.parquet"
SOURCE = "pro_api:/v2/cryptocurrency/ohlcv/historical"
KEY = ["cmc_id", "date"]
GENERATED_BY = "src/cmc/collectors/ohlcv.py"


def fetch_coin(client, cmc_id: int, time_start: str, time_end: str) -> Dict[str, Any]:
    return client.get(
        "/v2/cryptocurrency/ohlcv/historical",
        params={
            "id": cmc_id,
            "time_period": "daily",
            "time_start": time_start,
            "time_end": time_end,
            "interval": "daily",
            "convert": "USD",
        },
    )


def _parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    # /v2 keys by id → {id, symbol, quotes:[...]}
    entries = data.values() if isinstance(data, dict) else [data]
    recs: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        sym = entry.get("symbol")
        for q in entry.get("quotes") or []:
            usd = (q.get("quote") or {}).get("USD") or {}
            ts = pd.to_datetime(q.get("time_open") or usd.get("timestamp"),
                                utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            recs.append(
                {
                    "cmc_id": cid,
                    "date": ts.strftime("%Y-%m-%d"),
                    "symbol": sym,
                    "open": _f(usd.get("open")),
                    "high": _f(usd.get("high")),
                    "low": _f(usd.get("low")),
                    "close": _f(usd.get("close")),
                    "volume": _f(usd.get("volume")),
                    "market_cap": _f(usd.get("market_cap")),
                }
            )
    return recs


def run_sample(client, base_dir: Path, ids: Dict[int, Dict[str, str]]) -> dict:
    """Fetch OHLCV for a handful of coins. ``ids`` maps cmc_id → {start,end,note}."""
    out_dir = Path(base_dir) / DATASET
    recs: List[Dict[str, Any]] = []
    sample_payload = None
    errors: List[Dict[str, str]] = []
    for cmc_id, span in ids.items():
        try:
            payload = fetch_coin(client, cmc_id, span["start"], span["end"])
        except Exception as exc:  # record + continue (don't halt the run)
            errors.append({"cmc_id": str(cmc_id), "error": str(exc)})
            continue
        if sample_payload is None:
            sample_payload = payload
        recs.extend(_parse(payload))

    # Systemic-failure guard: every attempted fetch erroring means the run
    # produced nothing — surface it instead of reporting success.
    if ids and len(errors) == len(ids):
        raise RuntimeError(f"all_batches_failed: {errors[0]['error']}")

    if sample_payload is not None:
        save_raw_sample(out_dir, "ohlcv_sample.json", sample_payload)

    df = pd.DataFrame(
        recs,
        columns=["cmc_id", "date", "symbol", "open", "high", "low", "close",
                 "volume", "market_cap"],
    ).drop_duplicates(subset=KEY)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="date", symbol_col="symbol",
        run={
            "is_sample": True,
            "sample_ids": {str(k): v for k, v in ids.items()},
            "errors": errors,
            "full_backfill_status": "NOT RUN — sample only",
            "full_backfill_note": (
                "Full daily OHLCV for all ~12k mapped coins is a separate long "
                "run: ~12,000 coins x per-coin history, paced under the rate "
                "limit, is the outstanding heavy job. This sample proves the path."
            ),
        },
    )


def run(client, base_dir: Path, start=None, end=None) -> dict:
    """Default sample run: BTC (1), ETH (1027) + one delisted (resolved from map)."""
    end = end or datetime.now(timezone.utc).date().isoformat()
    default_start = start or "2013-04-28"
    ids = {
        1: {"start": default_start, "end": end, "note": "BTC"},
        1027: {"start": default_start, "end": end, "note": "ETH"},
    }
    return run_sample(client, base_dir, ids)
