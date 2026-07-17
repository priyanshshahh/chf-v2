#!/usr/bin/env python3
"""categories.py — CMC categories + category membership.

Endpoints:
  /v1/cryptocurrency/categories  (paginate) -> the category directory
  /v1/cryptocurrency/category    (per id)   -> member coin ids

Writes two datasets:
  cmc_categories.parquet         key=category_id
  cmc_category_members.parquet   key=(category_id, cmc_id)  (long/tidy)

Membership pulls are budget-aware: the run polls /v1/key/info (0 credits) and
hard-stops further calls at HARD_STOP_CREDITS, recording how far it got.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _num, save_raw_sample
from ..manifest import write_manifest
from ..storage import upsert

DATASET = "pro_api_categories"
CATS_FILE = "cmc_categories.parquet"
MEMBERS_FILE = "cmc_category_members.parquet"
SOURCE = "pro_api:/v1/cryptocurrency/categories(+/category)"
GENERATED_BY = "src/cmc/collectors/categories.py"
HARD_STOP_CREDITS = 140_000
POLL_EVERY = 50
RESCAN_AFTER_DAYS = 30  # membership freshness: rescan categories older than this
MEMBER_COLUMNS = ["category_id", "cmc_id", "scanned_at_utc"]


def credits_used(client) -> int:
    return int(client.get("/v1/key/info")["data"]["usage"]["current_month"]["credits_used"])


def fetch_category_list(client) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start, limit = 1, 5000
    while True:
        payload = client.get("/v1/cryptocurrency/categories",
                             params={"start": start, "limit": limit})
        chunk = payload.get("data") or []
        out.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return out


def run(client, base_dir: Path, max_membership: Optional[int] = None) -> dict:
    out_dir = Path(base_dir) / DATASET
    out_dir.mkdir(parents=True, exist_ok=True)

    cats = fetch_category_list(client)
    save_raw_sample(out_dir, "categories_sample.json",
                    {"status": {"error_code": 0}, "data": cats[:5]})

    cat_recs = [{
        "category_id": c.get("id"),
        "name": c.get("name"),
        "title": c.get("title"),
        "num_tokens": c.get("num_tokens"),
        "avg_price_change": _num(c.get("avg_price_change")),
        "market_cap": _num(c.get("market_cap")),
        "volume": _num(c.get("volume")),
        "last_updated": c.get("last_updated"),
    } for c in cats]
    cat_df = pd.DataFrame(cat_recs, columns=[
        "category_id", "name", "title", "num_tokens", "avg_price_change",
        "market_cap", "volume", "last_updated"]).drop_duplicates(subset=["category_id"])
    cat_rows = upsert(cat_df, key=["category_id"], path=out_dir / CATS_FILE)

    # Membership freshness: rescan a category if its rows' newest
    # scanned_at_utc is older than RESCAN_AFTER_DAYS (presence alone is not
    # "done forever"). Rows without scanned_at_utc (legacy files) are stale.
    now_utc = datetime.now(timezone.utc)
    members_path = out_dir / MEMBERS_FILE
    done: set = set()
    if members_path.exists():
        existing = pd.read_parquet(members_path)
        if "scanned_at_utc" not in existing.columns:
            # Legacy schema migration: add the column (all rows stale).
            existing["scanned_at_utc"] = None
            existing.to_parquet(members_path, index=False)
        else:
            cutoff = (now_utc - timedelta(days=RESCAN_AFTER_DAYS)).isoformat()
            last_scan = existing.groupby("category_id")["scanned_at_utc"].max()
            done = set(last_scan[last_scan.notna() & (last_scan >= cutoff)].index)
    cat_ids = [c.get("id") for c in cats if c.get("id") not in done]
    if max_membership is not None:
        cat_ids = cat_ids[:max_membership]

    member_buf: List[Dict[str, Any]] = []
    sample_saved = False
    errors: List[Dict[str, str]] = []
    processed = len(done)
    stopped = "completed"
    attempted = 0
    scanned_at = now_utc.isoformat()
    latest = credits_used(client)
    for i, cid in enumerate(cat_ids):
        if i % POLL_EVERY == 0:
            latest = credits_used(client)
            if latest >= HARD_STOP_CREDITS:
                stopped = f"hard_stop_credits>={HARD_STOP_CREDITS}"
                break
        attempted += 1
        try:
            payload = client.get("/v1/cryptocurrency/category",
                                 params={"id": cid, "limit": 1000})
        except Exception as exc:
            errors.append({"category_id": str(cid), "error": str(exc)[:200]})
            continue
        if not sample_saved:
            save_raw_sample(out_dir, "category_members_sample.json", payload)
            sample_saved = True
        for coin in (payload.get("data") or {}).get("coins") or []:
            member_buf.append({"category_id": cid, "cmc_id": coin.get("id"),
                               "scanned_at_utc": scanned_at})
        processed += 1
        if len(member_buf) >= 20000:
            upsert(pd.DataFrame(member_buf, columns=MEMBER_COLUMNS)
                   .drop_duplicates(), key=["category_id", "cmc_id"], path=members_path)
            member_buf = []

    if member_buf:
        upsert(pd.DataFrame(member_buf, columns=MEMBER_COLUMNS)
               .drop_duplicates(), key=["category_id", "cmc_id"], path=members_path)

    # Systemic-failure guard: every attempted membership call erroring means
    # the run produced nothing — surface it instead of reporting "completed".
    if attempted and len(errors) == attempted:
        raise RuntimeError(f"all_batches_failed: {errors[0]['error']}")

    mem_full = pd.read_parquet(members_path) if members_path.exists() else pd.DataFrame(
        columns=["category_id", "cmc_id"])
    latest = credits_used(client)
    write_manifest(
        out_dir=out_dir, dataset=DATASET, source=SOURCE,
        coverage_start=None, coverage_end=None,
        row_count=cat_rows, symbol_count=None, generated_by=GENERATED_BY,
        run={
            "categories_count": cat_rows,
            "membership_rows": len(mem_full),
            "categories_with_members": int(mem_full["category_id"].nunique()) if len(mem_full) else 0,
            "categories_total": len(cats),
            "membership_processed": processed,
            "stopped_reason": stopped,
            "errors": errors[:20], "error_count": len(errors),
            "credits_used_month_after": latest,
        },
    )
    print(f"[categories] cats={cat_rows} members={len(mem_full)} "
          f"cats_with_members={mem_full['category_id'].nunique() if len(mem_full) else 0}/{len(cats)} "
          f"reason={stopped}", flush=True)
    return {"categories": cat_rows, "members": len(mem_full), "stopped": stopped}


if __name__ == "__main__":
    from ..client import CMCClient
    REPO_ROOT = Path(__file__).resolve().parents[3]
    BASE = REPO_ROOT / "cmc_complete" / "data" / "coinmarketcap_data"
    run(CMCClient(), BASE)
