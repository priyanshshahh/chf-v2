from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


BASE_URL = "https://min-api.cryptocompare.com/data"


class CryptoCompareProvider:
    provider_name = "cryptocompare"

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
            params = {"limit": candidate_n, "tsym": vs_currency.upper()}
            payload = self.http.get_json(
                self.provider_name,
                f"{BASE_URL}/top/mktcapfull",
                params,
                f"top_mktcapfull_{snapshot_date.strftime('%Y%m%d')}_{params_hash(params)}",
                force_refresh=force_refresh,
                live_api_enabled=live_api_enabled,
            )
        rows = payload.get("Data", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("CryptoCompare top list returned invalid payload")
        return self.normalize_toplist(rows[:candidate_n], snapshot_date, vs_currency)

    def normalize_toplist(
        self, rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp, vs_currency: str
    ) -> pd.DataFrame:
        now = datetime.now(timezone.utc).isoformat()
        quote_key = vs_currency.upper()
        records: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            coin = row.get("CoinInfo", row)
            raw = (row.get("RAW") or {}).get(quote_key, {})
            records.append(
                {
                    "snapshot_date": snapshot_date,
                    "provider": self.provider_name,
                    "provider_asset_id": str(coin.get("Id") or coin.get("Name") or ""),
                    "coin_id": str(coin.get("Name") or "").lower(),
                    "symbol": str(coin.get("Name") or "").upper(),
                    "name": coin.get("FullName") or coin.get("Name") or "",
                    "market_cap_rank": int(idx),
                    "market_cap_usd": float(raw.get("MKTCAP") or 0.0),
                    "volume_24h_usd": float(raw.get("VOLUME24HOURTO") or 0.0),
                    "price_usd": float(raw.get("PRICE") or 0.0),
                    "source_timestamp_utc": now,
                    "first_seen_utc": "",
                    "raw_category_tags": [],
                    "source": self.provider_name,
                }
            )
        return pd.DataFrame(records)
