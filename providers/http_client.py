from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from configs.logging_config import get_logger


logger = get_logger("providers.http_client")


RETRY_STATUSES = {429, 500, 502, 503, 504}
NO_RETRY_STATUSES = {400, 401, 402, 403, 404, 451}


class RateLimitError(RuntimeError):
    """Raised when a provider exhausts retry budget due to rate limiting."""

    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider should be skipped for the rest of the current run."""

    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider


def params_hash(params: Optional[Dict[str, Any]]) -> str:
    """Return a deterministic short hash for request params."""
    payload = json.dumps(params or {}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def safe_cache_key(raw_key: str) -> str:
    """Sanitize a cache key for filesystem use."""
    key = re.sub(r"[^A-Za-z0-9_.=-]+", "_", raw_key).strip("_")
    return key[:220] or "request"


def _response_error_message(provider: str, resp: requests.Response) -> str:
    message = f"{provider} returned non-retryable HTTP {resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        return message
    status = payload.get("status") if isinstance(payload, dict) else None
    if isinstance(status, dict):
        error_code = status.get("error_code")
        error_message = status.get("error_message")
        if error_code is not None or error_message:
            message = (
                f"{provider} returned non-retryable HTTP {resp.status_code} "
                f"(cmc_error_code={error_code}, cmc_error_message={error_message})"
            )
    return message


class CachedHttpClient:
    """
    Cache-first JSON HTTP client with per-provider throttling.

    Cache is always checked before live HTTP. Live responses are written to disk
    before being returned to callers.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        request_timeout_seconds: float = 30,
        min_seconds_between_requests: float = 2.0,
        max_retries: int = 5,
        backoff_base_seconds: float = 3.0,
        backoff_jitter_seconds: float = 1.5,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.request_timeout_seconds = request_timeout_seconds
        self.min_seconds_between_requests = min_seconds_between_requests
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_jitter_seconds = backoff_jitter_seconds
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.api_call_count_by_provider: Dict[str, int] = defaultdict(int)
        self.failed_api_call_count_by_provider: Dict[str, int] = defaultdict(int)
        self.cache_hit_count_by_provider: Dict[str, int] = defaultdict(int)
        self.cache_hit_count = 0
        self._last_request_at: Dict[str, float] = defaultdict(float)

    def cache_path(self, provider: str, cache_key: str) -> Path:
        return self.cache_dir / provider / f"{safe_cache_key(cache_key)}.json"

    def _throttle(self, provider: str) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at[provider]
        wait = self.min_seconds_between_requests - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at[provider] = time.monotonic()

    def get_json(
        self,
        provider: str,
        url: str,
        params: Optional[dict],
        cache_key: str,
        force_refresh: bool = False,
        live_api_enabled: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        path = self.cache_path(provider, cache_key)
        if path.exists() and not force_refresh:
            self.cache_hit_count += 1
            self.cache_hit_count_by_provider[provider] += 1
            with open(path, "r") as f:
                return json.load(f)

        if not live_api_enabled:
            raise FileNotFoundError(
                f"Cache miss for {provider}/{cache_key} and live_api_enabled=false"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle(provider)
            self.api_call_count_by_provider[provider] += 1
            try:
                resp = self.session.get(
                    url,
                    params=params or {},
                    headers=headers,
                    timeout=self.request_timeout_seconds,
                )
                if resp.status_code in NO_RETRY_STATUSES:
                    self.failed_api_call_count_by_provider[provider] += 1
                    error_message = _response_error_message(provider, resp)
                    logger.warning(
                        "%s request failed | status=%s params=%s message=%s",
                        provider,
                        resp.status_code,
                        json.dumps(params or {}, sort_keys=True, default=str),
                        error_message,
                    )
                    raise ProviderUnavailableError(
                        provider,
                        error_message,
                    )
                if resp.status_code in RETRY_STATUSES:
                    retry_after = resp.headers.get("Retry-After")
                    if attempt >= self.max_retries:
                        self.failed_api_call_count_by_provider[provider] += 1
                        if resp.status_code == 429:
                            raise RateLimitError(
                                provider,
                                f"{provider} rate limited after {self.max_retries} attempts",
                            )
                        resp.raise_for_status()
                    if retry_after:
                        wait = float(retry_after)
                    else:
                        wait = (
                            self.backoff_base_seconds * (2 ** (attempt - 1))
                            + random.uniform(0, self.backoff_jitter_seconds)
                        )
                    logger.warning(
                        "Retryable HTTP %s from %s; sleeping %.1fs",
                        resp.status_code,
                        provider,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                payload = resp.json()
                with open(path, "w") as f:
                    json.dump(payload, f, indent=2, sort_keys=True)
                return payload
            except requests.RequestException as exc:
                last_error = exc
                if self._is_dns_resolution_error(exc):
                    self.failed_api_call_count_by_provider[provider] += 1
                    raise ProviderUnavailableError(
                        provider,
                        f"{provider} DNS resolution failed",
                    ) from exc
                if attempt >= self.max_retries:
                    self.failed_api_call_count_by_provider[provider] += 1
                    break
                wait = (
                    self.backoff_base_seconds * (2 ** (attempt - 1))
                    + random.uniform(0, self.backoff_jitter_seconds)
                )
                logger.warning("%s request failed: %s; sleeping %.1fs", provider, exc, wait)
                time.sleep(wait)
        if last_error is not None and "429" in str(last_error):
            raise RateLimitError(provider, f"{provider} rate limited after {self.max_retries} attempts")
        raise RuntimeError(f"{provider} request failed after {self.max_retries} attempts: {last_error}")

    def post_json(
        self,
        provider: str,
        url: str,
        json_body: Optional[dict],
        cache_key: str,
        force_refresh: bool = False,
        live_api_enabled: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        path = self.cache_path(provider, cache_key)
        if path.exists() and not force_refresh:
            self.cache_hit_count += 1
            self.cache_hit_count_by_provider[provider] += 1
            with open(path, "r") as f:
                return json.load(f)

        if not live_api_enabled:
            raise FileNotFoundError(
                f"Cache miss for {provider}/{cache_key} and live_api_enabled=false"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Optional[Exception] = None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        for attempt in range(1, self.max_retries + 1):
            self._throttle(provider)
            self.api_call_count_by_provider[provider] += 1
            try:
                resp = self.session.post(
                    url,
                    json=json_body or {},
                    headers=request_headers,
                    timeout=self.request_timeout_seconds,
                )
                if resp.status_code in NO_RETRY_STATUSES:
                    self.failed_api_call_count_by_provider[provider] += 1
                    error_message = _response_error_message(provider, resp)
                    raise ProviderUnavailableError(
                        provider,
                        error_message,
                    )
                if resp.status_code in RETRY_STATUSES:
                    retry_after = resp.headers.get("Retry-After")
                    if attempt >= self.max_retries:
                        self.failed_api_call_count_by_provider[provider] += 1
                        if resp.status_code == 429:
                            raise RateLimitError(
                                provider,
                                f"{provider} rate limited after {self.max_retries} attempts",
                            )
                        resp.raise_for_status()
                    if retry_after:
                        wait = float(retry_after)
                    else:
                        wait = (
                            self.backoff_base_seconds * (2 ** (attempt - 1))
                            + random.uniform(0, self.backoff_jitter_seconds)
                        )
                    logger.warning(
                        "Retryable HTTP %s from %s; sleeping %.1fs",
                        resp.status_code,
                        provider,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                with open(path, "w") as f:
                    json.dump(payload, f, indent=2, sort_keys=True)
                return payload
            except requests.RequestException as exc:
                last_error = exc
                if self._is_dns_resolution_error(exc):
                    self.failed_api_call_count_by_provider[provider] += 1
                    raise ProviderUnavailableError(
                        provider,
                        f"{provider} DNS resolution failed",
                    ) from exc
                if attempt >= self.max_retries:
                    self.failed_api_call_count_by_provider[provider] += 1
                    break
                wait = (
                    self.backoff_base_seconds * (2 ** (attempt - 1))
                    + random.uniform(0, self.backoff_jitter_seconds)
                )
                logger.warning("%s request failed: %s; sleeping %.1fs", provider, exc, wait)
                time.sleep(wait)
        if last_error is not None and "429" in str(last_error):
            raise RateLimitError(provider, f"{provider} rate limited after {self.max_retries} attempts")
        raise RuntimeError(f"{provider} request failed after {self.max_retries} attempts: {last_error}")

    @staticmethod
    def _is_dns_resolution_error(exc: requests.RequestException) -> bool:
        text = str(exc).lower()
        return (
            "name or service not known" in text
            or "temporary failure in name resolution" in text
            or "failed to resolve" in text
            or "nodename nor servname provided" in text
        )
