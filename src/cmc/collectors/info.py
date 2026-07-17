#!/usr/bin/env python3
"""info.py — coin metadata (static reference attributes).

Endpoint: /v2/cryptocurrency/info (batch up to 100 ids/call, ~1 credit/call).
One row per coin: category, launch/added dates, tags, platform, and key URLs.
Keyed on ``cmc_id``. Consumes the active-coin id set from the map.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import save_raw_sample
from ..manifest import write_manifest
from ..storage import upsert

DATASET = "pro_api_info_metadata"
FILENAME = "cmc_info.parquet"
SOURCE = "pro_api:/v2/cryptocurrency/info"
KEY = ["cmc_id"]
GENERATED_BY = "src/cmc/collectors/info.py"
COLUMNS = ["cmc_id", "symbol", "name", "category", "date_launched", "date_added",
           "tags", "platform_name", "platform_token_address", "website",
           "technical_doc"]


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    ts = pd.to_datetime(v, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts.strftime("%Y-%m-%d")


def _first(v: Any) -> Optional[str]:
    if isinstance(v, list):
        return v[0] if v else None
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
        plat = entry.get("platform") or {}
        recs.append({
            "cmc_id": entry.get("id"),
            "symbol": entry.get("symbol"),
            "name": entry.get("name"),
            "category": entry.get("category"),
            "date_launched": _date(entry.get("date_launched")),
            "date_added": _date(entry.get("date_added")),
            "tags": ";".join(entry.get("tags") or []) or None,
            "platform_name": (plat or {}).get("name"),
            "platform_token_address": (plat or {}).get("token_address"),
            "website": _first(urls.get("website")),
            "technical_doc": _first(urls.get("technical_doc")),
        })
    return recs


def run(client, base_dir: Path, map_path: Path) -> dict:
    out_dir = Path(base_dir) / DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAME

    m = pd.read_parquet(map_path)
    ids = [int(x) for x in m[m["is_active"] == True]["cmc_id"].dropna().tolist()]  # noqa: E712

    buf: List[Dict[str, Any]] = []
    sample_saved = False
    errors: List[Dict[str, str]] = []
    attempted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        attempted += 1
        try:
            payload = client.get("/v2/cryptocurrency/info",
                                 params={"id": ",".join(map(str, chunk)),
                                         "skip_invalid": "true"})
        except Exception as exc:
            errors.append({"ids": f"{chunk[0]}..{chunk[-1]}", "error": str(exc)[:200]})
            continue
        if not sample_saved:
            save_raw_sample(out_dir, "info_sample.json", payload)
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
        symbol_count=int(full["symbol"].nunique()), generated_by=GENERATED_BY,
        run={"ids_requested": len(ids), "coins_with_metadata": int(full["cmc_id"].nunique()),
             "errors": errors[:20], "error_count": len(errors)},
    )
    print(f"[info] rows={row_count} coins={full['cmc_id'].nunique()} errors={len(errors)}",
          flush=True)
    return {"row_count": row_count, "coins": int(full["cmc_id"].nunique())}


if __name__ == "__main__":
    from ..client import CMCClient
    REPO_ROOT = Path(__file__).resolve().parents[3]
    BASE = REPO_ROOT / "cmc_complete" / "data" / "coinmarketcap_data"
    run(CMCClient(), BASE, BASE / "pro_api_map" / "cmc_map.parquet")
