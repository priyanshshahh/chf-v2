"""Keyless Bybit v5 derivatives collector for CHF's data-breadth layer.

Bybit's public v5 market endpoints (no API key) provide a redundant / fallback
feed to OKX for the same positioning signals:

    * funding-rate history   GET /v5/market/funding/history
    * open interest history  GET /v5/market/open-interest
    * ticker snapshot        GET /v5/market/tickers

Binance USD-M futures (fapi.binance.com) is geo-blocked from this host and
returns HTTP 451; it is deliberately NOT collected here. OKX + Bybit together
give redundant coverage.

Output: ``data/external/derivatives_bybit.parquet`` in tidy long format

    symbol   base asset (BTC / ETH / SOL / ...)
    ts_utc   tz-aware UTC timestamp of the observation
    metric   one of {funding_rate, open_interest, open_interest_value, last_price}
    value    float observation
    source   provenance == "bybit"

Idempotent upsert on (symbol, ts_utc, metric). Every network call is wrapped so
a single endpoint failure logs and is skipped. Be gentle: >= 1s between calls.

Runnable as::

    .venv/bin/python -m providers.derivatives_bybit
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yaml

from configs.logging_config import get_logger
from src.cmc.storage import upsert

logger = get_logger("providers.derivatives_bybit")

CONFIG_PATH = "configs/data_sources.yaml"
OUTPUT_KEYS = ["symbol", "ts_utc", "metric"]

DEFAULT_BASE_URL = "https://api.bybit.com"
DEFAULT_OUTPUT = "data/external/derivatives_bybit.parquet"
DEFAULT_CATEGORY = "linear"
DEFAULT_OI_INTERVAL = "1h"
DEFAULT_SYMBOLS: Dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
}
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0

FUNDING_ENDPOINT = "/v5/market/funding/history"
OI_ENDPOINT = "/v5/market/open-interest"
TICKERS_ENDPOINT = "/v5/market/tickers"


def load_config(config_path: str | Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load the ``derivatives_bybit`` section of data_sources.yaml (best effort)."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return (yaml.safe_load(f) or {}).get("derivatives_bybit", {}) or {}


def _get_json(session: requests.Session, url: str, params: Optional[dict] = None) -> Any:
    """Single network chokepoint: GET ``url`` and return parsed JSON.

    Tests monkeypatch this to inject synthetic payloads (no live network).
    """
    resp = session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _bybit_list(payload: Any) -> List[dict]:
    """Unwrap a Bybit ``{"retCode": 0, "result": {"list": [...]}}`` envelope."""
    if not isinstance(payload, dict):
        raise ValueError("bybit: non-dict payload")
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"bybit error retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
        )
    result = payload.get("result") or {}
    items = result.get("list")
    return list(items) if isinstance(items, list) else []


def parse_funding_history(payload: Any, symbol: str) -> List[dict]:
    """Parse /market/funding/history into rows (metric=funding_rate)."""
    rows: List[dict] = []
    for item in _bybit_list(payload):
        ts = item.get("fundingRateTimestamp")
        rate = item.get("fundingRate")
        if ts is None or rate in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts),
                "metric": "funding_rate",
                "value": float(rate),
                "source": "bybit",
            }
        )
    return rows


def parse_open_interest(payload: Any, symbol: str) -> List[dict]:
    """Parse /market/open-interest into rows (metric=open_interest, coin units)."""
    rows: List[dict] = []
    for item in _bybit_list(payload):
        ts = item.get("timestamp")
        oi = item.get("openInterest")
        if ts is None or oi in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts),
                "metric": "open_interest",
                "value": float(oi),
                "source": "bybit",
            }
        )
    return rows


def parse_tickers(payload: Any, symbol: str) -> List[dict]:
    """Parse a /market/tickers snapshot into rows for the current instant.

    Bybit tickers carry no timestamp, so we stamp the response with the
    envelope ``time`` (ms) when present, else the current UTC time.
    """
    items = _bybit_list(payload)
    if not items:
        return []
    ts_ms = payload.get("time") if isinstance(payload, dict) else None
    ts_ms = int(ts_ms) if ts_ms else int(pd.Timestamp.utcnow().timestamp() * 1000)
    item = items[0]
    rows: List[dict] = []
    for metric, key in (
        ("open_interest_value", "openInterestValue"),
        ("last_price", "lastPrice"),
    ):
        raw = item.get(key)
        if raw in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": ts_ms,
                "metric": metric,
                "value": float(raw),
                "source": "bybit",
            }
        )
    return rows


def collect_bybit_derivatives(
    output_path: str | Path = DEFAULT_OUTPUT,
    config: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    write: bool = True,
) -> pd.DataFrame:
    """Collect Bybit funding history, open interest and ticker snapshots.

    Returns the newly-parsed long-format frame; idempotently merged into the
    parquet when ``write`` is True. Per-endpoint failures log and skip.
    """
    cfg = config if config is not None else load_config()
    base_url = str(cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    symbols: Dict[str, str] = cfg.get("symbols", DEFAULT_SYMBOLS)
    category = str(cfg.get("category", DEFAULT_CATEGORY))
    oi_interval = str(cfg.get("oi_interval", DEFAULT_OI_INTERVAL))
    endpoints = cfg.get("endpoints", {})
    funding_ep = endpoints.get("funding_history", FUNDING_ENDPOINT)
    oi_ep = endpoints.get("open_interest", OI_ENDPOINT)
    tickers_ep = endpoints.get("tickers", TICKERS_ENDPOINT)

    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "chf-derivatives-collector/1.0"

    rows: List[dict] = []
    for base, sym in symbols.items():
        # funding-rate history
        try:
            payload = _get_json(
                session,
                base_url + funding_ep,
                {"category": category, "symbol": sym, "limit": 200},
            )
            fr = parse_funding_history(payload, base)
            rows.extend(fr)
            logger.info("bybit funding %s: %d rows", sym, len(fr))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit funding %s failed: %s", sym, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # open-interest history
        try:
            payload = _get_json(
                session,
                base_url + oi_ep,
                {"category": category, "symbol": sym, "intervalTime": oi_interval, "limit": 200},
            )
            oi = parse_open_interest(payload, base)
            rows.extend(oi)
            logger.info("bybit open-interest %s: %d rows", sym, len(oi))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit open-interest %s failed: %s", sym, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # ticker snapshot
        try:
            payload = _get_json(
                session, base_url + tickers_ep, {"category": category, "symbol": sym}
            )
            tk = parse_tickers(payload, base)
            rows.extend(tk)
            logger.info("bybit tickers %s: %d rows", sym, len(tk))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bybit tickers %s failed: %s", sym, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not rows:
        logger.warning("bybit derivatives: no data collected from any endpoint")
        return pd.DataFrame(columns=["symbol", "ts_utc", "metric", "value", "source"])

    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[["symbol", "ts_utc", "metric", "value", "source"]]
    df = df.drop_duplicates(subset=OUTPUT_KEYS, keep="last").reset_index(drop=True)

    if write:
        path = Path(output_path)
        total = upsert(df, OUTPUT_KEYS, path)
        logger.info("bybit derivatives: wrote %d new rows, %d total -> %s", len(df), total, path)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Bybit v5 public derivatives (keyless)")
    parser.add_argument("--output", default=None, help="override output parquet path")
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = args.output or cfg.get("output_path", DEFAULT_OUTPUT)
    collect_bybit_derivatives(output_path=output, config=cfg)


if __name__ == "__main__":
    main()
