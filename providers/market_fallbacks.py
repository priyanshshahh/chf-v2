from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, ProviderUnavailableError, RateLimitError, params_hash


@dataclass
class FallbackFetchResult:
    df: pd.DataFrame
    provider_name: str
    data_type: str
    is_full_ohlcv: bool
    attempts: List[str]
    failure_reasons: Dict[str, str]


class MarketFallbackProvider:
    """Fallback market providers used after exchange OHLCV sources fail."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.unavailable_providers: Dict[str, str] = {}

    def fetch_daily_data(
        self,
        symbol: str,
        coin_id: str,
        provider_priority: List[str],
        requested_start_dt: datetime,
        requested_end_dt: datetime,
        force_refresh: bool,
        live_api_enabled: bool,
        use_fixtures: bool,
    ) -> FallbackFetchResult:
        attempts: List[str] = []
        failure_reasons: Dict[str, str] = {}
        for provider_name in provider_priority:
            if provider_name == "coinpaprika_metadata":
                continue
            if provider_name in self.unavailable_providers:
                failure_reasons[provider_name] = self.unavailable_providers[provider_name]
                continue
            method = getattr(self, f"_fetch_{provider_name}", None)
            if method is None:
                continue
            attempts.append(provider_name)
            try:
                result = method(
                    symbol=symbol,
                    coin_id=coin_id,
                    requested_start_dt=requested_start_dt,
                    requested_end_dt=requested_end_dt,
                    force_refresh=force_refresh,
                    live_api_enabled=live_api_enabled,
                    use_fixtures=use_fixtures,
                )
            except (RateLimitError, ProviderUnavailableError) as exc:
                reason = str(exc)
                self.unavailable_providers[provider_name] = reason
                failure_reasons[provider_name] = reason
                continue
            except Exception as exc:
                failure_reasons[provider_name] = str(exc)
                continue
            if not result.df.empty:
                result.attempts = attempts
                result.failure_reasons = failure_reasons
                return result
            failure_reasons[provider_name] = "empty_response"
        return FallbackFetchResult(
            df=pd.DataFrame(),
            provider_name="",
            data_type="",
            is_full_ohlcv=False,
            attempts=attempts,
            failure_reasons=failure_reasons,
        )

    def _fixture_path(self, provider_name: str, symbol: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / f"{provider_name}_{symbol.upper()}_ohlcv.json"
        return path if path.exists() else None

    def _load_fixture_rows(self, provider_name: str, symbol: str) -> List[Dict[str, Any]]:
        fixture_path = self._fixture_path(provider_name, symbol)
        if fixture_path is None:
            return []
        with open(fixture_path, "r") as f:
            return json.load(f)

    def _rows_to_df(self, rows: List[Dict[str, Any]], is_full_ohlcv: bool) -> pd.DataFrame:
        columns = ["date_ts", "open", "high", "low", "close", "volume"]
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows)
        df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True)
        if "close" in df.columns:
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        else:
            df["volume"] = pd.NA
        for col in ["open", "high", "low"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = pd.NA
        if not is_full_ohlcv:
            df["open"] = pd.NA
            df["high"] = pd.NA
            df["low"] = pd.NA
        return df[columns].sort_values("date_ts").reset_index(drop=True)

    def _fetch_cryptocompare(self, **kwargs: Any) -> FallbackFetchResult:
        symbol = kwargs["symbol"]
        if kwargs["use_fixtures"]:
            rows = self._load_fixture_rows("cryptocompare", symbol)
            return FallbackFetchResult(
                df=self._rows_to_df(rows, is_full_ohlcv=True),
                provider_name="cryptocompare",
                data_type="aggregate_ohlcv",
                is_full_ohlcv=True,
                attempts=[],
                failure_reasons={},
            )
        requested_start_dt = kwargs["requested_start_dt"]
        requested_end_dt = kwargs["requested_end_dt"]
        params = {
            "fsym": symbol.upper(),
            "tsym": "USD",
            "limit": max((requested_end_dt.date() - requested_start_dt.date()).days, 1),
            "toTs": int(requested_end_dt.timestamp()),
        }
        payload = self.http.get_json(
            "cryptocompare_market",
            "https://min-api.cryptocompare.com/data/v2/histoday",
            params,
            f"{symbol.upper()}_{params_hash(params)}",
            force_refresh=kwargs["force_refresh"],
            live_api_enabled=kwargs["live_api_enabled"],
        )
        rows = (payload.get("Data") or {}).get("Data", [])
        parsed = [
            {
                "date_ts": pd.Timestamp(row["time"], unit="s", tz="UTC"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volumeto", 0),
            }
            for row in rows
        ]
        return FallbackFetchResult(
            df=self._rows_to_df(parsed, is_full_ohlcv=True),
            provider_name="cryptocompare",
            data_type="aggregate_ohlcv",
            is_full_ohlcv=True,
            attempts=[],
            failure_reasons={},
        )

    def _fetch_coingecko(self, **kwargs: Any) -> FallbackFetchResult:
        symbol = kwargs["symbol"]
        coin_id = kwargs["coin_id"]
        if kwargs["use_fixtures"]:
            rows = self._load_fixture_rows("coingecko", symbol)
            return FallbackFetchResult(
                df=self._rows_to_df(rows, is_full_ohlcv=False),
                provider_name="coingecko",
                data_type="close_volume_only",
                is_full_ohlcv=False,
                attempts=[],
                failure_reasons={},
            )
        params = {"vs_currency": "usd", "days": "max"}
        payload = self.http.get_json(
            "coingecko_market",
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params,
            f"{coin_id}_{params_hash(params)}",
            force_refresh=kwargs["force_refresh"],
            live_api_enabled=kwargs["live_api_enabled"],
        )
        price_rows = payload.get("prices", [])
        vol_rows = {int(ts): vol for ts, vol in payload.get("total_volumes", [])}
        parsed = []
        for ts, close in price_rows:
            ts_int = int(ts)
            dt = pd.Timestamp(ts_int, unit="ms", tz="UTC").normalize()
            parsed.append(
                {
                    "date_ts": dt,
                    "close": close,
                    "volume": vol_rows.get(ts_int),
                }
            )
        return FallbackFetchResult(
            df=self._rows_to_df(parsed, is_full_ohlcv=False),
            provider_name="coingecko",
            data_type="close_volume_only",
            is_full_ohlcv=False,
            attempts=[],
            failure_reasons={},
        )

    def _fetch_coincap(self, **kwargs: Any) -> FallbackFetchResult:
        symbol = kwargs["symbol"]
        coin_id = kwargs["coin_id"]
        if kwargs["use_fixtures"]:
            rows = self._load_fixture_rows("coincap", symbol)
            return FallbackFetchResult(
                df=self._rows_to_df(rows, is_full_ohlcv=False),
                provider_name="coincap",
                data_type="close_only",
                is_full_ohlcv=False,
                attempts=[],
                failure_reasons={},
            )
        requested_start_dt = kwargs["requested_start_dt"]
        requested_end_dt = kwargs["requested_end_dt"]
        params = {
            "interval": "d1",
            "start": int(requested_start_dt.timestamp() * 1000),
            "end": int(requested_end_dt.timestamp() * 1000),
        }
        payload = self.http.get_json(
            "coincap_market",
            f"https://api.coincap.io/v2/assets/{coin_id}/history",
            params,
            f"{coin_id}_{params_hash(params)}",
            force_refresh=kwargs["force_refresh"],
            live_api_enabled=kwargs["live_api_enabled"],
        )
        parsed = [
            {
                "date_ts": row.get("date"),
                "close": row.get("priceUsd"),
            }
            for row in payload.get("data", [])
        ]
        return FallbackFetchResult(
            df=self._rows_to_df(parsed, is_full_ohlcv=False),
            provider_name="coincap",
            data_type="close_only",
            is_full_ohlcv=False,
            attempts=[],
            failure_reasons={},
        )

    def _fetch_coinpaprika(self, **kwargs: Any) -> FallbackFetchResult:
        symbol = kwargs["symbol"]
        coin_id = kwargs["coin_id"]
        if kwargs["use_fixtures"]:
            rows = self._load_fixture_rows("coinpaprika", symbol)
            return FallbackFetchResult(
                df=self._rows_to_df(rows, is_full_ohlcv=True),
                provider_name="coinpaprika",
                data_type="aggregate_ohlcv",
                is_full_ohlcv=True,
                attempts=[],
                failure_reasons={},
            )
        requested_start_dt = kwargs["requested_start_dt"]
        requested_end_dt = kwargs["requested_end_dt"]
        params = {
            "start": requested_start_dt.date().isoformat(),
            "end": requested_end_dt.date().isoformat(),
            "quote": "usd",
        }
        payload = self.http.get_json(
            "coinpaprika_market",
            f"https://api.coinpaprika.com/v1/coins/{coin_id}/ohlcv/historical",
            params,
            f"{coin_id}_{params_hash(params)}",
            force_refresh=kwargs["force_refresh"],
            live_api_enabled=kwargs["live_api_enabled"],
        )
        parsed = [
            {
                "date_ts": row.get("time_open") or row.get("time_close"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume", 0),
            }
            for row in payload
        ]
        return FallbackFetchResult(
            df=self._rows_to_df(parsed, is_full_ohlcv=True),
            provider_name="coinpaprika",
            data_type="aggregate_ohlcv",
            is_full_ohlcv=True,
            attempts=[],
            failure_reasons={},
        )
