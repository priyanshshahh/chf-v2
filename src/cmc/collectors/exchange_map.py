#!/usr/bin/env python3
"""exchange_map.py — CMC exchange reference map (active + inactive).

Endpoint: /v1/exchange/map. id↔name↔slug↔is_active directory of exchanges, keyed
on the stable exchange ``exchange_id``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import finalize, save_raw_sample

DATASET = "pro_api_exchange_map"
FILENAME = "cmc_exchange_map.parquet"
SOURCE = "pro_api:/v1/exchange/map"
KEY = ["exchange_id"]
GENERATED_BY = "src/cmc/collectors/exchange_map.py"
_AUX = "first_historical_data,last_historical_data,is_active,status"


def _norm_date(v: Any) -> Optional[str]:
    if not v:
        return None
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def _fetch_status(client, listing_status: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start, limit = 1, 5000
    while True:
        payload = client.get(
            "/v1/exchange/map",
            params={"listing_status": listing_status, "start": start,
                    "limit": limit, "aux": _AUX},
        )
        chunk = payload.get("data") or []
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return rows


def run(client, base_dir: Path, start=None, end=None) -> dict:
    out_dir = Path(base_dir) / DATASET
    all_rows, sample_payload = [], None
    for status in ("active", "inactive"):
        rows = _fetch_status(client, status)
        all_rows.extend(rows)
        if sample_payload is None and rows:
            sample_payload = {"status": {"error_code": 0}, "data": rows[:5]}

    recs = [
        {
            "exchange_id": r.get("id"),
            "name": r.get("name"),
            "slug": r.get("slug"),
            "is_active": bool(r.get("is_active", 0)),
            "first_historical_data": _norm_date(r.get("first_historical_data")),
            "last_historical_data": _norm_date(r.get("last_historical_data")),
            "source": SOURCE,
        }
        for r in all_rows
    ]
    df = pd.DataFrame(recs)
    df["exchange_id"] = pd.to_numeric(df["exchange_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["exchange_id"])

    if sample_payload is not None:
        save_raw_sample(out_dir, "exchange_map_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="first_historical_data",
        run={"listing_statuses": ["active", "inactive"]},
    )
