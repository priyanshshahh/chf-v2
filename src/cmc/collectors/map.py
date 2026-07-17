#!/usr/bin/env python3
"""map.py — coin reference map (survivorship-free id↔symbol↔name↔slug directory).

Endpoint: /v1/cryptocurrency/map. Fetches active + inactive + untracked coins so
delisted coins (e.g. LUNA/Terra Classic) are retained with is_active=false. Keyed
on the stable ``cmc_id``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import finalize, save_raw_sample

DATASET = "pro_api_map"
FILENAME = "cmc_map.parquet"
SOURCE = "pro_api:/v1/cryptocurrency/map"
KEY = ["cmc_id"]
GENERATED_BY = "src/cmc/collectors/map.py"
_AUX = "platform,first_historical_data,last_historical_data,is_active,status"


def _norm_date(v: Any) -> Optional[str]:
    if not v:
        return None
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def _fetch_status(client, listing_status: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 1
    limit = 5000
    while True:
        payload = client.get(
            "/v1/cryptocurrency/map",
            params={
                "listing_status": listing_status,
                "start": start,
                "limit": limit,
                "aux": _AUX,
            },
        )
        chunk = payload.get("data") or []
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return rows


def run(client, base_dir: Path, start=None, end=None) -> dict:
    base_dir = Path(base_dir)
    out_dir = base_dir / DATASET
    all_rows: List[Dict[str, Any]] = []
    sample_payload = None
    for status in ("active", "inactive", "untracked"):
        rows = _fetch_status(client, status)
        all_rows.extend(rows)
        if sample_payload is None and rows:
            sample_payload = {"status": {"error_code": 0}, "data": rows[:5]}

    recs = []
    for r in all_rows:
        recs.append(
            {
                "cmc_id": r.get("id"),
                "symbol": str(r.get("symbol") or "").upper().strip(),
                "name": r.get("name"),
                "slug": r.get("slug"),
                "is_active": bool(r.get("is_active", 0)),
                "first_historical_data": _norm_date(r.get("first_historical_data")),
                "last_historical_data": _norm_date(r.get("last_historical_data")),
                "source": SOURCE,
            }
        )
    df = pd.DataFrame(recs)
    df["cmc_id"] = pd.to_numeric(df["cmc_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["cmc_id"])

    if sample_payload is not None:
        save_raw_sample(out_dir, "map_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="first_historical_data",
        symbol_col="symbol",
        run={
            "listing_statuses": ["active", "inactive", "untracked"],
            "includes_inactive_delisted": True,
            "survivorship_bias_free": True,
        },
    )
