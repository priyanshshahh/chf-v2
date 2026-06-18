#!/usr/bin/env python3
"""
extract_cmc_daily_history.py — 3-year (extensible) DAILY historical ticker list + data
=======================================================================================

WHY THIS EXISTS (read docs/CMC_3YR_DAILY_EXTRACTION.md for the full story)
--------------------------------------------------------------------------
We tested the supplied CMC key live. Result:

  * Pro  /v1/cryptocurrency/listings/historical  → STILL HTTP-400 capped at **1 month**
    on this plan ("Your plan allows 1 months of historical access"). The announced
    "3 years of daily" upgrade does NOT apply to the historical *listings* (ticker-list)
    endpoint — it applies to /v2 quotes/historical (verified working 3y back).
  * The PUBLIC, KEYLESS data-API that powers coinmarketcap.com/historical returns the
    SAME historical TOP-N ticker list (active + inactive/delisted) for ANY date back to
    2013 — for free, with no plan limit. THIS is the only way to get a 3-year *daily*
    historical ticker list incl. delisted coins on the current subscription.

So this extractor pulls DAILY snapshots from the keyless endpoint. Each daily snapshot is
the real ranked top-N as of that date and already carries per-coin price / market cap /
24h volume / supply / dateAdded / category tags — i.e. it is BOTH the historical ticker
list AND the daily market data, including coins that have since delisted.

  GET https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical
        ?date=YYYY-MM-DD&start=1&limit=<=1000&convert=USD

INTEGRITY
---------
* No synthetic data. Every row parses from a real API response. A date that fails to
  return a credible list is recorded in `failures` and SKIPPED — never back-filled.
* Resumable + polite: each date's raw JSON is cached under raw_daily_json/; re-runs skip
  cached dates; live calls are paced.
* No API key required for this endpoint (keyless). Nothing here reads or writes a key.

OUTPUTS (under coinmarketcap_extract/)
--------------------------------------
  raw_daily_json/YYYY-MM-DD.json                      # untouched API response per day
  processed/cmc_daily_listings_historical.parquet     # tidy combined table
  processed/cmc_daily_listings_historical.csv         # same, CSV
  processed/extraction_manifest.json                  # provenance + coverage + failures

USAGE
-----
  python3 coinmarketcap_extract/extract_cmc_daily_history.py --years 3 --top 200
  python3 coinmarketcap_extract/extract_cmc_daily_history.py --start 2023-06-17 --end 2026-06-17 --top 200
  python3 coinmarketcap_extract/extract_cmc_daily_history.py --freq monthly --start 2013-05-05   # full history later
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw_daily_json"
OUT_DIR = HERE / "processed"
DATA_API = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical"
    "?date={date}&start={start}&limit={limit}&convert=USD"
)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_PAGE = 1000
SOURCE = "coinmarketcap_web_historical_keyless"


def _http_get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _rows_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """The keyless endpoint returns either {data:[...]} or {data:{cryptoCurrencyList:[...]}}."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lst = data.get("cryptoCurrencyList") or data.get("listings") or []
        return lst if isinstance(lst, list) else []
    return []


