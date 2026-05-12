from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


BASE_URL = "https://api.coinpaprika.com/v1"


class CoinPaprikaProvider:
    provider_name = "coinpaprika"

    def __init__(self, http_client: Optional[CachedHttpClient] = None, cache_dir: Optional[Path | str] = None) -> None:
        self.http = http_client or CachedHttpClient(cache_dir or Path("data/cache"))

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
            with open(fixture_path, "r") as f:
                payload = json.load(f)
        else:
            params = {"quotes": vs_currency.upper()}
            payload = self.http.get_json(
                self.provider_name,
                f"{BASE_URL}/tickers",
                params,
                f"tickers_{snapshot_date.strftime('%Y%m%d')}_{params_hash(params)}",
                force_refresh=force_refresh,
                live_api_enabled=live_api_enabled,
            )
        if not isinstance(payload, list):
            raise ValueError("CoinPaprika /tickers returned non-list payload")
        return self.normalize_tickers(payload[:candidate_n], snapshot_date, vs_currency)

    def normalize_tickers(
        self, rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp, vs_currency: str
    ) -> pd.DataFrame:
        now = datetime.now(timezone.utc).isoformat()
        quote_key = vs_currency.upper()
        records: List[Dict[str, Any]] = []
        for row in rows:
            quote = (row.get("quotes") or {}).get(quote_key, {})
            records.append(
                {
                    "snapshot_date": snapshot_date,
                    "provider": self.provider_name,
                    "provider_asset_id": row.get("id") or "",
                    "coin_id": row.get("id") or "",
                    "symbol": str(row.get("symbol") or "").upper(),
                    "name": row.get("name") or "",
                    "market_cap_rank": int(row.get("rank") or 999999),
                    "market_cap_usd": float(quote.get("market_cap") or 0.0),
                    "volume_24h_usd": float(quote.get("volume_24h") or 0.0),
                    "price_usd": float(quote.get("price") or 0.0),
                    "source_timestamp_utc": row.get("last_updated") or now,
                    "first_seen_utc": row.get("started_at") or row.get("first_data_at") or "",
                    "raw_category_tags": [],
                    "source": self.provider_name,
                }
            )
        return pd.DataFrame(records)

    def fetch_coin_registry(
        self,
        force_refresh: bool,
        live_api_enabled: bool,
        fixture_path: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        if fixture_path is not None and fixture_path.exists():
            with open(fixture_path, "r") as f:
                payload = json.load(f)
        else:
            payload = self.http.get_json(
                self.provider_name,
                f"{BASE_URL}/coins",
                {},
                "coins_registry",
                force_refresh=force_refresh,
                live_api_enabled=live_api_enabled,
            )
        if not isinstance(payload, list):
            raise ValueError("CoinPaprika /coins returned non-list payload")
        return payload
