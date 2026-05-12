from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


BASE_URL = "https://api.coincap.io/v2"


class CoinCapProvider:
    provider_name = "coincap"

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
            params = {"limit": candidate_n}
            payload = self.http.get_json(
                self.provider_name,
                f"{BASE_URL}/assets",
                params,
                f"assets_{snapshot_date.strftime('%Y%m%d')}_{params_hash(params)}",
                force_refresh=force_refresh,
                live_api_enabled=live_api_enabled,
            )
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("CoinCap /assets returned invalid payload")
        return self.normalize_assets(rows[:candidate_n], snapshot_date)

    def normalize_assets(self, rows: List[Dict[str, Any]], snapshot_date: pd.Timestamp) -> pd.DataFrame:
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
                    "market_cap_rank": int(row.get("rank") or 999999),
                    "market_cap_usd": float(row.get("marketCapUsd") or 0.0),
                    "volume_24h_usd": float(row.get("volumeUsd24Hr") or 0.0),
                    "price_usd": float(row.get("priceUsd") or 0.0),
                    "source_timestamp_utc": now,
                    "first_seen_utc": "",
                    "raw_category_tags": [],
                    "source": self.provider_name,
                }
            )
        return pd.DataFrame(records)
