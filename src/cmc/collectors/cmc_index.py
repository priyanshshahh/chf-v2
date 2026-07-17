#!/usr/bin/env python3
"""cmc_index.py — CoinMarketCap CMC100 and CMC20 index history (USD, daily).

Endpoints:
  * /v3/index/cmc100-historical  (+ /v3/index/cmc100-latest)
  * /v3/index/cmc20-historical   (+ /v3/index/cmc20-latest)

Quirk: these endpoints cap ``count`` at [1, 10] per call and require FULL ISO-8601
``time_start`` timestamps (a bare date is rejected). History is therefore paged
forward in 10-point windows: request 10 daily points from the cursor, advance the
cursor past the newest returned point, repeat until "today". On this plan the
earliest available data point is 2024-01-01 regardless of how far back time_start
is set.

Each daily row carries the index ``value`` plus its constituent basket, stored as
a JSON string (id/symbol/weight, plus priceUsd/units for CMC20). Keyed on ``date``.
Both index series share the ``pro_api_index_historical/`` directory with their own
manifests (fear_greed and altcoin_season now live in their own dataset dirs).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ..client import CMCClientError
from .base import coerce_float as _f, finalize, save_raw_sample

DATASET = "pro_api_index_historical"
# maintain.py reads incremental coverage from the CMC100 manifest (both index
# series are collected together and cover the same date range).
MANIFEST = "cmc100_manifest.json"
DEFAULT_START = "2024-01-01"  # earliest point this plan exposes for the indices
MAX_COUNT = 10                # endpoint hard cap on `count`


def _iso(day: str) -> str:
    return f"{day}T00:00:00Z"


def _constituents(item: Dict[str, Any], with_price: bool) -> List[Dict[str, Any]]:
    out = []
    for cst in item.get("constituents") or []:
        rec = {
            "id": cst.get("id"),
            "symbol": cst.get("symbol"),
            "weight": _f(cst.get("weight")),
        }
        if with_price:
            rec["priceUsd"] = _f(cst.get("priceUsd"))
            rec["units"] = _f(cst.get("units"))
        out.append(rec)
    return out


def _parse(payload: Dict[str, Any], with_price: bool) -> List[Dict[str, Any]]:
    recs = []
    for item in payload.get("data") or []:
        ts = pd.to_datetime(item.get("update_time"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        csts = _constituents(item, with_price)
        recs.append(
            {
                "date": ts.strftime("%Y-%m-%d"),
                "value": _f(item.get("value")),
                "num_constituents": len(csts),
                "constituents_json": json.dumps(csts, separators=(",", ":")),
            }
        )
    return recs


def _collect(
    client,
    base_dir: Path,
    *,
    index_name: str,
    hist_path: str,
    latest_path: str,
    filename: str,
    manifest_name: str,
    sample_name: str,
    with_price: bool,
    generated_by: str,
    start: Optional[str],
    end: Optional[str],
) -> dict:
    out_dir = Path(base_dir) / DATASET
    time_start = start or DEFAULT_START
    time_end = end or datetime.now(timezone.utc).date().isoformat()

    recs: List[Dict[str, Any]] = []
    sample_payload = None
    cursor = time_start
    notes = ""
    calls = 0
    while cursor <= time_end:
        try:
            payload = client.get(
                hist_path,
                params={"interval": "daily", "count": MAX_COUNT,
                        "time_start": _iso(cursor)},
            )
        except CMCClientError as exc:
            notes = f"Aborted at {cursor}: {exc}"
            break
        calls += 1
        if sample_payload is None:
            sample_payload = {"status": {"error_code": 0},
                              "data": (payload.get("data") or [])[:2]}
        chunk = _parse(payload, with_price)
        if not chunk:
            break
        recs.extend(chunk)
        last = max(r["date"] for r in chunk)
        nxt = (pd.to_datetime(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt <= cursor:  # no forward progress -> stop
            break
        cursor = nxt

    df = pd.DataFrame(recs).drop_duplicates(subset=["date"])
    if sample_payload is not None:
        save_raw_sample(out_dir, sample_name, sample_payload)

    # Latest value for the manifest (cheap, informational).
    latest_value = latest_time = None
    try:
        lp = (client.get(latest_path).get("data") or {})
        latest_value = _f(lp.get("value"))
        latest_time = lp.get("last_update")
    except CMCClientError as exc:
        notes = (notes + " ; " if notes else "") + f"latest fetch failed: {exc}"

    return finalize(
        df, out_dir=out_dir, filename=filename, dataset=DATASET,
        source=f"pro_api:{hist_path}", key=["date"], generated_by=generated_by,
        date_col="date", manifest_name=manifest_name,
        run={
            "index": index_name,
            "interval": "daily", "convert": "USD",
            "requested_start": time_start, "requested_end": time_end,
            "api_calls": calls,
            "count_cap_per_call": MAX_COUNT,
            "latest_value": latest_value,
            "latest_update": latest_time,
            "notes": notes or f"Earliest available point on this plan is {DEFAULT_START}.",
        },
    )


def run_cmc100(client, base_dir: Path, start=None, end=None) -> dict:
    return _collect(
        client, base_dir,
        index_name="cmc100",
        hist_path="/v3/index/cmc100-historical",
        latest_path="/v3/index/cmc100-latest",
        filename="cmc100_index.parquet",
        manifest_name="cmc100_manifest.json",
        sample_name="cmc100_sample.json",
        with_price=False,
        generated_by="src/cmc/collectors/cmc_index.py:run_cmc100",
        start=start, end=end,
    )


def run(client, base_dir: Path, start=None, end=None) -> dict:
    """Standard collector interface: collect both CMC100 and CMC20 series.

    Returns a merged manifest-style summary (row_count summed across the two
    series, coverage spanning both) so ``maintain.py`` can report it.
    """
    r100 = run_cmc100(client, base_dir, start=start, end=end)
    r20 = run_cmc20(client, base_dir, start=start, end=end)
    starts = [s for s in (r100["coverage_start"], r20["coverage_start"]) if s]
    ends = [e for e in (r100["coverage_end"], r20["coverage_end"]) if e]
    return {
        "dataset": DATASET,
        "source": "pro_api:/v3/index/cmc100-historical + /v3/index/cmc20-historical",
        "coverage_start": min(starts) if starts else None,
        "coverage_end": max(ends) if ends else None,
        "row_count": int(r100["row_count"]) + int(r20["row_count"]),
        "symbol_count": None,
    }


def run_cmc20(client, base_dir: Path, start=None, end=None) -> dict:
    return _collect(
        client, base_dir,
        index_name="cmc20",
        hist_path="/v3/index/cmc20-historical",
        latest_path="/v3/index/cmc20-latest",
        filename="cmc20_index.parquet",
        manifest_name="cmc20_manifest.json",
        sample_name="cmc20_sample.json",
        with_price=True,
        generated_by="src/cmc/collectors/cmc_index.py:run_cmc20",
        start=start, end=end,
    )
