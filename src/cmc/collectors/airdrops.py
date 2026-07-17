#!/usr/bin/env python3
"""airdrops.py — CoinMarketCap airdrop events (ENDED / ONGOING / UPCOMING).

Endpoint: /v1/cryptocurrency/airdrops (paginated start/limit, one status per call).

Quirk: the ``id`` field comes back as a decomposed MongoDB ObjectId object
(timestamp / machineIdentifier / processIdentifier / counter) rather than a hex
string. It is reassembled into the canonical 24-char hex ObjectId to form a stable
``airdrop_id`` key. Coin metadata is nested under ``coin``. Dates normalized to
``YYYY-MM-DD``. Keyed on ``airdrop_id``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _num, finalize, save_raw_sample

DATASET = "pro_api_airdrops"
FILENAME = "cmc_airdrops.parquet"
SOURCE = "pro_api:/v1/cryptocurrency/airdrops"
KEY = ["airdrop_id"]
GENERATED_BY = "src/cmc/collectors/airdrops.py"
STATUSES = ("ENDED", "ONGOING", "UPCOMING")


def _oid(raw: Any) -> Optional[str]:
    """Reassemble a canonical 24-char hex ObjectId from CMC's decomposed form."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        try:
            return "%08x%06x%04x%06x" % (
                int(raw["timestamp"]),
                int(raw["machineIdentifier"]) & 0xFFFFFF,
                int(raw["processIdentifier"]) & 0xFFFF,
                int(raw["counter"]) & 0xFFFFFF,
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _date(v: Any) -> Optional[str]:
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def _parse(rows: List[Dict[str, Any]], status: str) -> List[Dict[str, Any]]:
    recs = []
    for r in rows:
        coin = r.get("coin") or {}
        recs.append(
            {
                "airdrop_id": _oid(r.get("id")),
                "project_name": r.get("project_name"),
                "status": r.get("status") or status,
                "coin_id": coin.get("id"),
                "coin_symbol": coin.get("symbol"),
                "coin_name": coin.get("name"),
                "start_date": _date(r.get("start_date")),
                "end_date": _date(r.get("end_date")),
                "total_prize": _num(r.get("total_prize")),
                "winner_count": r.get("winner_count"),
                "link": r.get("link"),
            }
        )
    return recs


def run(client, base_dir: Path, start=None, end=None) -> dict:
    out_dir = Path(base_dir) / DATASET
    recs: List[Dict[str, Any]] = []
    sample_payload = None
    per_status: Dict[str, int] = {}

    for status in STATUSES:
        cursor, limit = 1, 1000
        got = 0
        while True:
            payload = client.get(
                "/v1/cryptocurrency/airdrops",
                params={"status": status, "start": cursor, "limit": limit},
            )
            rows = payload.get("data") or []
            if sample_payload is None and rows:
                sample_payload = {"status": {"error_code": 0}, "data": rows[:2]}
            recs.extend(_parse(rows, status))
            got += len(rows)
            if len(rows) < limit:
                break
            cursor += limit
        per_status[status] = got

    df = pd.DataFrame(recs)
    df = df.dropna(subset=["airdrop_id"]).drop_duplicates(subset=["airdrop_id"])
    df["coin_id"] = pd.to_numeric(df["coin_id"], errors="coerce").astype("Int64")
    df["winner_count"] = pd.to_numeric(df["winner_count"], errors="coerce").astype("Int64")
    if sample_payload is not None:
        save_raw_sample(out_dir, "airdrops_sample.json", sample_payload)

    return finalize(
        df, out_dir=out_dir, filename=FILENAME, dataset=DATASET, source=SOURCE,
        key=KEY, generated_by=GENERATED_BY, date_col="start_date",
        symbol_col="coin_symbol",
        run={"statuses": list(STATUSES), "rows_per_status": per_status,
             "note": "airdrop_id is the reassembled hex MongoDB ObjectId."},
    )
