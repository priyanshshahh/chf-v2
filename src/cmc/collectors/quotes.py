#!/usr/bin/env python3
"""quotes.py — full-universe daily historical quotes (survivorship-free).

Endpoint: /v3/cryptocurrency/quotes/historical (interval=daily, convert=USD).
One row per (cmc_id, date): price / market_cap / volume_24h in USD. The universe is
every ``is_active`` coin from the map PLUS any inactive coin whose
``last_historical_data`` is within the 36-month window (delisted-but-in-window).

The paid plan caps historical access at 36 months, so ``time_start`` is pinned to
the rolling window boundary (today - 36 months + 1 day). Coins are batched so that
``coins x days <= 10000`` data points per call (the plan's per-call cap). Progress
is upserted incrementally on (cmc_id, date) so a mid-run stop is fully resumable,
and the run polls /v1/key/info (0 credits) to enforce a hard credit ceiling.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import coerce_float as _f, save_raw_sample
from ..manifest import read_manifest, write_manifest
from ..storage import upsert

DATASET = "pro_api_quotes_historical"
FILENAME = "cmc_quotes_daily_full.parquet"
SOURCE = "pro_api:/v3/cryptocurrency/quotes/historical"
KEY = ["cmc_id", "date"]
GENERATED_BY = "src/cmc/collectors/quotes.py"

DATAPOINT_CAP = 10000          # max (coins x daily-points) per call
FLUSH_EVERY_COINS = 500        # upsert to parquet at least this often
POLL_EVERY_CALLS = 25          # poll key/info (free) this often
HARD_STOP_CREDITS = 140_000    # abort further data calls at/above this


def window_start(end: str) -> str:
    """Return the earliest valid ``time_start`` for the 36-month plan window."""
    end_ts = pd.to_datetime(end)
    return (end_ts - pd.DateOffset(months=36) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def credits_used(client) -> int:
    info = client.get("/v1/key/info")
    return int(info["data"]["usage"]["current_month"]["credits_used"])


def fetch_batch(client, ids: List[int], time_start: str, time_end: str,
                count: int) -> Dict[str, Any]:
    return client.get(
        "/v3/cryptocurrency/quotes/historical",
        params={
            "id": ",".join(str(i) for i in ids),
            "time_start": time_start,
            "time_end": time_end,
            "interval": "daily",
            "convert": "USD",
            "count": count,
            "skip_invalid": "true",
        },
    )


def parse(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    entries = data.values() if isinstance(data, dict) else data
    recs: List[Dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, list):  # some shapes nest a single-item list
            entry = entry[0] if entry else None
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        sym = entry.get("symbol")
        name = entry.get("name")
        for q in entry.get("quotes") or []:
            usd = (q.get("quote") or {}).get("USD") or {}
            ts = pd.to_datetime(q.get("timestamp") or usd.get("timestamp"),
                                utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            recs.append({
                "date": ts.strftime("%Y-%m-%d"),
                "cmc_id": cid,
                "symbol": sym,
                "name": name,
                "price": _f(usd.get("price")),
                "market_cap": _f(usd.get("market_cap")),
                "volume_24h": _f(usd.get("volume_24h")),
            })
    return recs


COLUMNS = ["date", "cmc_id", "symbol", "name", "price", "market_cap", "volume_24h"]


def _flush(buf: List[Dict[str, Any]], path: Path) -> int:
    if not buf:
        return 0
    df = pd.DataFrame(buf, columns=COLUMNS).drop_duplicates(subset=KEY)
    return upsert(df, key=KEY, path=path)


def build_universe(map_path: Path, window_start_date: str) -> pd.DataFrame:
    m = pd.read_parquet(map_path)
    active = m[m["is_active"] == True]  # noqa: E712
    inwin = m[(m["is_active"] == False) &  # noqa: E712
              (m["last_historical_data"].notna()) &
              (m["last_historical_data"] >= window_start_date)]
    uni = pd.concat([active, inwin]).drop_duplicates(subset=["cmc_id"])
    return uni[["cmc_id", "symbol", "name"]].reset_index(drop=True)


def run(client, base_dir: Path, start=None, end=None) -> dict:
    """Standard collector interface (collectors/__init__): delegate to run_full.

    ``map_path`` is derived from ``base_dir`` (the coin map parquet lives under
    the ``pro_api_map`` dataset dir next to this one). ``end`` maps to
    ``time_end``. If the dataset parquet already exists (e.g. monthly
    maintenance), this performs an APPEND pass: fetch [start, end] for ALL
    universe coins and upsert — the (cmc_id, date) key already dedupes, so
    appending a window for every coin is safe and idempotent. The
    presence-based resume (skip coins already in the parquet) applies ONLY to
    the initial full backfill (no parquet yet, or ``run_full`` called
    directly). Returns the dataset manifest dict (row_count / coverage_*).
    """
    base_dir = Path(base_dir)
    map_path = base_dir / "pro_api_map" / "cmc_map.parquet"
    path = base_dir / DATASET / FILENAME
    run_full(client, base_dir, map_path, time_end=end, time_start=start,
             resume_by_presence=not path.exists())
    return read_manifest(base_dir / DATASET) or {}


def run_full(client, base_dir: Path, map_path: Path,
             time_end: Optional[str] = None,
             time_start: Optional[str] = None,
             resume_by_presence: bool = True) -> dict:
    base_dir = Path(base_dir)
    out_dir = base_dir / DATASET
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAME

    time_end = time_end or datetime.now(timezone.utc).date().isoformat()
    plan_floor = window_start(time_end)  # earliest date the 36-month plan allows
    time_start = max(time_start, plan_floor) if time_start else plan_floor
    days = (pd.to_datetime(time_end) - pd.to_datetime(time_start)).days + 1
    batch_size = max(1, DATAPOINT_CAP // days)
    count = days + 5  # request a hair more than the span; server clamps to range

    universe = build_universe(map_path, plan_floor)
    total = len(universe)

    # Resume (initial backfill only): skip coins already present in the output
    # parquet. Append passes re-fetch the window for ALL coins; upsert dedupes.
    done_ids: set = set()
    if resume_by_presence and path.exists():
        existing_ids = pd.read_parquet(path, columns=["cmc_id"])["cmc_id"]
        done_ids = set(int(x) for x in existing_ids.dropna().unique())
    remaining = universe[~universe["cmc_id"].astype("int64").isin(done_ids)]
    remaining_ids = [int(x) for x in remaining["cmc_id"].tolist()]

    credit_start = credits_used(client)
    dataset_credit_start = credit_start

    buf: List[Dict[str, Any]] = []
    sample_saved = path.exists()  # only save a fresh sample if none yet
    calls = 0
    coins_since_flush = 0
    coins_done = len(done_ids)
    errors: List[Dict[str, str]] = []
    stopped_reason = "completed"
    latest_credits = credit_start
    attempted_batches = 0

    for i in range(0, len(remaining_ids), batch_size):
        if calls % POLL_EVERY_CALLS == 0:
            latest_credits = credits_used(client)
            if latest_credits >= HARD_STOP_CREDITS:
                stopped_reason = f"hard_stop_credits>={HARD_STOP_CREDITS}"
                break
        ids = remaining_ids[i:i + batch_size]
        attempted_batches += 1
        try:
            payload = fetch_batch(client, ids, time_start, time_end, count)
        except Exception as exc:  # record + continue; never lose progress
            errors.append({"ids": ",".join(map(str, ids)), "error": str(exc)[:300]})
            calls += 1
            continue
        calls += 1
        if not sample_saved:
            save_raw_sample(out_dir, "quotes_daily_sample.json", payload)
            sample_saved = True
        recs = parse(payload)
        buf.extend(recs)
        coins_done += len(ids)
        coins_since_flush += len(ids)
        if coins_since_flush >= FLUSH_EVERY_COINS:
            _flush(buf, path)
            buf = []
            coins_since_flush = 0
            print(f"[quotes] flushed | coins_done={coins_done}/{total} "
                  f"calls={calls} credits_used={latest_credits}", flush=True)

    _flush(buf, path)  # final flush

    # Systemic-failure guard: every attempted batch erroring means the run
    # produced nothing — surface it instead of reporting "completed".
    if attempted_batches and len(errors) == attempted_batches:
        raise RuntimeError(f"all_batches_failed: {errors[0]['error']}")

    latest_credits = credits_used(client)
    full = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
    dates = full["date"].dropna().astype(str) if len(full) else pd.Series([], dtype=str)
    cov_start = dates.min() if len(dates) else None
    cov_end = dates.max() if len(dates) else None

    run = {
        "universe_rule": "map is_active==True + inactive with last_historical_data>=window_start",
        "time_start": time_start,
        "time_end": time_end,
        "days_in_window": int(days),
        "batch_size_coins": int(batch_size),
        "datapoint_cap": DATAPOINT_CAP,
        "coins_total": int(total),
        "coins_done": int(min(coins_done, total)),
        "coins_with_rows": int(full["cmc_id"].nunique()) if len(full) else 0,
        "api_calls_this_session": calls,
        "credits_used_for_this_dataset": int(latest_credits - dataset_credit_start),
        "credits_used_month_after": int(latest_credits),
        "stopped_reason": stopped_reason,
        "errors": errors[:50],
        "error_count": len(errors),
    }
    write_manifest(
        out_dir=out_dir, dataset=DATASET, source=SOURCE,
        coverage_start=cov_start, coverage_end=cov_end,
        row_count=len(full),
        symbol_count=int(full["symbol"].nunique()) if len(full) else 0,
        generated_by=GENERATED_BY, run=run,
    )
    print(f"[quotes] DONE reason={stopped_reason} rows={len(full)} "
          f"coins_with_rows={run['coins_with_rows']}/{total} "
          f"credits_dataset={run['credits_used_for_this_dataset']} "
          f"credits_month={latest_credits}", flush=True)
    return run


if __name__ == "__main__":
    from ..client import CMCClient

    REPO_ROOT = Path(__file__).resolve().parents[3]
    BASE_DIR = REPO_ROOT / "cmc_complete" / "data" / "coinmarketcap_data"
    MAP_PATH = BASE_DIR / "pro_api_map" / "cmc_map.parquet"
    run_full(CMCClient(), BASE_DIR, MAP_PATH)