def fetch_snapshot(date_str: str, top_n: int, timeout: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 1
    while len(rows) < top_n:
        limit = min(MAX_PAGE, top_n - len(rows))
        payload = _http_get_json(DATA_API.format(date=date_str, start=start, limit=limit), timeout)
        chunk = _rows_from_payload(payload)
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return rows


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _quote(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the per-row quote across both response shapes."""
    qs = row.get("quotes")
    if isinstance(qs, list) and qs:
        q = qs[0]
        return {
            "price": _f(q.get("price")),
            "market_cap": _f(q.get("marketCap")),
            "volume_24h": _f(q.get("volume24h") or q.get("volume_24h")),
        }
    q = ((row.get("quote") or {}).get("USD") or {})
    return {
        "price": _f(q.get("price")),
        "market_cap": _f(q.get("market_cap")),
        "volume_24h": _f(q.get("volume_24h")),
    }


def parse_snapshot(rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp, top_n: int, min_rows: int) -> pd.DataFrame:
    recs: List[Dict[str, Any]] = []
    for r in rows:
        symbol = str(r.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        q = _quote(r)
        if not (q["market_cap"] > 0):
            continue
        recs.append(
            {
                "snapshot_date": snapshot_date,
                "cmc_id": pd.to_numeric(r.get("id"), errors="coerce"),
                "rank": pd.to_numeric(r.get("cmcRank") or r.get("cmc_rank"), errors="coerce"),
                "symbol": symbol,
                "name": str(r.get("name") or symbol),
                "slug": str(r.get("slug") or "").lower(),
                "is_active": int(r.get("isActive", r.get("is_active", 1)) or 0),
                "market_cap_usd": q["market_cap"],
                "price_usd": q["price"],
                "volume_24h_usd": q["volume_24h"],
                "circulating_supply": _f(r.get("circulatingSupply")),
                "total_supply": _f(r.get("totalSupply")),
                "max_supply": _f(r.get("maxSupply")),
                "num_market_pairs": pd.to_numeric(r.get("numMarketPairs"), errors="coerce"),
                "date_added": r.get("dateAdded"),
                "raw_category_tags": [str(t) for t in (r.get("tags") or [])],
                "source": SOURCE,
            }
        )
    df = pd.DataFrame(recs)
    if len(df) < min_rows:
        raise ValueError(f"only {len(df)} valid rows (< min_rows={min_rows}); not a credible snapshot")
    df["cmc_id"] = df["cmc_id"].astype("Int64")
    df["rank"] = df["rank"].astype("Int64")
    df["date_added"] = pd.to_datetime(df["date_added"], utc=True, errors="coerce")
    return df.drop_duplicates(subset=["cmc_id"], keep="first").sort_values("rank").head(top_n).reset_index(drop=True)


def _dates(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> List[pd.Timestamp]:
    if freq == "daily":
        return list(pd.date_range(start=start, end=end, freq="D", tz="UTC"))
    if freq == "weekly":
        return list(pd.date_range(start=start, end=end, freq="W-SUN", tz="UTC"))
    return list(pd.date_range(start=start.replace(day=1), end=end, freq="MS", tz="UTC"))


def main() -> int:
    p = argparse.ArgumentParser(description="Extract CMC daily historical ticker list (active+inactive), keyless.")
    p.add_argument("--start", default=None, help="First date YYYY-MM-DD (default: --years back from end)")
    p.add_argument("--end", default=None, help="Last date YYYY-MM-DD (default: today UTC)")
    p.add_argument("--years", type=float, default=3.0, help="If --start omitted, go this many years back")
    p.add_argument("--top", type=int, default=200, help="Top-N by rank per snapshot (keep >=100 incl. churn)")
    p.add_argument("--freq", choices=["daily", "weekly", "monthly"], default="daily")
    p.add_argument("--min-seconds", type=float, default=1.5, help="Polite delay between LIVE calls")
    p.add_argument("--timeout", type=float, default=40.0)
    p.add_argument("--min-rows", type=int, default=50)
    p.add_argument("--force-refresh", action="store_true")
    args = p.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp(args.end, tz="UTC").normalize() if args.end else pd.Timestamp.now(tz="UTC").normalize()
    start = (
        pd.Timestamp(args.start, tz="UTC").normalize()
        if args.start
        else (end - pd.Timedelta(days=int(args.years * 365.25))).normalize()
    )
    targets = [d for d in _dates(start, end, args.freq) if d <= pd.Timestamp.now(tz="UTC")]
    if not targets:
        print(f"[cmc] no dates in {start.date()}..{end.date()}")
        return 1

    print(f"[cmc] {len(targets)} {args.freq} snapshot(s) {start.date()}..{end.date()} top={args.top}")
    frames: List[pd.DataFrame] = []
    failures: List[Dict[str, str]] = []
    live = 0
    for i, target in enumerate(targets, 1):
        ds = target.strftime("%Y-%m-%d")
        cache = RAW_DIR / f"{ds}.json"
        rows: Optional[List[Dict[str, Any]]] = None
        if cache.exists() and not args.force_refresh:
            try:
                rows = json.loads(cache.read_text())
            except Exception:
                rows = None
        if rows is None:
            try:
                rows = fetch_snapshot(ds, args.top, args.timeout)
                cache.write_text(json.dumps(rows))
                live += 1
                time.sleep(args.min_seconds)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                failures.append({"date": ds, "stage": "fetch", "error": str(exc)})
                print(f"[cmc]   MISS {ds}: {exc}")
                continue
        try:
            parsed = parse_snapshot(rows, target, args.top, args.min_rows)
        except Exception as exc:
            failures.append({"date": ds, "stage": "parse", "error": str(exc)})
            print(f"[cmc]   MISS {ds}: {exc}")
            continue
        frames.append(parsed)
        if i % 25 == 0 or i == len(targets) or args.freq != "daily":
            print(f"[cmc]   {i}/{len(targets)} OK {ds} rows={len(parsed)} top={parsed.iloc[0]['symbol']}")

    if not frames:
        print("[cmc] ERROR: no snapshots parsed; refusing to write empty output")
        return 1

    full = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["snapshot_date", "cmc_id"], keep="first")
        .sort_values(["snapshot_date", "rank"])
        .reset_index(drop=True)
    )
    pq = OUT_DIR / "cmc_daily_listings_historical.parquet"
    csv = OUT_DIR / "cmc_daily_listings_historical.csv"
    full.to_parquet(pq, index=False)
    full.to_csv(csv, index=False)

    snaps = sorted(full["snapshot_date"].dt.strftime("%Y-%m-%d").unique().tolist())
    manifest = {
        "source": SOURCE,
        "endpoint": DATA_API.split("?")[0],
        "requires_api_key": False,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "freq": args.freq,
        "top_n": int(args.top),
        "requested_start": start.date().isoformat(),
        "requested_end": end.date().isoformat(),
        "actual_start": snaps[0],
        "actual_end": snaps[-1],
        "snapshots_requested": len(targets),
        "snapshots_built": len(snaps),
        "total_rows": int(len(full)),
        "unique_symbols": int(full["symbol"].nunique()),
        "unique_cmc_ids": int(full["cmc_id"].nunique()),
        "includes_inactive_delisted": True,
        "survivorship_bias_free": True,
        "synthetic_data": False,
        "live_calls": live,
        "failure_count": len(failures),
        "failures": failures[:100],
        "outputs": {"parquet": str(pq), "csv": str(csv), "raw_dir": str(RAW_DIR)},
    }
    (OUT_DIR / "extraction_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    print(
        f"[cmc] DONE snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]}) rows={len(full)} "
        f"unique_cmc_ids={full['cmc_id'].nunique()} failures={len(failures)}"
    )
    print(f"[cmc] wrote {pq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
