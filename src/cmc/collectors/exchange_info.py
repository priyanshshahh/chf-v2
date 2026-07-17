#!/usr/bin/env python3
"""exchange_info.py — exchange reference metadata.

Endpoint: /v1/exchange/info (batch up to 100 ids/call). One row per exchange:
launch date, countries, fiats, fees, spot volume, weekly visits, website.
Keyed on ``exchange_id``. Consumes ids from the exchange map.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _num, save_raw_sample
from ..manifest import write_manifest
from ..storage import upsert

DATASET = "pro_api_exchange_info"
FILENAME = "cmc_exchange_info.parquet"
SOURCE = "pro_api:/v1/exchange/info"
KEY = ["exchange_id"]
GENERATED_BY = "src/cmc/collectors/exchange_info.py"
COLUMNS = ["exchange_id", "name", "slug", "date_launched", "countries", "fiats",
           "maker_fee", "taker_fee", "spot_volume_usd", "weekly_visits", "website"]


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def _join(v: Any) -> Optional[str]:
    if isinstance(v, list):
        return ";".join(str(x).strip() for x in v if x) or None
    return v or None


def parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    recs: List[Dict[str, Any]] = []
    for entry in data.values():
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        if not isinstance(entry, dict):
            continue
        urls = entry.get("urls") or {}
        recs.append({
            "exchange_id": entry.get("id"),
            "name": entry.get("name"),
            "slug": entry.get("slug"),
            "date_launched": _date(entry.get("date_launched")),
            "countries": _join(entry.get("countries")),
            "fiats": _join(entry.get("fiats")),
            "maker_fee": _num(entry.get("maker_fee")),
            "taker_fee": _num(entry.get("taker_fee")),
            "spot_volume_usd": _num(entry.get("spot_volume_usd")),
            "weekly_visits": _num(entry.get("weekly_visits")),
            "website": (lambda w: w[0] if isinstance(w, list) and w else (w or None))(
                urls.get("website")),
        })
    return recs


def run(client, base_dir: Path, exchange_map_path: Path) -> dict:
    out_dir = Path(base_dir) / DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAME

    em = pd.read_parquet(exchange_map_path)
    ids = [int(x) for x in em["exchange_id"].dropna().tolist()]

    buf: List[Dict[str, Any]] = []
    sample_saved = False
    errors: List[Dict[str, str]] = []
    attempted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        attempted += 1
        try:
            payload = client.get("/v1/exchange/info",
                                 params={"id": ",".join(map(str, chunk))})
        except Exception as exc:
            errors.append({"ids": f"{chunk[0]}..{chunk[-1]}", "error": str(exc)[:200]})
            continue
        if not sample_saved:
            save_raw_sample(out_dir, "exchange_info_sample.json", payload)
            sample_saved = True
        buf.extend(parse(payload))

    # Systemic-failure guard: every attempted batch erroring means the run
    # produced nothing — surface it instead of reporting success.
    if attempted and len(errors) == attempted:
        raise RuntimeError(f"all_batches_failed: {errors[0]['error']}")

    df = pd.DataFrame(buf, columns=COLUMNS).drop_duplicates(subset=KEY)
    row_count = upsert(df, key=KEY, path=path)
    full = pd.read_parquet(path)
    write_manifest(
        out_dir=out_dir, dataset=DATASET, source=SOURCE,
        coverage_start=None, coverage_end=None, row_count=row_count,
        symbol_count=None, generated_by=GENERATED_BY,
        run={"exchanges_requested": len(ids),
             "exchanges_with_info": int(full["exchange_id"].nunique()),
             "errors": errors[:20], "error_count": len(errors)},
    )
    print(f"[exchange_info] rows={row_count} exchanges={full['exchange_id'].nunique()} "
          f"errors={len(errors)}", flush=True)
    return {"row_count": row_count}


if __name__ == "__main__":
    from ..client import CMCClient
    REPO_ROOT = Path(__file__).resolve().parents[3]
    BASE = REPO_ROOT / "cmc_complete" / "data" / "coinmarketcap_data"
    run(CMCClient(), BASE, BASE / "pro_api_exchange_map" / "cmc_exchange_map.parquet")
