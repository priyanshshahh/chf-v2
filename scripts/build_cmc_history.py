#!/usr/bin/env python3
"""
build_cmc_history.py — download REAL point-in-time history from CoinMarketCap.
=============================================================================

This is the survivorship-bias-free data source. It uses CoinMarketCap's
`/v1/cryptocurrency/listings/historical` endpoint to capture the ranked top-N
universe *as it existed* at the first of each month — including coins that were
active then but have since gone inactive/delisted (`cryptocurrency_type=all`).
It then pulls `/v2/cryptocurrency/ohlcv/historical` (daily) for the union of all
coins that ever appeared, giving real OHLCV for delisted names too.

It reuses the existing `providers/coinmarketcap.py` (no changes to existing code).
The companion `agents/universe_agent_cmc.py` turns the downloaded listings into the
standard universe artifacts.

PLAN LIMITS (read the CMC docs):
  * `listings/historical` history depth is plan-gated:
      Hobbyist = 1 month, Standard = 3 months, Professional = 12 months,
      Enterprise = up to 6 years.  ← this is the binding limit for PIT membership.
  * `ohlcv/historical` daily depth is more generous (Hobbyist ~12 months).
  * Rate limit (Hobbyist) = 30 req/min → this script paces requests accordingly.
  * Credits: listings/historical = 1 per 100 coins; ohlcv/historical = 1 per 100
    data points. The script prints your plan + credit usage from /v1/key/info first.

Everything is cached on disk (data/cache/cmc_history) and saved as raw JSON +
normalized Parquet, so runs are resumable and fully reproducible.

Requires CMC_API_KEY in .env or the environment.

Usage:
    python3 scripts/build_cmc_history.py --start 2025-06-01 --end 2026-06-01 --top 100
    python3 scripts/build_cmc_history.py --months 1 --top 100         # Hobbyist test (1 month)
    python3 scripts/build_cmc_history.py --check-plan                 # just show plan limits
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

from providers.coinmarketcap import CoinMarketCapProvider, CoinMarketCapProviderError  # noqa: E402


def _utc(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fetch_key_info(provider: CoinMarketCapProvider, api_key: str) -> Dict[str, Any]:
    """Free /v1/key/info call — shows the plan, rate limits, and credit usage."""
    return provider.http.get_json(
        provider="coinmarketcap",
        url="https://pro-api.coinmarketcap.com/v1/key/info",
        params={},
        cache_key="key_info",
        force_refresh=True,
        live_api_enabled=True,
        headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
    )


def print_plan(info: Dict[str, Any]) -> None:
    data = (info or {}).get("data", {})
    plan = data.get("plan", {})
    usage = data.get("usage", {})
    print("[cmc] Plan:", plan.get("name") or "unknown")
    print(f"[cmc] Rate limit/min: {plan.get('rate_limit_minute')} | "
          f"monthly credit limit: {plan.get('credit_limit_monthly')}")
    cur = (usage.get("current_month") or {})
    print(f"[cmc] Credits used this month: {cur.get('credits_used')} / "
          f"{cur.get('credits_left')} left")
    print("[cmc] NOTE: listings/historical depth is plan-gated "
          "(Hobbyist=1mo, Standard=3mo, Professional=12mo, Enterprise=up to 6yr).")


def month_starts(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    return list(pd.date_range(start=start.replace(day=1), end=end.replace(day=1), freq="MS", tz="UTC"))


def main() -> int:
    p = argparse.ArgumentParser(description="Download real point-in-time CMC history.")
    p.add_argument("--start", default=None, help="First snapshot month YYYY-MM-01")
    p.add_argument("--end", default=None, help="Last snapshot month YYYY-MM-01 (default: today)")
    p.add_argument("--months", type=int, default=None, help="Alternatively: N months back from end")
    p.add_argument("--top", type=int, default=100, help="Top-N by cmc_rank per snapshot")
    p.add_argument("--convert", default="USD")
    p.add_argument("--out-dir", default="data/external/cmc")
    p.add_argument("--min-seconds", type=float, default=2.1, help="Pacing (30 req/min default)")
    p.add_argument("--no-ohlcv", action="store_true", help="Only fetch listings, skip OHLCV")
    p.add_argument("--max-ohlcv-coins", type=int, default=None, help="Cap number of coins for OHLCV")
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--check-plan", action="store_true", help="Only print plan/limits and exit")
    args = p.parse_args()

    api_key = os.getenv("CMC_API_KEY")
    if not api_key:
        print("[cmc] ERROR: CMC_API_KEY not set. Add it to .env "
              "(get a key at pro.coinmarketcap.com).", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = PROJECT_ROOT / "data" / "cache" / "cmc_history"

    provider = CoinMarketCapProvider(
        cache_dir=cache_dir,
        api_key=api_key,
        request_timeout_seconds=30,
        min_seconds_between_requests=float(args.min_seconds),
        max_retries=6,
        backoff_base_seconds=3,
        backoff_jitter_seconds=1.5,
        live_api_enabled=True,
        force_refresh=bool(args.force_refresh),
    )

    try:
        info = fetch_key_info(provider, api_key)
        print_plan(info)
    except Exception as exc:  # noqa: BLE001
        print(f"[cmc] WARNING: could not read /v1/key/info ({exc}). Check your key.", file=sys.stderr)
    if args.check_plan:
        return 0

    end = _utc(args.end) if args.end else pd.Timestamp.now(tz="UTC")
    if args.start:
        start = _utc(args.start)
    elif args.months:
        start = end - pd.DateOffset(months=int(args.months))
    else:
        start = end - pd.DateOffset(months=1)  # safe default within Hobbyist window
    dates = month_starts(start, end)
    print(f"[cmc] Snapshots requested: {len(dates)} months "
          f"({dates[0].date()} -> {dates[-1].date()}), top {args.top} by cmc_rank")

    # ---- Listings Historical (point-in-time membership, incl. inactive) ----
    listing_frames: List[pd.DataFrame] = []
    reachable: List[pd.Timestamp] = []
    plan_blocked = False
    for d in dates:
        try:
            df = provider.fetch_historical_listings(
                snapshot_date=d, start=1, limit=int(args.top), convert=args.convert,
                live_api_enabled=True, force_refresh=bool(args.force_refresh),
            )
        except CoinMarketCapProviderError as exc:
            print(f"[cmc]   {d.date()} skipped (plan/limit): {str(exc)[:120]}")
            plan_blocked = True
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[cmc]   {d.date()} failed: {str(exc)[:120]}")
            continue
        if df.empty:
            print(f"[cmc]   {d.date()} returned no rows (likely beyond plan history window)")
            plan_blocked = True
            continue
        df = df.copy()
        df["snapshot_date"] = d
        listing_frames.append(df)
        reachable.append(d)
        # Save raw snapshot for full provenance.
        (raw_dir / f"listings_{d.date()}.json").write_text(
            df.to_json(orient="records", date_format="iso")
        )
        print(f"[cmc]   {d.date()} OK | {len(df)} coins | "
              f"active={int(df['is_active_at_snapshot'].sum())} "
              f"inactive={int((~df['is_active_at_snapshot'].astype(bool)).sum())}")

    if not listing_frames:
        print("[cmc] ERROR: no historical listings retrieved. On Hobbyist, listings/historical "
              "only reaches ~1 month back — request a recent month or upgrade your plan.",
              file=sys.stderr)
        return 1

    listings = pd.concat(listing_frames, ignore_index=True)
    listings_path = out_dir / "cmc_listings_historical.parquet"
    listings.to_parquet(listings_path, index=False)
    n_unique = int(listings["cmc_id"].nunique())
    print(f"[cmc] Listings saved: {listings_path} | {len(listings)} rows | "
          f"{len(reachable)} snapshots | {n_unique} unique coins (PIT membership)")
    if plan_blocked:
        print("[cmc] NOTE: some requested months were beyond your plan's history window "
              "and were skipped. Upgrade the plan for deeper history.")

    # ---- OHLCV Historical for the union of all coins (incl. delisted) ----
    ohlcv_path = out_dir / "cmc_ohlcv_historical.parquet"
    if not args.no_ohlcv:
        coins = (
            listings[["cmc_id", "symbol"]]
            .dropna(subset=["cmc_id"]).drop_duplicates("cmc_id")
            .astype({"cmc_id": "int64"})
        )
        if args.max_ohlcv_coins:
            coins = coins.head(int(args.max_ohlcv_coins))
        time_start = reachable[0] - pd.Timedelta(days=1)  # exclusive start per CMC docs
        time_end = reachable[-1] + pd.offsets.MonthEnd(0)
        print(f"[cmc] Fetching daily OHLCV for {len(coins)} coins "
              f"({time_start.date()} -> {time_end.date()})...")
        ohlcv_frames: List[pd.DataFrame] = []
        failures: List[str] = []
        for i, row in enumerate(coins.itertuples(), 1):
            try:
                odf = provider.fetch_ohlcv_historical(
                    cmc_id=int(row.cmc_id), symbol=str(row.symbol),
                    time_start=time_start, time_end=time_end,
                    interval="daily", convert=args.convert,
                    live_api_enabled=True, force_refresh=bool(args.force_refresh),
                )
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{row.symbol}: {str(exc)[:80]}")
                continue
            if not odf.empty:
                ohlcv_frames.append(odf)
            if i % 25 == 0 or i == len(coins):
                rows = sum(len(f) for f in ohlcv_frames)
                print(f"[cmc]   OHLCV {i}/{len(coins)} coins | {rows} rows | failures: {len(failures)}")
        if ohlcv_frames:
            ohlcv = pd.concat(ohlcv_frames, ignore_index=True)
            ohlcv.to_parquet(ohlcv_path, index=False)
            print(f"[cmc] OHLCV saved: {ohlcv_path} | {len(ohlcv)} rows | "
                  f"{ohlcv['symbol'].nunique()} coins")
        else:
            print("[cmc] WARNING: no OHLCV rows fetched (plan window / rate limits).")
        if failures:
            print(f"[cmc] {len(failures)} OHLCV failures: {failures[:5]}{' ...' if len(failures) > 5 else ''}")

    # ---- Manifest ----
    manifest = {
        "source": "coinmarketcap",
        "endpoint_listings": "/v1/cryptocurrency/listings/historical",
        "endpoint_ohlcv": "/v2/cryptocurrency/ohlcv/historical",
        "convert": args.convert,
        "top_n": int(args.top),
        "requested_start": dates[0].date().isoformat(),
        "requested_end": dates[-1].date().isoformat(),
        "actual_start": reachable[0].date().isoformat(),
        "actual_end": reachable[-1].date().isoformat(),
        "snapshots_requested": len(dates),
        "snapshots_retrieved": len(reachable),
        "unique_coins_pit_membership": n_unique,
        "plan_history_window_hit": plan_blocked,
        "survivorship_bias_free": True,
        "includes_inactive_delisted": True,
        "files": {
            "listings": str(listings_path),
            "ohlcv": str(ohlcv_path) if not args.no_ohlcv else None,
            "raw_dir": str(raw_dir),
        },
    }
    (out_dir / "cmc_history_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(f"[cmc] Manifest: {out_dir / 'cmc_history_manifest.json'}")
    print("[cmc] Next: python3 agents/universe_agent_cmc.py "
          f"--listings {listings_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
