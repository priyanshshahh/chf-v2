#!/usr/bin/env python3
"""client.py — thin CoinMarketCap Pro API HTTP client.

Auth: reads ``CMC_API_KEY`` from the environment (loaded from a repo-root
``.env`` by the tiny loader below — no python-dotenv dependency). The key is
sent in the ``X-CMC_PRO_API_KEY`` header and NEVER logged.

Retries: exponential backoff on HTTP 429 (rate limit) and 5xx (server) errors.
Every response is returned as parsed JSON.

Imported by every module under ``src/cmc/collectors/`` and by
``src/cmc/maintain.py``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

BASE_URL = "https://pro-api.coinmarketcap.com"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Optional[Path] = None) -> None:
    """Populate ``os.environ`` from a ``.env`` file (KEY=VALUE per line).

    Existing environment values win; missing keys are filled in. Comment lines
    (starting with ``#``) and blanks are ignored. Intentionally tiny so the
    pipeline has no python-dotenv dependency.
    """
    env_path = path or (_REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class CMCClientError(RuntimeError):
    """Raised when a request ultimately fails (after retries) or lacks a key."""


class CMCClient:
    """Minimal paced/retrying GET client for the CMC Pro API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        min_interval: float = 0.25,
        timeout: float = 40.0,
    ) -> None:
        if api_key is None:
            load_env()
            api_key = os.environ.get("CMC_API_KEY")
        if not api_key:
            raise CMCClientError(
                "CMC_API_KEY not set. Add it to .env or the environment."
            )
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._min_interval = min_interval
        self._timeout = timeout
        self._last_call = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
        )

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET ``path`` with ``params``; return parsed JSON.

        Backs off exponentially on HTTP 429/5xx. Raises ``CMCClientError`` on a
        non-retryable 4xx (e.g. plan limit) with the CMC error message attached,
        so callers can record it and degrade gracefully.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            self._pace()
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                self._last_call = time.monotonic()
            except requests.RequestException as exc:  # network error → retry
                last_exc = exc
                time.sleep(self._backoff_base ** (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = CMCClientError(f"HTTP {resp.status_code} on {path}")
                time.sleep(self._backoff_base ** (attempt + 1))
                continue
            if resp.status_code >= 400:
                # Non-retryable (bad request / plan limit). Surface the message.
                try:
                    status = resp.json().get("status", {})
                    msg = status.get("error_message") or resp.text
                except ValueError:
                    msg = resp.text
                raise CMCClientError(f"HTTP {resp.status_code} on {path}: {msg}")
            return resp.json()
        raise CMCClientError(f"exhausted retries on {path}: {last_exc}")
