from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoProvider:
    """CoinGecko bulk market-data provider for universe construction."""

    provider_name = "coingecko"

    def __init__(
        self,
        http_client: Optional[CachedHttpClient] = None,
        cache_dir: Optional[Path | str] = None,
        api_key: Optional[str] = None,
        **legacy_kwargs: Any,
    ) -> None:
        self.http = http_client or CachedHttpClient(cache_dir or Path("data/cache"))
        api_key = api_key or os.getenv("COINGECKO_API_KEY")
        if api_key:
            self.http.session.headers.update({"x-cg-demo-api-key": api_key})

    def fetch_candidates(
        self,
        candidate_n: int,
        snapshot_date: pd.Timestamp,
        vs_currency: str,
        force_refresh: bool,
        live_api_enabled: bool,
        fixture_path: Optional[Path] = None,
    ) -> pd.DataFrame:
        if fixture_path is not None and fixture_path.exists():
            import json

            with open(fixture_path, "r") as f:
                payload = json.load(f)
            return self.normalize_markets(payload, snapshot_date)

        rows: List[Dict[str, Any]] = []
        per_page = 250
        pages = (candidate_n // per_page) + (1 if candidate_n % per_page else 0)
        for page in range(1, pages + 1):
            params = {
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": min(per_page, candidate_n - len(rows)),
                "page": page,
                "sparkline": "false",
            }
            cache_key = (
                f"markets_{snapshot_date.strftime('%Y%m%d')}_page={page}_"
                f"{params_hash(params)}"
            )
            payload = self.http.get_json(
                provider=self.provider_name,
                url=f"{BASE_URL}/coins/markets",
                params=params,
                cache_key=cache_key,
                force_refresh=force_refresh,
                live_api_enabled=live_api_enabled,
            )
            if not isinstance(payload, list):
                raise ValueError("CoinGecko /coins/markets returned non-list payload")
            rows.extend(payload)
            if len(rows) >= candidate_n:
                break
        return self.normalize_markets(rows[:candidate_n], snapshot_date)

    def normalize_markets(self, rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp) -> pd.DataFrame:
        now = datetime.now(timezone.utc).isoformat()
        records: List[Dict[str, Any]] = []
        for row in rows:
            records.append(
                {
                    "snapshot_date": snapshot_date,
                    "provider": self.provider_name,
                    "provider_asset_id": row.get("id") or "",
                    "coin_id": row.get("id") or "",
                    "symbol": str(row.get("symbol") or "").upper(),
                    "name": row.get("name") or "",
                    "market_cap_rank": int(row.get("market_cap_rank") or 999999),
                    "market_cap_usd": float(row.get("market_cap") or 0.0),
                    "volume_24h_usd": float(row.get("total_volume") or 0.0),
                    "price_usd": float(row.get("current_price") or 0.0),
                    "source_timestamp_utc": row.get("last_updated") or now,
                    "first_seen_utc": row.get("atl_date") or row.get("ath_date") or "",
                    "raw_category_tags": row.get("categories") or [],
                    "source": self.provider_name,
                }
            )
        return pd.DataFrame(records)

    # Backward-compatible helper for older callers/tests.
    def get_top_coins_by_market_cap(self, top_n: int = 250, vs_currency: str = "usd") -> List[Dict[str, Any]]:
        df = self.fetch_candidates(
            candidate_n=top_n,
            snapshot_date=pd.Timestamp.now(tz="UTC"),
            vs_currency=vs_currency,
            force_refresh=False,
            live_api_enabled=True,
        )
        return df.to_dict(orient="records")
