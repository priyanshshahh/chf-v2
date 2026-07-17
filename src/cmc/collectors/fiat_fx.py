#!/usr/bin/env python3
"""fiat_fx.py — current USD->fiat exchange-rate snapshot (all fiats + metals).

Endpoint: /v2/tools/price-conversion (amount=1, id=2781 [USD], convert_id=<fiats>).
The fiat universe is read from pro_api_fiat_map/cmc_fiat_map.parquet and batched
into comma-separated ``convert_id`` requests. Each returned quote price is the
number of fiat units per 1 USD (``usd_to_fiat_rate``). Keyed on (fiat_id, snapshot_date).

LIMITATION: this is a CURRENT snapshot only. CMC exposes no bulk historical FX
time-series on this plan — historical conversion requires one call per date via the
``time`` param — so a full historical FX series is a documented gap, not collected here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _f, finalize, save_raw_sample

DATASET = "pro_api_fiat_fx"
FILENAME = "cmc_fiat_usd_snapshot.parquet"
SOURCE = "pro_api:/v2/tools/price-conversion"
KEY = ["fiat_id", "snapshot_date"]
GENERATED_BY = "src/cmc/collectors/fiat_fx.py"
USD_ID = 2781
BATCH = 8  # convert_id per call (plan limit: max 8 convert options)


def run(client, base_dir: Path, start=None, end=None) -> dict:
    base_dir = Path(base_dir)
    out_dir = base_dir / DATASET
    fiat_map_path = base_dir / "pro_api_fiat_map" / "cmc_fiat_map.parquet"

    fmap = pd.read_parquet(fiat_map_path)
    sym_by_id = dict(zip(fmap["fiat_id"].astype("Int64"), fmap["symbol"]))
    fiat_ids = [int(x) for x in fmap["fiat_id"].dropna().astype(int).tolist()]

    snapshot_date = datetime.now(timezone.utc).date().isoformat()
    recs: List[Dict[str, Any]] = []
    sample_payload = None
    errors: List[str] = []

    for i in range(0, len(fiat_ids), BATCH):
        chunk = fiat_ids[i:i + BATCH]
        try:
            payload = client.get(
                "/v2/tools/price-conversion",
                params={"amount": 1, "id": USD_ID,
                        "convert_id": ",".join(str(x) for x in chunk)},
            )
        except Exception as exc:
            errors.append(f"batch@{i}: {str(exc)[:200]}")
            continue
        if sample_payload is None:
            sample_payload = payload
        quote = ((payload.get("data") or {}).get("quote")) or {}
        for fid in chunk:
            q = quote.get(str(fid)) or {}
            rate = _f(q.get("price"))
            if rate is None:
                continue
            recs.append(
                {
                    "snapshot_date": snapshot_date,
                    "fiat_id": fid,
                    "fiat_symbol": sym_by_id.get(fid),
                    "usd_to_fiat_rate": rate,
                    "source": SOURCE,
                }
            )

    cols = ["snapshot_date", "fiat_id", "fiat_symbol", "usd_to_fiat_rate", "source"]
    df = pd.DataFrame(recs, columns=cols)
    df["fiat_id"] = pd.to_numeric(df["fiat_id"], errors="coerce").astype("Int64")
    if sample_payload is not None:
        save_raw_sample(out_dir, "fiat_fx_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="snapshot_date",
        symbol_col="fiat_symbol",
        run={
            "base_currency": "USD (id=2781)",
            "snapshot_date": snapshot_date,
            "fiats_requested": len(fiat_ids),
            "fiats_returned": int(df["fiat_id"].nunique()) if len(df) else 0,
            "errors": errors,
            "LIMITATION": ("CURRENT snapshot only. No bulk historical FX time-series "
                           "on this plan (historical needs one call per date via `time`). "
                           "Full historical FX is a documented gap."),
        },
    )
