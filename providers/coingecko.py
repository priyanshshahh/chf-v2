"""
CHF CoinGecko Provider
Fetches universe metadata, market caps, categories, and stablecoin/wrapped flags
using the free CoinGecko API (no key required for basic endpoints).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from configs.logging_config import get_logger

logger = get_logger("providers.coingecko")

_BASE_URL = "https://api.coingecko.com/api/v3"
_DEMO_BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoProvider:
    """
    Free-tier CoinGecko API provider.
    Handles rate limiting with exponential backoff.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit_sleep: float = 1.5,
        max_retries: int = 5,
    ):
        self.api_key = api_key or os.getenv("COINGECKO_API_KEY")
        self.rate_limit_sleep = rate_limit_sleep
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if self.api_key:
            self.session.headers.update({"x-cg-demo-api-key": self.api_key})

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """GET request with retry and exponential backoff."""
        url = f"{_BASE_URL}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = self.rate_limit_sleep * (2 ** attempt)
                    logger.warning(f"Rate limited on {endpoint}, sleeping {wait:.1f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                time.sleep(self.rate_limit_sleep)
                return resp.json()
            except requests.exceptions.RequestException as e:
                wait = self.rate_limit_sleep * (2 ** attempt)
                logger.warning(f"Request error on {endpoint} (attempt {attempt}): {e}")
                if attempt < self.max_retries:
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"Max retries exceeded for {endpoint}")

    def get_top_coins_by_market_cap(
        self, top_n: int = 250, vs_currency: str = "usd"
    ) -> List[Dict]:
        """
        Fetch top N coins by market cap.
        Returns list of coin dicts with id, symbol, name, market_cap, total_volume, etc.
        """
        results = []
        per_page = 250
        pages = (top_n // per_page) + (1 if top_n % per_page else 0)

        for page in range(1, pages + 1):
            data = self._get(
                "/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "order": "market_cap_desc",
                    "per_page": min(per_page, top_n - len(results)),
                    "page": page,
                    "sparkline": False,
                    "price_change_percentage": "24h",
                },
            )
            results.extend(data)
            if len(results) >= top_n:
                break

        logger.info(f"Fetched {len(results)} coins from CoinGecko markets endpoint")
        return results[:top_n]

    def get_coin_detail(self, coin_id: str) -> Dict:
        """Fetch detailed metadata for a single coin."""
        return self._get(
            f"/coins/{coin_id}",
            params={
                "localization": False,
                "tickers": False,
                "market_data": False,
                "community_data": False,
                "developer_data": False,
            },
        )

    def get_coins_list(self) -> List[Dict]:
        """Fetch the full list of coins with id, symbol, name."""
        return self._get("/coins/list", params={"include_platform": False})

    def get_categories(self) -> List[Dict]:
        """Fetch all coin categories."""
        return self._get("/coins/categories/list")

    def is_stablecoin(self, coin: Dict, stablecoin_keywords: List[str]) -> bool:
        """
        Heuristic stablecoin detection using categories and symbol keywords.
        """
        symbol = coin.get("symbol", "").lower()
        name = coin.get("name", "").lower()
        categories = [c.lower() for c in coin.get("categories", [])]

        # Category-based detection
        stablecoin_categories = {"stablecoins", "usd stablecoin", "algorithmic stablecoin"}
        if any(cat in stablecoin_categories for cat in categories):
            return True

        # Keyword-based detection
        for kw in stablecoin_keywords:
            if kw in symbol or kw in name:
                return True

        return False

    def is_wrapped(self, coin: Dict, wrapped_keywords: List[str]) -> bool:
        """
        Heuristic wrapped/synthetic asset detection.
        """
        symbol = coin.get("symbol", "").lower()
        name = coin.get("name", "").lower()
        categories = [c.lower() for c in coin.get("categories", [])]

        wrapped_categories = {"wrapped-tokens", "bridged tokens", "liquid staking tokens"}
        if any(cat in wrapped_categories for cat in categories):
            return True

        for kw in wrapped_keywords:
            if kw in symbol or kw in name:
                return True

        return False

    def build_universe_snapshot(
        self,
        top_n: int = 100,
        min_volume_usd: float = 1_000_000,
        stablecoin_keywords: Optional[List[str]] = None,
        wrapped_keywords: Optional[List[str]] = None,
        snapshot_id: str = "",
        run_id: str = "",
    ) -> List[Dict]:
        """
        Build a universe snapshot: fetch top N+buffer coins, apply filters,
        return eligible assets with metadata.
        """
        stablecoin_keywords = stablecoin_keywords or []
        wrapped_keywords = wrapped_keywords or []
        retrieved_at = datetime.now(timezone.utc).isoformat()

        # Fetch more than top_n to account for exclusions
        raw_coins = self.get_top_coins_by_market_cap(top_n=min(top_n * 3, 500))

        eligible = []
        excluded = []

        for coin in raw_coins:
            symbol = coin.get("symbol", "").upper()
            coin_id = coin.get("id", "")
            market_cap = coin.get("market_cap") or 0
            volume = coin.get("total_volume") or 0

            # Fetch categories for better classification
            categories = []
            try:
                detail = self.get_coin_detail(coin_id)
                categories = detail.get("categories") or []
            except Exception:
                categories = []

            coin["categories"] = categories

            is_stable = self.is_stablecoin(coin, stablecoin_keywords)
            is_wrap = self.is_wrapped(coin, wrapped_keywords)
            low_volume = volume < min_volume_usd

            record = {
                "symbol": symbol,
                "coingecko_id": coin_id,
                "name": coin.get("name", ""),
                "rank": coin.get("market_cap_rank") or 9999,
                "market_cap_usd": market_cap,
                "volume_24h_usd": volume,
                "is_stablecoin": is_stable,
                "is_wrapped": is_wrap,
                "categories": categories,
                "retrieved_at": retrieved_at,
                "snapshot_id": snapshot_id,
                "run_id": run_id,
                "source": "coingecko",
            }

            if is_stable:
                record["is_excluded"] = True
                record["exclusion_reason"] = "stablecoin"
                excluded.append(record)
            elif is_wrap:
                record["is_excluded"] = True
                record["exclusion_reason"] = "wrapped_or_synthetic"
                excluded.append(record)
            elif low_volume:
                record["is_excluded"] = True
                record["exclusion_reason"] = f"low_volume_usd={volume:.0f}"
                excluded.append(record)
            else:
                record["is_excluded"] = False
                record["exclusion_reason"] = None
                eligible.append(record)

            if len(eligible) >= top_n:
                break

        logger.info(
            f"Universe snapshot: {len(eligible)} eligible, {len(excluded)} excluded"
        )
        return eligible + excluded
