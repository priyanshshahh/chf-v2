"""Coinglass liquidation + aggregated-funding collector (key-gated).

Coinglass's open-api v3 now requires an API key on essentially every useful
endpoint. This module is implemented in full but *degrades gracefully*: with no
``COINGLASS_API_KEY`` in the environment it logs a clear "requires
COINGLASS_API_KEY" skip and writes nothing. The moment the user exports a key it
activates automatically — no code change needed.

Endpoints (v3, key sent in the ``CG-API-KEY`` header):

    * liquidation history      GET /api/futures/liquidation/history
    * OI-weighted funding rate GET /api/futures/funding-rate/oi-weight-history

Output (only when a key is present and endpoints return data):
``data/external/liquidations.parquet`` in tidy long format

    symbol   base asset (BTC / ETH / SOL)
    ts_utc   tz-aware UTC timestamp
    metric   one of {long_liquidation_usd, short_liquidation_usd, oi_weight_funding_rate}
    value    float observation
    source   provenance == "coinglass"

Runnable as::

    .venv/bin/python -m providers.derivatives_coinglass
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yaml

from configs.logging_config import get_logger
from src.cmc.storage import upsert

logger = get_logger("providers.derivatives_coinglass")

CONFIG_PATH = "configs/data_sources.yaml"
OUTPUT_KEYS = ["symbol", "ts_utc", "metric"]

DEFAULT_BASE_URL = "https://open-api-v3.coinglass.com"
DEFAULT_OUTPUT = "data/external/liquidations.parquet"
DEFAULT_SYMBOLS: List[str] = ["BTC", "ETH", "SOL"]
DEFAULT_API_KEY_ENV = "COINGLASS_API_KEY"
LIQUIDATION_ENDPOINT = "/api/futures/liquidation/history"
FUNDING_ENDPOINT = "/api/futures/funding-rate/oi-weight-history"
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0


def load_config(config_path: str | Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load the ``derivatives_coinglass`` section of data_sources.yaml."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return (yaml.safe_load(f) or {}).get("derivatives_coinglass", {}) or {}


def _get_json(session: requests.Session, url: str, params: Optional[dict] = None) -> Any:
    """Single network chokepoint: GET ``url`` and return parsed JSON."""
    resp = session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _coinglass_data(payload: Any) -> List[dict]:
    """Unwrap a Coinglass ``{"code": "0", "data": [...]}`` envelope, else raise."""
    if not isinstance(payload, dict):
        raise ValueError("coinglass: non-dict payload")
    if str(payload.get("code")) not in ("0", "0.0"):
        raise RuntimeError(f"coinglass error code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data")
    return list(data) if isinstance(data, list) else []


def parse_liquidations(payload: Any, symbol: str) -> List[dict]:
    """Parse liquidation history into long+short liquidation-usd rows."""
    rows: List[dict] = []
    for item in _coinglass_data(payload):
        ts = item.get("t", item.get("time", item.get("ts")))
        if ts is None:
            continue
        ts_ms = int(ts) if int(ts) > 1e12 else int(ts) * 1000
        long_usd = item.get("longLiquidationUsd", item.get("longVolUsd"))
        short_usd = item.get("shortLiquidationUsd", item.get("shortVolUsd"))
        for metric, raw in (("long_liquidation_usd", long_usd), ("short_liquidation_usd", short_usd)):
            if raw in (None, ""):
                continue
            rows.append(
                {"symbol": symbol, "ts_ms": ts_ms, "metric": metric, "value": float(raw), "source": "coinglass"}
            )
    return rows


def parse_oi_weight_funding(payload: Any, symbol: str) -> List[dict]:
    """Parse OI-weighted funding-rate history into rows."""
    rows: List[dict] = []
    for item in _coinglass_data(payload):
        ts = item.get("t", item.get("time", item.get("ts")))
        rate = item.get("c", item.get("close", item.get("fundingRate")))
        if ts is None or rate in (None, ""):
            continue
        ts_ms = int(ts) if int(ts) > 1e12 else int(ts) * 1000
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": ts_ms,
                "metric": "oi_weight_funding_rate",
                "value": float(rate),
                "source": "coinglass",
            }
        )
    return rows


def collect_coinglass(
    output_path: str | Path = DEFAULT_OUTPUT,
    config: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    api_key: Optional[str] = None,
    write: bool = True,
) -> pd.DataFrame:
    """Collect Coinglass liquidations + OI-weighted funding.

    Degrades to an empty frame with a clear log message when no API key is set.
    Returns the newly-parsed long-format frame; idempotently merged when data is
    present and ``write`` is True.
    """
    cfg = config if config is not None else load_config()
    base_url = str(cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    symbols: List[str] = cfg.get("symbols", DEFAULT_SYMBOLS)
    key_env = str(cfg.get("api_key_env", DEFAULT_API_KEY_ENV))
    endpoints = cfg.get("endpoints", {})
    liq_ep = endpoints.get("liquidation_history", LIQUIDATION_ENDPOINT)
    fund_ep = endpoints.get("funding_aggregated", FUNDING_ENDPOINT)

    api_key = api_key or os.getenv(key_env)
    if not api_key:
        logger.warning(
            "coinglass skipped: requires %s (export the key to activate this collector)", key_env
        )
        return pd.DataFrame(columns=["symbol", "ts_utc", "metric", "value", "source"])

    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "chf-derivatives-collector/1.0"
    session.headers["CG-API-KEY"] = api_key

    rows: List[dict] = []
    for symbol in symbols:
        try:
            payload = _get_json(session, base_url + liq_ep, {"symbol": symbol, "interval": "1d"})
            liq = parse_liquidations(payload, symbol)
            rows.extend(liq)
            logger.info("coinglass liquidations %s: %d rows", symbol, len(liq))
        except Exception as exc:  # noqa: BLE001
            logger.warning("coinglass liquidations %s failed: %s", symbol, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        try:
            payload = _get_json(session, base_url + fund_ep, {"symbol": symbol, "interval": "1d"})
            fund = parse_oi_weight_funding(payload, symbol)
            rows.extend(fund)
            logger.info("coinglass funding %s: %d rows", symbol, len(fund))
        except Exception as exc:  # noqa: BLE001
            logger.warning("coinglass funding %s failed: %s", symbol, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not rows:
        logger.warning("coinglass: no data collected (key present but endpoints returned nothing)")
        return pd.DataFrame(columns=["symbol", "ts_utc", "metric", "value", "source"])

    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[["symbol", "ts_utc", "metric", "value", "source"]]
    df = df.drop_duplicates(subset=OUTPUT_KEYS, keep="last").reset_index(drop=True)

    if write:
        path = Path(output_path)
        total = upsert(df, OUTPUT_KEYS, path)
        logger.info("coinglass: wrote %d new rows, %d total -> %s", len(df), total, path)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Coinglass liquidations (needs API key)")
    parser.add_argument("--output", default=None, help="override output parquet path")
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = args.output or cfg.get("output_path", DEFAULT_OUTPUT)
    collect_coinglass(output_path=output, config=cfg)


if __name__ == "__main__":
    main()
