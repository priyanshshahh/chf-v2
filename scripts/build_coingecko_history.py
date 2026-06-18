#!/usr/bin/env python3
"""
build_coingecko_history.py — fetch REAL historical market-cap data from CoinGecko (free).
=========================================================================================

Produces an actual, long-format dataset of daily market capitalisations for the current
top-N cryptocurrencies, suitable for building a point-in-time historical universe with
``agents/universe_agent_free.py``. No synthetic data is generated — every value comes
from the CoinGecko public API.

Real-world limits (CoinGecko free / public tier):
  * Historical depth is capped at the last 365 days. Longer history requires a paid plan;
    this script will only ever contain real data within that window.
  * Rate limits are strict without a key. Set a free CoinGecko *Demo* key as
    COINGECKO_API_KEY (in .env or the shell) to fetch reliably and quickly. Without it,
    the script paces requests slowly and retries on HTTP 429 — it still works, just slower.

Survivorship note: to capture coins that have since dropped out of the top 100, fetch a
WIDE current set (``--top 250`` or more). Coins delisted from CoinGecko entirely cannot
be recovered from this free source; that residual limitation is recorded by the agent.

Caching: every API response is cached on disk (data/cache/coingecko_history), so re-runs
are cheap and the job is resumable if interrupted.

Usage:
    python3 scripts/build_coingecko_history.py --top 250
    python3 scripts/build_coingecko_history.py --top 300 --out data/external/coingecko_history.parquet
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # load COINGECKO_API_KEY from .env if present
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:  # noqa: BLE001
    pass

from providers.coingecko import BASE_URL  # noqa: E402
from providers.http_client import CachedHttpClient, params_hash  # noqa: E402


def fetch_top_coins(http: CachedHttpClient, top_n: int, vs_currency: str, force_refresh: bool) -> List[Dict[str, Any]]:
    """Real current top-N coins by market cap from /coins/markets."""
    rows: List[Dict[str, Any]] = []
    per_page = 250
    pages = (top_n // per_page) + (1 if top_n % per_page else 0)
    for page in range(1, pages + 1):
        params = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": min(per_page, top_n - len(rows)),
            "page": page,
            "sparkline": "false",
        }
        payload = http.get_json(
            provider="coingecko",
            url=f"{BASE_URL}/coins/markets",
            params=params,
            cache_key=f"markets_top_page={page}_{params_hash(params)}",
            force_refresh=force_refresh,
            live_api_enabled=True,
        )
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        if len(rows) >= top_n:
            break
    return rows[:top_n]


def fetch_market_chart(
    http: CachedHttpClient, coin_id: str, vs_currency: str, days: int, force_refresh: bool
) -> Dict[str, list]:
    """Real daily series for one coin from /coins/{id}/market_chart.

    Returns the full payload: {prices, market_caps, total_volumes}. The same cached
    response carries all three, so building prices alongside market caps is free.
    """
    params = {"vs_currency": vs_currency, "days": days, "interval": "daily"}
    payload = http.get_json(
        provider="coingecko",
        url=f"{BASE_URL}/coins/{coin_id}/market_chart",
        params=params,
        cache_key=f"chart_{coin_id}_{params_hash(params)}",
        force_refresh=force_refresh,
        live_api_enabled=True,
    )
    return payload or {}


def _series_to_daily_map(arr: list) -> Dict[str, float]:
    """[[ts_ms, value], ...] -> {YYYY-MM-DD: value}, dropping nulls/non-positives."""
    out: Dict[str, float] = {}
    for point in arr or []:
        if not point or len(point) < 2 or point[1] is None:
            continue
        day = pd.Timestamp(point[0], unit="ms", tz="UTC").normalize().date().isoformat()
        out[day] = float(point[1])
    return out


# Categories whose members should be excluded from a tradable risk-asset universe.
# Precise asset-type categories only. We deliberately DO NOT deny the broad
# "real-world-assets-rwa"/"rwa-protocol" umbrella, because it sweeps in legitimate
# high-volatility infrastructure tokens (e.g. LINK/Chainlink). The actual peg/asset-backed
# instruments are caught by the precise "tokenized-*", "*stablecoin*", and wrapped/staking slugs.
DENY_CATEGORY_PATTERN = re.compile(
    r"stablecoin|wrapped|bridged|binance-peg|liquid-stak|liquid-restak|restaking|restaked|"
    r"\bstaked\b|staked-|tokenized|asset-backed|commodity-backed|peg-token",
    re.IGNORECASE,
)
# Categories that MATCH the deny pattern but are actually legitimate project tokens
# (issuers/governance/protocols/ecosystems/indexes), not peg/asset-backed instruments.
# e.g. "stablecoin-issuer" (AAVE), "liquid-staking-governance-tokens", "bridge-governance-tokens".
SKIP_CATEGORY_PATTERN = re.compile(
    r"issuer|governance|protocol|ecosystem|infrastructure|\bindex\b|narrative",
    re.IGNORECASE,
)


def fetch_deny_category_members(
    http: CachedHttpClient, vs_currency: str, force_refresh: bool
) -> Dict[str, List[str]]:
    """Return {coin_id: [matched deny-category slugs]} using REAL CoinGecko category data.

    Keyed by the UNIQUE CoinGecko coin id (not symbol) to avoid ticker collisions — e.g.
    a bridged token sharing the "BTC" ticker must not tag real Bitcoin (id=bitcoin).
    Only categories matching DENY_CATEGORY_PATTERN are queried.
    """
    listing = http.get_json(
        provider="coingecko",
        url=f"{BASE_URL}/coins/categories/list",
        params={},
        cache_key="categories_list",
        force_refresh=force_refresh,
        live_api_enabled=True,
    )
    deny_ids = [
        c["category_id"]
        for c in (listing or [])
        if DENY_CATEGORY_PATTERN.search(f"{c.get('category_id','')} {c.get('name','')}")
        and not SKIP_CATEGORY_PATTERN.search(f"{c.get('category_id','')} {c.get('name','')}")
    ]
    print(f"[build] Enriching categories: {len(deny_ids)} deny-categories to scan...")
    coin_tags: Dict[str, Set[str]] = {}
    for i, cat_id in enumerate(deny_ids, 1):
        params = {"vs_currency": vs_currency, "category": cat_id, "per_page": 250, "page": 1}
        try:
            members = http.get_json(
                provider="coingecko",
                url=f"{BASE_URL}/coins/markets",
                params=params,
                cache_key=f"category_members_{cat_id}_{params_hash(params)}",
                force_refresh=force_refresh,
                live_api_enabled=True,
            )
        except Exception:  # noqa: BLE001 — deprecated/empty categories are skipped
            continue
        for m in members or []:
            cid = str(m.get("id") or "")
            if cid:
                coin_tags.setdefault(cid, set()).add(cat_id)
        if i % 25 == 0 or i == len(deny_ids):
            print(f"[build]   categories {i}/{len(deny_ids)} | tagged coin ids: {len(coin_tags)}")
    return {cid: sorted(tags) for cid, tags in coin_tags.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch real CoinGecko historical market caps.")
    parser.add_argument("--top", type=int, default=250, help="Number of current top coins to fetch (wider = less survivorship bias)")
    parser.add_argument("--days", type=int, default=365, help="History depth in days (free tier max = 365)")
    parser.add_argument("--vs", default="usd", help="Quote currency")
    parser.add_argument("--out", default="data/external/coingecko_history.parquet", help="Output parquet path")
    parser.add_argument("--min-seconds", type=float, default=2.5, help="Min seconds between API calls (raise if key-less and seeing 429s)")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cache and refetch")
    parser.add_argument("--no-categories", action="store_true", help="Skip real category enrichment (RWA/stablecoin tagging)")
    args = parser.parse_args()

    if args.days > 365:
        print("[build] WARNING: CoinGecko free tier caps history at 365 days; clamping.", file=sys.stderr)
        args.days = 365

    cache_dir = PROJECT_ROOT / "data" / "cache" / "coingecko_history"
    http = CachedHttpClient(
        cache_dir=cache_dir,
        request_timeout_seconds=30,
        min_seconds_between_requests=float(args.min_seconds),
        max_retries=8,
        backoff_base_seconds=3,
        backoff_jitter_seconds=1.5,
    )

    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        # CoinGecko Demo keys authenticate via this header on the public base URL.
        http.session.headers.update({"x-cg-demo-api-key": api_key})
    has_key = bool(api_key)
    print(f"[build] CoinGecko Demo key detected: {has_key} "
          f"({'fast path' if has_key else 'no key — slow/throttled, consider setting COINGECKO_API_KEY'})")
    print(f"[build] Fetching current top {args.top} coins...")
    coins = fetch_top_coins(http, args.top, args.vs, args.force_refresh)
    if not coins:
        print("[build] ERROR: could not fetch top coins (rate-limited?). Set COINGECKO_API_KEY and retry.", file=sys.stderr)
        return 1
    print(f"[build] Got {len(coins)} coins. Fetching {args.days}d daily market caps for each...")

    records: List[Dict[str, Any]] = []
    price_records: List[Dict[str, Any]] = []
    failures: List[str] = []
    for i, coin in enumerate(coins, 1):
        coin_id = coin.get("id")
        symbol = str(coin.get("symbol") or "").upper()
        name = coin.get("name") or symbol
        if not coin_id or not symbol:
            continue
        try:
            payload = fetch_market_chart(http, coin_id, args.vs, args.days, args.force_refresh)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{symbol}: {exc}")
            continue
        mcap_map = _series_to_daily_map(payload.get("market_caps", []))
        price_map = _series_to_daily_map(payload.get("prices", []))
        vol_map = _series_to_daily_map(payload.get("total_volumes", []))
        for day, mcap in mcap_map.items():
            if mcap <= 0:
                continue
            records.append({"date": day, "symbol": symbol, "name": name, "market_cap": mcap})
            close = price_map.get(day)
            if close is not None and close > 0:
                # REAL daily close + volume + market cap (no fabricated OHLC).
                price_records.append(
                    {
                        "date": day,
                        "symbol": symbol,
                        "close": close,
                        "volume": vol_map.get(day, 0.0),
                        "market_cap": mcap,
                    }
                )
        if i % 25 == 0 or i == len(coins):
            print(f"[build]   {i}/{len(coins)} coins | {len(records)} rows so far | failures: {len(failures)}")

    if not records:
        print("[build] ERROR: no historical rows fetched (likely rate-limited). "
              "Set a free COINGECKO_API_KEY and retry.", file=sys.stderr)
        return 1

    df = pd.DataFrame(records)
    # One row per (symbol, date): keep last (closest to that day's close).
    df = df.drop_duplicates(subset=["symbol", "date"], keep="last").sort_values(["date", "market_cap"], ascending=[True, False])

    # Real category enrichment so RWA / non-USD stablecoins / staking tokens are tagged.
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.no_categories:
        df["categories"] = ""
    else:
        id_to_tags = fetch_deny_category_members(http, args.vs, args.force_refresh)
        # Map each universe SYMBOL to tags via OUR coin's unique id (highest-mcap coin per
        # symbol wins, since `coins` is market-cap-desc). This avoids ticker collisions.
        sym_to_tags: Dict[str, List[str]] = {}
        for coin in coins:
            sym = str(coin.get("symbol") or "").upper()
            cid = str(coin.get("id") or "")
            if sym and sym not in sym_to_tags:
                sym_to_tags[sym] = id_to_tags.get(cid, [])
        cats_path = out_path.parent / "coingecko_categories.json"
        with open(cats_path, "w") as fh:
            json.dump({s: t for s, t in sym_to_tags.items() if t}, fh, indent=2, sort_keys=True)
        df["categories"] = df["symbol"].map(lambda s: ";".join(sym_to_tags.get(s, []))).fillna("")
        tagged = sorted({s for s, t in sym_to_tags.items() if t})
        print(f"[build] Category tags written: {cats_path.name} | "
              f"{len(tagged)} of {df['symbol'].nunique()} symbols carry deny-tags")

    df.to_parquet(out_path, index=False)

    # Real daily price/volume dataset (close only — no fabricated O/H/L), for backtesting.
    prices_path = out_path.parent / "coingecko_prices.parquet"
    if price_records:
        pdf = pd.DataFrame(price_records).drop_duplicates(subset=["symbol", "date"], keep="last")
        pdf = pdf.sort_values(["symbol", "date"]).reset_index(drop=True)
        pdf.to_parquet(prices_path, index=False)

    n_symbols = df["symbol"].nunique()
    print(f"[build] DONE. Wrote {len(df)} real rows | {n_symbols} symbols | "
          f"{df['date'].min()} -> {df['date'].max()}")
    print(f"[build] Output (market caps): {out_path}")
    if price_records:
        print(f"[build] Output (prices):      {prices_path} ({len(pdf)} real close/volume rows)")
    if failures:
        print(f"[build] {len(failures)} coins failed (skipped): {failures[:5]}{' ...' if len(failures) > 5 else ''}")
    print("[build] Next: python3 agents/universe_agent_free.py --dataset "
          f"{out_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
