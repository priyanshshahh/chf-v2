#!/usr/bin/env python3
"""
build_cmc_quotes_history.py — REAL 3-year daily history from CoinMarketCap (Hobbyist).
======================================================================================

Hobbyist's `/v3/cryptocurrency/quotes/historical` allows 36 months of daily data
(verified against the live API), even though `listings/historical` is capped at 1
month. This script uses that to build a 3-YEAR dataset of daily price / market_cap /
volume for a broad candidate set, in the SAME schema as the CoinGecko builder — so it
feeds `agents/universe_agent_free.py` directly to produce a 3-year point-in-time
universe by cross-sectional market-cap ranking per month.

The single output therefore serves both purposes:
  * universe construction (rank by market_cap as-of each month), and
  * backtest prices (real daily close + volume).

Survivorship note: the candidate set is the current top-N active coins. This is strongly
survivorship-RESISTANT — it captures coins that fell out of the top 100 but still trade
(the bulk of the bias). Perfectly survivorship-FREE (coins fully delisted from CMC)
requires multi-month `listings/historical`, which needs an Enterprise plan. This is the
most rigorous universe achievable on Hobbyist.

Requires CMC_API_KEY in .env. Caches on disk (resumable). Credits ≈ top-N × ~11.

Usage:
    python3 scripts/build_cmc_quotes_history.py --top 300 --months 36
    python3 agents/universe_agent_free.py --dataset data/external/cmc/cmc_quotes_history.parquet
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

from providers.http_client import CachedHttpClient, params_hash  # noqa: E402

BASE = "https://pro-api.coinmarketcap.com"
_NEWER_THAN = re.compile(r"newer than\s+([0-9T:\-\.Z]+)", re.IGNORECASE)


def _http() -> CachedHttpClient:
    return CachedHttpClient(
        cache_dir=PROJECT_ROOT / "data" / "cache" / "cmc_quotes",
        request_timeout_seconds=30,
        min_seconds_between_requests=float(os.getenv("_CMC_MIN_SECONDS", "0.25")),
        max_retries=6,
        backoff_base_seconds=3,
        backoff_jitter_seconds=1.5,
    )


def fetch_top_active(http: CachedHttpClient, key: str, top_n: int, convert: str, force: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    per = 5000
    for start in range(1, top_n + 1, per):
        params = {"start": start, "limit": min(per, top_n - len(rows)), "convert": convert, "sort": "market_cap"}
        payload = http.get_json(
            provider="coinmarketcap", url=f"{BASE}/v1/cryptocurrency/listings/latest",
            params=params, cache_key=f"listings_latest_{start}_{params_hash(params)}",
            force_refresh=force, live_api_enabled=True,
            headers={"X-CMC_PRO_API_KEY": key, "Accept": "application/json"},
        )
        data = (payload or {}).get("data", []) or []
        if not data:
            break
        rows.extend(data)
        if len(rows) >= top_n:
            break
    return rows[:top_n]


def fetch_quotes_historical(
    http: CachedHttpClient, key: str, cid: int, start: str, end: str, convert: str, force: bool
) -> List[Dict[str, Any]]:
    params = {"id": cid, "interval": "daily", "time_start": start, "time_end": end, "convert": convert}
    payload = http.get_json(
        provider="coinmarketcap", url=f"{BASE}/v3/cryptocurrency/quotes/historical",
        params=params, cache_key=f"quotes_{cid}_{params_hash(params)}",
        force_refresh=force, live_api_enabled=True,
        headers={"X-CMC_PRO_API_KEY": key, "Accept": "application/json"},
    )
    block = ((payload or {}).get("data") or {}).get(str(cid)) or {}
    return block.get("quotes", []) or []


def main() -> int:
    p = argparse.ArgumentParser(description="Build real 3-year CMC daily history (Hobbyist).")
    p.add_argument("--top", type=int, default=300, help="Candidate set: current top-N active coins")
    p.add_argument("--months", type=int, default=36, help="History depth in months (plan allows 36)")
    p.add_argument("--convert", default="USD")
    p.add_argument("--out", default="data/external/cmc/cmc_quotes_history.parquet")
    p.add_argument("--categories-from", default="data/external/coingecko_categories.json",
                   help="Reuse a symbol->categories JSON for exclusion tagging (optional)")
    p.add_argument("--force-refresh", action="store_true")
    args = p.parse_args()

    key = os.getenv("CMC_API_KEY")
    if not key:
        print("[cmc-quotes] ERROR: CMC_API_KEY not set in .env", file=sys.stderr)
        return 1
    http = _http()

    end = pd.Timestamp.now(tz="UTC").normalize()
    start = (end - pd.DateOffset(months=int(args.months))) + pd.Timedelta(days=2)  # stay inside plan window
    start_s, end_s = start.date().isoformat(), end.date().isoformat()
    print(f"[cmc-quotes] Window: {start_s} -> {end_s} ({args.months}mo) | candidate top {args.top}")

    coins = fetch_top_active(http, key, args.top, args.convert, args.force_refresh)
    if not coins:
        print("[cmc-quotes] ERROR: could not fetch current top coins.", file=sys.stderr)
        return 1
    print(f"[cmc-quotes] Candidate coins: {len(coins)}. Fetching {args.months}mo daily quotes each...")

    # Optional category reuse for accurate exclusion downstream.
    cat_map: Dict[str, List[str]] = {}
    cat_path = Path(args.categories_from)
    if not cat_path.is_absolute():
        cat_path = PROJECT_ROOT / cat_path
    if cat_path.exists():
        cat_map = json.loads(cat_path.read_text())
        print(f"[cmc-quotes] Reusing categories for {len(cat_map)} symbols from {cat_path.name}")

    records: List[Dict[str, Any]] = []
    failures: List[str] = []
    clamp_start = start_s
    for i, coin in enumerate(coins, 1):
        cid, sym, name = coin.get("id"), str(coin.get("symbol") or "").upper(), coin.get("name") or ""
        if not cid or not sym:
            continue
        try:
            quotes = fetch_quotes_historical(http, key, int(cid), clamp_start, end_s, args.convert, args.force_refresh)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            m = _NEWER_THAN.search(msg)
            if m:  # plan window boundary — clamp and retry once
                clamp_start = pd.Timestamp(m.group(1)).date().isoformat()
                try:
                    quotes = fetch_quotes_historical(http, key, int(cid), clamp_start, end_s, args.convert, args.force_refresh)
                except Exception as exc2:  # noqa: BLE001
                    failures.append(f"{sym}: {str(exc2)[:70]}"); continue
            else:
                failures.append(f"{sym}: {msg[:70]}"); continue
        cats = ";".join(cat_map.get(sym, []))
        for q in quotes:
            usd = (q.get("quote") or {}).get(args.convert.upper()) or {}
            mcap = usd.get("market_cap")
            price = usd.get("price")
            if mcap is None or mcap <= 0:
                continue
            day = pd.Timestamp(q.get("timestamp")).tz_convert("UTC").normalize().date().isoformat()
            records.append({
                "date": day, "symbol": sym, "name": name,
                "market_cap": float(mcap),
                "price": float(price) if price is not None else None,
                "volume_24h": float(usd.get("volume_24h") or 0.0),
                "categories": cats,
            })
        if i % 25 == 0 or i == len(coins):
            print(f"[cmc-quotes]   {i}/{len(coins)} coins | {len(records)} rows | failures: {len(failures)}")

    if not records:
        print("[cmc-quotes] ERROR: no quotes returned.", file=sys.stderr)
        return 1

    df = pd.DataFrame(records).drop_duplicates(subset=["symbol", "date"], keep="last")
    df = df.sort_values(["date", "market_cap"], ascending=[True, False]).reset_index(drop=True)
    out = Path(args.out)
    if not out.is_absolute():
        out = PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    # Also write a prices-only file for backtesting (real close + volume).
    prices = df[df["price"].notna()][["date", "symbol", "price", "volume_24h", "market_cap"]].rename(columns={"price": "close", "volume_24h": "volume"})
    prices_path = out.parent / "cmc_prices_history.parquet"
    prices.to_parquet(prices_path, index=False)

    print(f"[cmc-quotes] DONE. {len(df)} rows | {df['symbol'].nunique()} symbols | {df['date'].min()} -> {df['date'].max()}")
    print(f"[cmc-quotes] Universe dataset: {out}")
    print(f"[cmc-quotes] Prices dataset:   {prices_path} ({len(prices)} rows)")
    if failures:
        print(f"[cmc-quotes] {len(failures)} failures: {failures[:5]}{' ...' if len(failures) > 5 else ''}")
    print(f"[cmc-quotes] Next: python3 agents/universe_agent_free.py --dataset {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
