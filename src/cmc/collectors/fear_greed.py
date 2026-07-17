#!/usr/bin/env python3
"""fear_greed.py — CMC Crypto Fear & Greed Index history.

Endpoint: /v3/fear-and-greed/historical (paginated by start/limit). One row per
day: value (0-100) + value_classification. Keyed on ``date``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .base import finalize, save_raw_sample

DATASET = "pro_api_fear_greed"  # own dir (was combined into pro_api_index_historical)
FILENAME = "cmc_fear_greed.parquet"
SOURCE = "pro_api:/v3/fear-and-greed/historical"
KEY = ["date"]
GENERATED_BY = "src/cmc/collectors/fear_greed.py"
MANIFEST = "manifest.json"  # dedicated dir -> standard manifest name


def _parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs = []
    for r in payload.get("data") or []:
        ts = r.get("timestamp")
        # timestamp may be unix seconds (str/int) or ISO
        try:
            dt = pd.to_datetime(int(ts), unit="s", utc=True)
        except (TypeError, ValueError):
            dt = pd.to_datetime(ts, utc=True, errors="coerce")
        if pd.isna(dt):
            continue
        recs.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "value": pd.to_numeric(r.get("value"), errors="coerce"),
                "value_classification": r.get("value_classification"),
            }
        )
    return recs


def run(client, base_dir: Path, start=None, end=None) -> dict:
    # NOTE (collector contract, see base.CollectorRun): ``start`` is
    # deliberately IGNORED — this collector always pages the FULL history.
    # The endpoint is cheap (a handful of paginated calls for the whole
    # series), and re-fetching everything is self-healing: any gap left by a
    # missed month is refilled, and upsert dedupes on the ``date`` key, so a
    # full re-page is idempotent. Honoring ``start`` would save almost nothing
    # and could leave holes if a prior run's coverage_end was wrong.
    out_dir = Path(base_dir) / DATASET
    recs: List[Dict[str, Any]] = []
    sample_payload = None
    cursor = 1
    limit = 500
    while True:
        payload = client.get(
            "/v3/fear-and-greed/historical", params={"start": cursor, "limit": limit}
        )
        if sample_payload is None:
            sample_payload = {"status": {"error_code": 0},
                              "data": (payload.get("data") or [])[:3]}
        chunk = _parse(payload)
        if not chunk:
            break
        recs.extend(chunk)
        if len(payload.get("data") or []) < limit:
            break
        cursor += limit

    df = pd.DataFrame(recs).drop_duplicates(subset=["date"])
    if sample_payload is not None:
        save_raw_sample(out_dir, "fear_greed_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="date",
        manifest_name=MANIFEST, run={"index": "fear_and_greed"},
    )
