#!/usr/bin/env python3
"""
build_cmc_web_history.py — survivorship-bias-FREE point-in-time universe rankings
=================================================================================

Ingests CoinMarketCap's PUBLIC, keyless historical-listings data-API
(``https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical``)
into a single tidy Parquet of point-in-time top-N rankings.

Why this source
---------------
On the CMC *Pro* API (Hobbyist), ``/v1/cryptocurrency/listings/historical`` (the
only Pro endpoint giving true PIT membership incl. since-delisted coins) is
HTTP-400 blocked. The PUBLIC data-API that powers ``coinmarketcap.com/historical``
returns the SAME information for free — the full top-N ranking AS OF any date back
to 2013-05-05 — including coins that were ranked then but have since collapsed or
delisted. That is precisely what eliminates survivorship bias.

Each row also carries the true ``dateAdded`` (first-listing date — enables a
point-in-time-correct 365-day maturity gate), CMC category ``tags`` (accurate
stablecoin/wrapped/LST/RWA classification), the stable ``cmc_id``, and
``numMarketPairs`` (a PIT liquidity/tradability proxy).

Integrity properties
---------------------
* survivorship_bias_free = True, includes_inactive_delisted = True.
* No synthetic/fabricated rows. Every row is parsed from a real API response.
  Dates that fail to return a credible list are recorded as failures and skipped
  — never back-filled or invented.
* Resumable + polite: each date's raw JSON is cached; re-runs skip cached dates;
  live calls are rate-limited.

Output
------
``data/external/cmc_web/cmc_web_listings_historical.parquet`` with columns:
    snapshot_date (UTC) | cmc_id | rank | symbol | name | slug |
    market_cap_usd | price_usd | volume_24h_usd |
    circulating_supply | total_supply | max_supply | num_market_pairs |
    date_added (UTC) | raw_category_tags (list[str]) | source
plus ``cmc_web_history_manifest.json``.

Usage
-----
    python3 scripts/build_cmc_web_history.py --start 2021-01-01 --end 2026-06-01 --top 300 --freq monthly
    python3 scripts/build_cmc_web_history.py --start 2025-01-01 --top 500 --freq weekly
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_NAME = "coinmarketcap_web_historical"
DATA_API = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical"
    "?date={date}&start={start}&limit={limit}&convert=USD"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_PAGE = 1000  # data-api hard cap per request


# --------------------------------------------------------------------------- io
def _http_get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
    )
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return json.loads(raw)


def _fetch_snapshot(date_str: str, top_n: int, timeout: float) -> List[Dict[str, Any]]:
    """Fetch up to top_n rows for a date, paginating in pages of <=1000."""
    rows: List[Dict[str, Any]] = []
    start = 1
    while len(rows) < top_n:
        limit = min(_MAX_PAGE, top_n - len(rows))
        payload = _http_get_json(DATA_API.format(date=date_str, start=start, limit=limit), timeout)
        chunk = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return rows


# ---------------------------------------------------------------------- parsing
def _f(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_snapshot(rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp, top_n: int, min_rows: int) -> pd.DataFrame:
    recs: List[Dict[str, Any]] = []
    for r in rows:
        quotes = r.get("quotes") or [{}]
        q = quotes[0] if quotes else {}
        symbol = str(r.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        mcap = _f(q.get("marketCap"))
        if not (mcap > 0):
            continue
        recs.append(
            {
                "snapshot_date": snapshot_date,
                "cmc_id": pd.to_numeric(r.get("id"), errors="coerce"),
                "rank": pd.to_numeric(r.get("cmcRank"), errors="coerce"),
                "symbol": symbol,
                "name": str(r.get("name") or symbol),
                "slug": str(r.get("slug") or "").lower(),
                "market_cap_usd": mcap,
                "price_usd": _f(q.get("price")),
                "volume_24h_usd": _f(q.get("volume24h")),
                "circulating_supply": _f(r.get("circulatingSupply")),
                "total_supply": _f(r.get("totalSupply")),
                "max_supply": _f(r.get("maxSupply")),
                "num_market_pairs": pd.to_numeric(r.get("numMarketPairs"), errors="coerce"),
                "date_added": r.get("dateAdded"),
                "raw_category_tags": [str(t) for t in (r.get("tags") or [])],
                "source": SOURCE_NAME,
            }
        )
    df = pd.DataFrame(recs)
    if len(df) < min_rows:
        raise ValueError(f"only {len(df)} valid rows (< min_rows={min_rows}); response not a credible snapshot")
    df["cmc_id"] = df["cmc_id"].astype("Int64")
    df["rank"] = df["rank"].astype("Int64")
    df["date_added"] = pd.to_datetime(df["date_added"], utc=True, errors="coerce")
    df = df.drop_duplicates(subset=["cmc_id"], keep="first").sort_values("rank").head(top_n).reset_index(drop=True)
    return df


# ----------------------------------------------------------------- date helpers
def _candidate_dates(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> List[pd.Timestamp]:
    if freq == "weekly":
        d = start + pd.Timedelta(days=(6 - start.weekday()) % 7)  # first Sunday >= start
        out = []
        while d <= end:
            out.append(d)
            d += pd.Timedelta(days=7)
        return out
    return list(pd.date_range(start=start.replace(day=1), end=end, freq="MS", tz="UTC"))


# ------------------------------------------------------------------------- main
def build(args: argparse.Namespace) -> int:
    start = pd.Timestamp(args.start, tz="UTC").normalize()
    end = pd.Timestamp(args.end, tz="UTC").normalize() if args.end else pd.Timestamp.now(tz="UTC").normalize()
    out_dir = PROJECT_ROOT / "data" / "external" / "cmc_web"
    cache_dir = PROJECT_ROOT / (args.cache_dir or "data/cache/cmc_web")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    targets = [d for d in _candidate_dates(start, end, args.freq) if d <= pd.Timestamp.now(tz="UTC")]
    if not targets:
        print(f"[cmc-web] No snapshot dates in range {start.date()}..{end.date()}")
        return 1

    frames: List[pd.DataFrame] = []
    failures: List[Dict[str, str]] = []
    fetched_live = 0
    print(f"[cmc-web] {len(targets)} target snapshot(s), freq={args.freq}, top={args.top}")

    for target in targets:
        date_str = target.strftime("%Y-%m-%d")
        cache_file = cache_dir / f"{target.strftime('%Y%m%d')}_top{args.top}.json"
        rows: Optional[List[Dict[str, Any]]] = None
        if cache_file.exists() and not args.force_refresh:
            try:
                rows = json.loads(cache_file.read_text())
            except Exception:
                rows = None
        if rows is None:
            try:
                rows = _fetch_snapshot(date_str, args.top, args.timeout)
                cache_file.write_text(json.dumps(rows))
                fetched_live += 1
                time.sleep(args.min_seconds)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                failures.append({"date": date_str, "stage": "fetch", "error": str(exc)})
                print(f"[cmc-web]   MISS {date_str}: fetch error {exc}")
                if args.fail_on_missing_snapshot:
                    return 1
                continue
        try:
            parsed = parse_snapshot(rows, target, args.top, args.min_rows)
        except Exception as exc:
            failures.append({"date": date_str, "stage": "parse", "error": str(exc)})
            print(f"[cmc-web]   MISS {date_str}: {exc}")
            if args.fail_on_missing_snapshot:
                return 1
            continue
        frames.append(parsed)
        print(f"[cmc-web]   OK   {date_str}  rows={len(parsed)}  top={parsed.iloc[0]['symbol']}  delisted_safe")

    if not frames:
        print("[cmc-web] ERROR: no snapshots parsed; refusing to write empty/fake output")
        return 1

    full = pd.concat(frames, ignore_index=True)
    full = full.drop_duplicates(subset=["snapshot_date", "cmc_id"], keep="first")
    full = full.sort_values(["snapshot_date", "rank"]).reset_index(drop=True)

    out_path = out_dir / "cmc_web_listings_historical.parquet"
    full.to_parquet(out_path, index=False)

    snaps = sorted(full["snapshot_date"].dt.strftime("%Y-%m-%d").unique().tolist())
    manifest = {
        "source": SOURCE_NAME,
        "endpoint": DATA_API.split("?")[0],
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start": start.date().isoformat(),
        "requested_end": end.date().isoformat(),
        "freq": args.freq,
        "top_n": int(args.top),
        "snapshots_built": len(snaps),
        "snapshot_dates": snaps,
        "actual_start": snaps[0] if snaps else None,
        "actual_end": snaps[-1] if snaps else None,
        "total_rows": int(len(full)),
        "unique_symbols": int(full["symbol"].nunique()),
        "unique_cmc_ids": int(full["cmc_id"].nunique()),
        "survivorship_bias_free": True,
        "includes_inactive_delisted": True,
        "synthetic_data": False,
        "live_pages_fetched": fetched_live,
        "failure_count": len(failures),
        "failures": failures[:50],
        "output_file": str(out_path),
    }
    with open(out_dir / "cmc_web_history_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=str)

    print(
        f"[cmc-web] DONE  snapshots={len(snaps)} ({snaps[0]}..{snaps[-1]})  "
        f"rows={len(full)}  unique_symbols={full['symbol'].nunique()}  unique_cmc_ids={full['cmc_id'].nunique()}  "
        f"failures={len(failures)}"
    )
    print(f"[cmc-web] wrote {out_path}")
    print("[cmc-web] Next: universe source=cmc_web_pit consumes this dataset.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Build survivorship-free PIT universe from CMC public data-API.")
    p.add_argument("--start", required=True, help="First snapshot date (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="Last snapshot date (YYYY-MM-DD); default = today")
    p.add_argument("--top", type=int, default=300, help="Keep top-N by market cap per snapshot")
    p.add_argument("--freq", choices=["weekly", "monthly"], default="monthly", help="Snapshot cadence")
    p.add_argument("--min-seconds", type=float, default=2.5, dest="min_seconds", help="Polite delay between live calls")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--min-rows", type=int, default=50, dest="min_rows", help="Reject responses with fewer valid rows")
    p.add_argument("--cache-dir", default=None, dest="cache_dir")
    p.add_argument("--force-refresh", action="store_true", dest="force_refresh")
    p.add_argument("--fail-on-missing-snapshot", action="store_true", dest="fail_on_missing_snapshot")
    args = p.parse_args()
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
