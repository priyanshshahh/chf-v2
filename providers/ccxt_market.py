from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from configs.logging_config import get_logger
from providers.http_client import ProviderUnavailableError, RateLimitError


logger = get_logger("providers.ccxt_market")

NON_RETRY_MARKERS = (
    "401",
    "402",
    "403",
    "404",
    "451",
    "not available",
    "restricted location",
    "name or service not known",
    "temporary failure in name resolution",
    "failed to resolve",
    "nodename nor servname provided",
)
ALIAS_MAP = {
    "kraken": {"BTC": "XBT"},
}


def _sanitize_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace("-", "_")


class CCXTMarketProvider:
    """CCXT-based OHLCV provider for exchange candles with disk caching."""

    def __init__(
        self,
        exchange_name: str,
        cache_dir: Path | str,
        timeframe: str = "1d",
        live_api_enabled: bool = True,
        use_fixtures: bool = False,
        force_refresh: bool = False,
        request_timeout_seconds: float = 30,
        min_seconds_between_requests: float = 1.5,
        max_retries: int = 5,
        backoff_base_seconds: float = 3.0,
        backoff_jitter_seconds: float = 1.5,
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.exchange_name = exchange_name
        self.cache_dir = Path(cache_dir)
        self.timeframe = timeframe
        self.live_api_enabled = live_api_enabled
        self.use_fixtures = use_fixtures
        self.force_refresh = force_refresh
        self.request_timeout_seconds = request_timeout_seconds
        self.min_seconds_between_requests = min_seconds_between_requests
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_jitter_seconds = backoff_jitter_seconds
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self._exchange = None
        self._last_request_at = 0.0
        self.api_call_count_by_provider: Dict[str, int] = defaultdict(int)
        self.cache_hit_count_by_provider: Dict[str, int] = defaultdict(int)

    @property
    def provider_key(self) -> str:
        return f"ccxt_{self.exchange_name}"

    def _cache_path(self, kind: str, key: str) -> Path:
        return self.cache_dir / self.provider_key / f"{kind}_{key}.json"

    def _resolution_cache_path(self, base_symbol: str, preferred_quote: str, allow_usdt: bool) -> Path:
        suffix = "allow_usdt" if allow_usdt else "strict"
        key = f"{base_symbol.upper()}_{preferred_quote.upper()}_{suffix}"
        return self.cache_dir / "symbol_resolution" / self.provider_key / f"{key}.json"

    def _fixture_path(self, kind: str, key: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / f"{self.provider_key}_{kind}_{key}.json"
        return path if path.exists() else None

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_seconds_between_requests - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _get_exchange(self):
        if self._exchange is None:
            import ccxt

            exchange_cls = getattr(ccxt, self.exchange_name)
            self._exchange = exchange_cls(
                {
                    "enableRateLimit": True,
                    "timeout": int(self.request_timeout_seconds * 1000),
                }
            )
        return self._exchange

    def load_markets(self) -> Dict[str, Any]:
        cache_path = self._cache_path("markets", self.exchange_name)
        fixture_path = self._fixture_path("markets", self.exchange_name)
        if cache_path.exists() and not self.force_refresh:
            self.cache_hit_count_by_provider[self.provider_key] += 1
            with open(cache_path, "r") as f:
                return json.load(f)
        if self.use_fixtures and fixture_path is not None:
            with open(fixture_path, "r") as f:
                payload = json.load(f)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            return payload
        if not self.live_api_enabled:
            raise FileNotFoundError(
                f"Missing market cache for {self.exchange_name} and live_api_enabled=false"
            )

        exchange = self._get_exchange()
        markets = None
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._throttle()
                self.api_call_count_by_provider[self.provider_key] += 1
                markets = exchange.load_markets()
                break
            except Exception as exc:
                last_error = exc
                if self._is_non_retry_error(exc):
                    raise ProviderUnavailableError(
                        self.provider_key,
                        f"{self.provider_key} unavailable while loading markets: {exc}",
                    ) from exc
                if self._is_rate_limit_error(exc) and attempt >= self.max_retries:
                    raise RateLimitError(
                        self.provider_key,
                        f"{self.provider_key} rate limited while loading markets",
                    ) from exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(self._backoff_wait(attempt))
        if markets is None:
            raise RuntimeError(f"{self.provider_key} load_markets failed: {last_error}")
        payload = {symbol: {"symbol": symbol} for symbol in markets.keys()}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        return payload

    def has_market(self, exchange_symbol: str) -> bool:
        markets = self.load_markets()
        return exchange_symbol in markets

    def resolve_market_symbol(
        self,
        base_symbol: str,
        preferred_quote: str = "USD",
        allow_usdt: bool = False,
    ) -> Optional[str]:
        cache_path = self._resolution_cache_path(base_symbol, preferred_quote, allow_usdt)
        if cache_path.exists() and not self.force_refresh:
            with open(cache_path, "r") as f:
                payload = json.load(f)
            return payload.get("resolved_symbol")

        markets = self.load_markets()
        base_symbol = base_symbol.upper()
        preferred_quote = preferred_quote.upper()
        candidates = self._resolution_candidates(base_symbol, preferred_quote, allow_usdt)
        resolved = next((candidate for candidate in candidates if candidate in markets), None)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({"resolved_symbol": resolved, "candidates": candidates}, f, indent=2, sort_keys=True)
        return resolved

    def _resolution_candidates(self, base_symbol: str, preferred_quote: str, allow_usdt: bool) -> List[str]:
        aliases = [base_symbol]
        exchange_aliases = ALIAS_MAP.get(self.exchange_name, {})
        if base_symbol in exchange_aliases:
            aliases.insert(0, exchange_aliases[base_symbol])
        quotes = [preferred_quote]
        if preferred_quote == "USD":
            quotes.append("USDC")
        if allow_usdt:
            quotes.append("USDT")
        candidates: List[str] = []
        for alias in aliases:
            for quote in quotes:
                symbol = f"{alias}/{quote}"
                if not allow_usdt and quote == "USDT":
                    continue
                if symbol not in candidates:
                    candidates.append(symbol)
        return candidates

    def fetch_ohlcv(
        self,
        exchange_symbol: str,
        since_dt: datetime,
        until_dt: Optional[datetime] = None,
        limit: int = 1000,
        max_pages: int = 20,
        max_rows: int = 3000,
    ) -> pd.DataFrame:
        key = f"{_sanitize_symbol(exchange_symbol)}_{since_dt.date().isoformat()}_{self.timeframe}"
        cache_path = self._cache_path("ohlcv", key)
        fixture_path = self._fixture_path("ohlcv", key)
        if cache_path.exists() and not self.force_refresh:
            self.cache_hit_count_by_provider[self.provider_key] += 1
            with open(cache_path, "r") as f:
                payload = json.load(f)
            return self._payload_to_df(payload)
        if self.use_fixtures and fixture_path is not None:
            with open(fixture_path, "r") as f:
                payload = json.load(f)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(payload, f, indent=2)
            return self._payload_to_df(payload)
        if not self.live_api_enabled:
            raise FileNotFoundError(
                f"Missing OHLCV cache for {self.exchange_name} {exchange_symbol} and live_api_enabled=false"
            )

        exchange = self._get_exchange()
        since_ms = int(since_dt.timestamp() * 1000)
        until_ms = int(until_dt.timestamp() * 1000) if until_dt else None
        all_rows: List[List[float]] = []
        pages = 0
        last_ts: Optional[int] = None
        while pages < max_pages and len(all_rows) < max_rows:
            payload: List[List[float]] = []
            for attempt in range(1, self.max_retries + 1):
                try:
                    self._throttle()
                    self.api_call_count_by_provider[self.provider_key] += 1
                    payload = exchange.fetch_ohlcv(
                        exchange_symbol,
                        timeframe=self.timeframe,
                        since=since_ms,
                        limit=limit,
                    )
                    break
                except Exception as exc:
                    if self._is_non_retry_error(exc):
                        raise ProviderUnavailableError(
                            self.provider_key,
                            f"{self.provider_key} unavailable for {exchange_symbol}: {exc}",
                        ) from exc
                    if self._is_rate_limit_error(exc) and attempt >= self.max_retries:
                        raise RateLimitError(
                            self.provider_key,
                            f"{self.provider_key} rate limited for {exchange_symbol}",
                        ) from exc
                    if attempt >= self.max_retries:
                        raise
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s OHLCV fetch failed for %s: %s; sleeping %.1fs",
                        self.exchange_name,
                        exchange_symbol,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
            if not payload:
                break
            pages += 1
            if until_ms is not None:
                payload = [row for row in payload if int(row[0]) < until_ms]
                if not payload:
                    break
            all_rows.extend(payload)
            if len(payload) < limit:
                break
            next_since = int(payload[-1][0]) + 1
            if last_ts is not None and next_since <= last_ts:
                break
            last_ts = next_since
            since_ms = next_since
            if until_ms is not None and since_ms >= until_ms:
                break
        if len(all_rows) > max_rows:
            all_rows = all_rows[:max_rows]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(all_rows, f, indent=2)
        return self._payload_to_df(all_rows)

    def _payload_to_df(self, payload: List[List[float]]) -> pd.DataFrame:
        if not payload:
            return pd.DataFrame(columns=["date_ts", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(
            payload,
            columns=["timestamp_ms", "open", "high", "low", "close", "volume"],
        )
        df["date_ts"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        return df.drop(columns=["timestamp_ms"]).sort_values("date_ts").reset_index(drop=True)

    def _backoff_wait(self, attempt: int) -> float:
        return self.backoff_base_seconds * (2 ** (attempt - 1)) + random.uniform(0, self.backoff_jitter_seconds)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "429" in text or "rate limit" in text or "too many requests" in text

    @staticmethod
    def _is_non_retry_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in NON_RETRY_MARKERS)
