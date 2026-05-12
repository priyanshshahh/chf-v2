from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
from configs.logging_config import get_logger

from providers.http_client import CachedHttpClient, params_hash


class CoinMarketCapProviderError(RuntimeError):
    pass


logger = get_logger("providers.coinmarketcap")


def _to_utc_timestamp(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


class CoinMarketCapProvider:
    BASE_URL = "https://pro-api.coinmarketcap.com"
    PROVIDER_KEY = "coinmarketcap"

    def __init__(
        self,
        cache_dir,
        api_key=None,
        request_timeout_seconds=30,
        min_seconds_between_requests=1.5,
        max_retries=5,
        backoff_base_seconds=3,
        backoff_jitter_seconds=1.5,
        live_api_enabled=True,
        force_refresh=False,
    ):
        self.cache_dir = Path(cache_dir)
        self.api_key = api_key or os.environ.get("CMC_API_KEY")
        self.live_api_enabled = bool(live_api_enabled)
        self.force_refresh = bool(force_refresh)
        self.http = CachedHttpClient(
            cache_dir=self.cache_dir,
            request_timeout_seconds=float(request_timeout_seconds),
            min_seconds_between_requests=float(min_seconds_between_requests),
            max_retries=int(max_retries),
            backoff_base_seconds=float(backoff_base_seconds),
            backoff_jitter_seconds=float(backoff_jitter_seconds),
        )

    @property
    def api_call_count_by_provider(self) -> Dict[str, int]:
        return {k: int(v) for k, v in self.http.api_call_count_by_provider.items()}

    @property
    def cache_hit_count_by_provider(self) -> Dict[str, int]:
        return {k: int(v) for k, v in self.http.cache_hit_count_by_provider.items()}

    def _require_api_key(self) -> None:
        if self.live_api_enabled and not self.api_key:
            raise CoinMarketCapProviderError(
                "CMC_API_KEY is missing. Set it in your shell or .env before running live CMC mode."
            )

    def _headers(self) -> Dict[str, str]:
        self._require_api_key()
        return {"X-CMC_PRO_API_KEY": str(self.api_key)}

    @staticmethod
    def _load_fixture(fixture_path: Optional[Path | str]) -> Optional[Any]:
        if not fixture_path:
            return None
        path = Path(fixture_path)
        if not path.exists():
            return None
        with open(path, "r") as fh:
            return json.load(fh)

    def _get_json(
        self,
        endpoint: str,
        params: Dict[str, Any],
        *,
        fixture_path: Optional[Path | str] = None,
        live_api_enabled: Optional[bool] = None,
        force_refresh: Optional[bool] = None,
    ) -> Any:
        fixture_payload = self._load_fixture(fixture_path)
        if fixture_payload is not None:
            return fixture_payload
        live_enabled = self.live_api_enabled if live_api_enabled is None else bool(live_api_enabled)
        key = f"{endpoint.strip('/').replace('/', '_')}_{params_hash(params)}"
        return self.http.get_json(
            self.PROVIDER_KEY,
            f"{self.BASE_URL}{endpoint}",
            params,
            key,
            force_refresh=self.force_refresh if force_refresh is None else bool(force_refresh),
            live_api_enabled=live_enabled,
            headers=self._headers() if live_enabled else None,
        )

    def _log_request(self, endpoint: str, params: Dict[str, Any]) -> None:
        logger.info(
            "coinmarketcap request | endpoint=%s params=%s",
            endpoint,
            json.dumps(params, sort_keys=True, default=str),
        )

    def fetch_historical_listings(
        self,
        snapshot_date,
        start=1,
        limit=300,
        convert="USD",
        fixture_path: Optional[Path | str] = None,
        live_api_enabled: Optional[bool] = None,
        force_refresh: Optional[bool] = None,
    ) -> pd.DataFrame:
        snapshot_ts = _to_utc_timestamp(snapshot_date)
        params = {
            "date": snapshot_ts.strftime("%Y-%m-%d"),
            "start": int(start),
            "limit": int(limit),
            "convert": convert,
            "sort": "cmc_rank",
            "sort_dir": "desc",
            "cryptocurrency_type": "all",
            "aux": "platform,tags,date_added,circulating_supply,total_supply,max_supply,cmc_rank,num_market_pairs",
        }
        self._log_request("/v1/cryptocurrency/listings/historical", params)
        payload = self._get_json(
            "/v1/cryptocurrency/listings/historical",
            params,
            fixture_path=fixture_path,
            live_api_enabled=live_api_enabled,
            force_refresh=force_refresh,
        )
        rows = []
        for item in payload.get("data", []) or []:
            quote = ((item.get("quote") or {}).get(str(convert).upper()) or {})
            rows.append(
                {
                    "cmc_id": item.get("id"),
                    "provider_asset_id": str(item.get("id") or ""),
                    "coin_id": str(item.get("slug") or ""),
                    "symbol": str(item.get("symbol") or "").upper(),
                    "name": item.get("name"),
                    "slug": item.get("slug"),
                    "market_cap_rank": item.get("cmc_rank"),
                    "market_cap_usd": quote.get("market_cap"),
                    "volume_24h_usd": quote.get("volume_24h"),
                    "price_usd": quote.get("price"),
                    "is_active_at_snapshot": bool(item.get("is_active", 1)),
                    "raw_category_tags": item.get("tags") or [],
                    "source": self.PROVIDER_KEY,
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        for col in ["cmc_id", "market_cap_rank"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ["market_cap_usd", "volume_24h_usd", "price_usd"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def fetch_ohlcv_historical(
        self,
        cmc_id,
        symbol,
        time_start,
        time_end,
        interval="daily",
        convert="USD",
        fixture_path: Optional[Path | str] = None,
        live_api_enabled: Optional[bool] = None,
        force_refresh: Optional[bool] = None,
    ) -> pd.DataFrame:
        start_ts = _to_utc_timestamp(time_start)
        end_ts = _to_utc_timestamp(time_end)
        params = {
            "id": int(cmc_id),
            "time_start": start_ts.isoformat(),
            "time_end": end_ts.isoformat(),
            "interval": interval,
            "convert": convert,
        }
        self._log_request("/v2/cryptocurrency/ohlcv/historical", params)
        payload = self._get_json(
            "/v2/cryptocurrency/ohlcv/historical",
            params,
            fixture_path=fixture_path,
            live_api_enabled=live_api_enabled,
            force_refresh=force_refresh,
        )
        data = payload.get("data") or {}
        quotes = []
        if isinstance(data, dict):
            if "quotes" in data:
                quotes = data.get("quotes") or []
            else:
                asset_block = data.get(str(cmc_id)) or data.get(int(cmc_id)) or {}
                quotes = asset_block.get("quotes") or []
        rows = []
        for item in quotes:
            quote = ((item.get("quote") or {}).get(str(convert).upper()) or {})
            rows.append(
                {
                    "date_ts": _to_utc_timestamp(item.get("time_open") or item.get("time_close") or item.get("timestamp")).normalize(),
                    "cmc_id": int(cmc_id),
                    "symbol": str(symbol).upper(),
                    "open": quote.get("open"),
                    "high": quote.get("high"),
                    "low": quote.get("low"),
                    "close": quote.get("close"),
                    "volume": quote.get("volume"),
                    "market_cap": quote.get("market_cap"),
                    "source": self.PROVIDER_KEY,
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        for col in ["open", "high", "low", "close", "volume", "market_cap"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.sort_values("date_ts").drop_duplicates(subset=["date_ts"], keep="last").reset_index(drop=True)

    def fetch_map(
        self,
        symbols: Optional[Iterable[str]] = None,
        fixture_path: Optional[Path | str] = None,
        live_api_enabled: Optional[bool] = None,
        force_refresh: Optional[bool] = None,
    ) -> pd.DataFrame:
        params: Dict[str, Any] = {}
        if symbols:
            params["symbol"] = ",".join(sorted({str(sym).upper() for sym in symbols if str(sym).strip()}))
        self._log_request("/v1/cryptocurrency/map", params)
        payload = self._get_json(
            "/v1/cryptocurrency/map",
            params,
            fixture_path=fixture_path,
            live_api_enabled=live_api_enabled,
            force_refresh=force_refresh,
        )
        rows = []
        for item in payload.get("data", []) or []:
            rows.append(
                {
                    "cmc_id": item.get("id"),
                    "symbol": str(item.get("symbol") or "").upper(),
                    "name": item.get("name"),
                    "slug": item.get("slug"),
                    "is_active": item.get("is_active"),
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty and "cmc_id" in df.columns:
            df["cmc_id"] = pd.to_numeric(df["cmc_id"], errors="coerce").astype("Int64")
        return df
