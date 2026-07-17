#!/usr/bin/env python3
"""fiat_map.py — CMC fiat-currency (and precious-metal) reference map.

Endpoint: /v1/fiat/map (include_metals=true). The currency reference table for
USD-conversion work: id↔symbol↔name↔sign. Keyed on the stable ``fiat_id``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .base import finalize, save_raw_sample

DATASET = "pro_api_fiat_map"
FILENAME = "cmc_fiat_map.parquet"
SOURCE = "pro_api:/v1/fiat/map"
KEY = ["fiat_id"]
GENERATED_BY = "src/cmc/collectors/fiat_map.py"


def run(client, base_dir: Path, start=None, end=None) -> dict:
    out_dir = Path(base_dir) / DATASET
    payload = client.get(
        "/v1/fiat/map", params={"include_metals": "true", "start": 1, "limit": 5000}
    )
    rows = payload.get("data") or []
    save_raw_sample(
        out_dir, "fiat_map_sample.json",
        {"status": {"error_code": 0}, "data": rows[:5]},
    )
    recs = [
        {
            "fiat_id": r.get("id"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "sign": r.get("sign"),
            "source": SOURCE,
        }
        for r in rows
    ]
    df = pd.DataFrame(recs)
    df["fiat_id"] = pd.to_numeric(df["fiat_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["fiat_id"])
    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col=None, symbol_col="symbol",
        run={"include_metals": True,
             "note": "Reference table for USD-conversion / fiat exchange work."},
    )
