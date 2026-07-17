#!/usr/bin/env python3
"""altcoin_season.py — CMC Altcoin Season Index history.

Endpoint: /v1/altcoin-season-index/historical (timeframe=90d is the max the
endpoint exposes). One row per day: altcoin_index + altcoin_marketcap. Keyed on
``date``. Manifest notes the 90d limitation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .base import coerce_float as _f, finalize, save_raw_sample
from ..manifest import read_manifest

DATASET = "pro_api_altcoin_season"  # own dir (was combined into pro_api_index_historical)
FILENAME = "cmc_altcoin_season.parquet"
SOURCE = "pro_api:/v1/altcoin-season-index/historical"
KEY = ["date"]
GENERATED_BY = "src/cmc/collectors/altcoin_season.py"
MANIFEST = "manifest.json"  # dedicated dir -> standard manifest name


def _points(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get("points") or []
    return data or []


def _parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs = []
    for r in _points(payload):
        ts = pd.to_datetime(r.get("timestamp"), utc=True, errors="coerce")
        if pd.isna(ts):
            try:
                ts = pd.to_datetime(int(r.get("timestamp")), unit="s", utc=True)
            except (TypeError, ValueError):
                continue
        recs.append(
            {
                "date": ts.strftime("%Y-%m-%d"),
                "altcoin_index": _f(r.get("altcoinIndex") or r.get("altcoin_index")),
                "altcoin_marketcap": _f(
                    r.get("altcoinMarketcap") or r.get("altcoin_marketcap")
                ),
            }
        )
    return recs


def run(client, base_dir: Path, start=None, end=None) -> dict:
    # NOTE (collector contract, see base.CollectorRun): ``start`` cannot be
    # honored — the endpoint only exposes a FIXED 90-day trailing window
    # (timeframe=90d is the max). If maintenance skips more than 90 days the
    # missed span is unrecoverable, so we detect that and record it in the
    # manifest (gap_detected / gap_range) instead of silently reporting success.
    out_dir = Path(base_dir) / DATASET
    prev = read_manifest(out_dir, MANIFEST)

    payload = client.get(
        "/v1/altcoin-season-index/historical", params={"timeframe": "90d"}
    )
    save_raw_sample(
        out_dir, "altcoin_season_sample.json",
        {"status": {"error_code": 0},
         "data": {"points": _points(payload)[:3]}},
    )
    df = pd.DataFrame(_parse(payload)).drop_duplicates(subset=["date"])

    run_info: Dict[str, Any] = {
        "index": "altcoin_season",
        "notes": "Endpoint only exposes timeframe=90d (max available).",
    }
    window_start = (datetime.now(timezone.utc).date()
                    - timedelta(days=90)).isoformat()
    prev_end = (prev or {}).get("coverage_end")
    if prev_end and prev_end < window_start:
        # Previous coverage ended before the current 90d window began: the
        # span (prev_end, window_start) can never be fetched again.
        run_info["gap_detected"] = True
        run_info["gap_range"] = [prev_end, window_start]
        print(f"[altcoin_season] WARNING: unrecoverable gap detected — previous "
              f"coverage_end={prev_end} predates the current 90d window start "
              f"{window_start}", flush=True)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="date",
        manifest_name=MANIFEST, run=run_info,
    )
