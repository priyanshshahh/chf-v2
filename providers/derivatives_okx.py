"""Keyless OKX derivatives collector for CHF's data-breadth layer.

Extends the funding-only collector (``providers/funding_rates.py``) to the full
set of OKX public derivatives signals — the orthogonal, non-price data a crypto
carry / positioning edge is built from:

    * open interest          GET /api/v5/public/open-interest
    * funding-rate history   GET /api/v5/public/funding-rate-history
    * long/short account ratio
                             GET /api/v5/rubik/stat/contracts/long-short-account-ratio

All endpoints are keyless. Symbols default to the most liquid USDT-margined
perpetuals (BTC, ETH, SOL, XRP, DOGE); override via ``configs/data_sources.yaml``.

Output: ``data/external/derivatives_okx.parquet`` in tidy long format

    symbol   base asset (BTC / ETH / SOL / ...)
    ts_utc   tz-aware UTC timestamp of the observation
    metric   one of {open_interest, funding_rate, long_short_ratio}
    value    float observation
    source   provenance == "okx"

Idempotent: merges with any existing parquet, de-duplicated on
(symbol, ts_utc, metric) via ``src.cmc.storage.upsert``. Every network call is
wrapped so a single endpoint failure logs and is skipped rather than crashing
the run. Be gentle: >= 1s between requests, small pagination.

Runnable as::

    .venv/bin/python -m providers.derivatives_okx
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

logger = get_logger("providers.derivatives_okx")

CONFIG_PATH = "configs/data_sources.yaml"
OUTPUT_KEYS = ["symbol", "ts_utc", "metric"]

# Defaults mirror configs/data_sources.yaml so the module runs standalone even
# if the config is missing.
DEFAULT_BASE_URL = "https://www.okx.com"
DEFAULT_OUTPUT = "data/external/derivatives_okx.parquet"
DEFAULT_INSTRUMENTS: Dict[str, str] = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
    "XRP": "XRP-USDT-SWAP",
    "DOGE": "DOGE-USDT-SWAP",
}
DEFAULT_RATIO_PERIOD = "1H"
DEFAULT_FUNDING_PAGES = 3
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0

OI_ENDPOINT = "/api/v5/public/open-interest"
FUNDING_ENDPOINT = "/api/v5/public/funding-rate-history"
RATIO_ENDPOINT = "/api/v5/rubik/stat/contracts/long-short-account-ratio"


def load_config(config_path: str | Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load the ``derivatives_okx`` section of data_sources.yaml (best effort)."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return (yaml.safe_load(f) or {}).get("derivatives_okx", {}) or {}


def _get_json(session: requests.Session, url: str, params: Optional[dict] = None) -> Any:
    """Single network chokepoint: GET ``url`` and return parsed JSON.

    Raises ``requests.HTTPError`` on non-2xx so callers can log-and-skip. Tests
    monkeypatch this function to inject synthetic payloads without live network.
    """
    resp = session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _okx_data(payload: Any) -> List[dict]:
    """Unwrap an OKX ``{"code": "0", "data": [...]}`` envelope, else raise."""
    if not isinstance(payload, dict):
        raise ValueError("okx: non-dict payload")
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"okx error code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data")
    return list(data) if isinstance(data, list) else []


def parse_open_interest(payload: Any, symbol: str) -> List[dict]:
    """Parse /public/open-interest into long-format rows (metric=open_interest).

    ``oiCcy`` is open interest denominated in the base coin; we record that as
    the portable, USD-independent measure.
    """
    rows: List[dict] = []
    for item in _okx_data(payload):
        ts = item.get("ts")
        oi = item.get("oiCcy", item.get("oi"))
        if ts is None or oi in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts),
                "metric": "open_interest",
                "value": float(oi),
                "source": "okx",
            }
        )
    return rows


def parse_funding_history(payload: Any, symbol: str) -> List[dict]:
    """Parse /public/funding-rate-history into rows (metric=funding_rate)."""
    rows: List[dict] = []
    for item in _okx_data(payload):
        ts = item.get("fundingTime")
        rate = item.get("fundingRate")
        if ts is None or rate in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts),
                "metric": "funding_rate",
                "value": float(rate),
                "source": "okx",
            }
        )
    return rows


def parse_long_short_ratio(payload: Any, symbol: str) -> List[dict]:
    """Parse rubik long/short account ratio ([[ts, ratio], ...]) into rows."""
    rows: List[dict] = []
    for item in _okx_data(payload):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        ts, ratio = item[0], item[1]
        if ts is None or ratio in (None, ""):
            continue
        rows.append(
            {
                "symbol": symbol,
                "ts_ms": int(ts),
                "metric": "long_short_ratio",
                "value": float(ratio),
                "source": "okx",
            }
        )
    return rows


def collect_okx_derivatives(
    output_path: str | Path = DEFAULT_OUTPUT,
    config: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    write: bool = True,
) -> pd.DataFrame:
    """Collect OKX open interest, funding history and long/short ratio.

    Returns the newly-parsed long-format frame. When ``write`` is True the frame
    is idempotently merged into the parquet at ``output_path``. A failure on any
    single endpoint is logged and skipped so the rest of the run proceeds.
    """
    cfg = config if config is not None else load_config()
    base_url = str(cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    instruments: Dict[str, str] = cfg.get("instruments", DEFAULT_INSTRUMENTS)
    ratio_period = str(cfg.get("ratio_period", DEFAULT_RATIO_PERIOD))
    funding_pages = int(cfg.get("funding_history_pages", DEFAULT_FUNDING_PAGES))
    endpoints = cfg.get("endpoints", {})
    oi_ep = endpoints.get("open_interest", OI_ENDPOINT)
    funding_ep = endpoints.get("funding_rate_history", FUNDING_ENDPOINT)
    ratio_ep = endpoints.get("long_short_ratio", RATIO_ENDPOINT)

    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "chf-derivatives-collector/1.0"

    rows: List[dict] = []
    for base, inst in instruments.items():
        # open interest
        try:
            payload = _get_json(session, base_url + oi_ep, {"instType": "SWAP", "instId": inst})
            oi_rows = parse_open_interest(payload, base)
            rows.extend(oi_rows)
            logger.info("okx open-interest %s: %d rows", inst, len(oi_rows))
        except Exception as exc:  # noqa: BLE001 - log and skip
            logger.warning("okx open-interest %s failed: %s", inst, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # funding rate history (backward-paginated via ``after`` cursor)
        after: Optional[int] = None
        collected = 0
        for _ in range(funding_pages):
            params: Dict[str, Any] = {"instId": inst, "limit": 100}
            if after is not None:
                params["after"] = after
            try:
                payload = _get_json(session, base_url + funding_ep, params)
                fr_rows = parse_funding_history(payload, base)
            except Exception as exc:  # noqa: BLE001
                logger.warning("okx funding %s failed: %s", inst, exc)
                break
            if not fr_rows:
                break
            rows.extend(fr_rows)
            collected += len(fr_rows)
            after = min(r["ts_ms"] for r in fr_rows)
            if len(fr_rows) < 100:
                break
            time.sleep(SLEEP_BETWEEN_REQUESTS)
        logger.info("okx funding %s: %d rows", inst, collected)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # long/short account ratio (keyed by currency)
        try:
            payload = _get_json(
                session, base_url + ratio_ep, {"ccy": base, "period": ratio_period}
            )
            ls_rows = parse_long_short_ratio(payload, base)
            rows.extend(ls_rows)
            logger.info("okx long/short %s: %d rows", base, len(ls_rows))
        except Exception as exc:  # noqa: BLE001
            logger.warning("okx long/short %s failed: %s", base, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not rows:
        logger.warning("okx derivatives: no data collected from any endpoint")
        return pd.DataFrame(columns=["symbol", "ts_utc", "metric", "value", "source"])

    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[["symbol", "ts_utc", "metric", "value", "source"]]
    df = df.drop_duplicates(subset=OUTPUT_KEYS, keep="last").reset_index(drop=True)

    if write:
        path = Path(output_path)
        total = upsert(df, OUTPUT_KEYS, path)
        logger.info("okx derivatives: wrote %d new rows, %d total -> %s", len(df), total, path)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect OKX public derivatives (keyless)")
    parser.add_argument("--output", default=None, help="override output parquet path")
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = args.output or cfg.get("output_path", DEFAULT_OUTPUT)
    collect_okx_derivatives(output_path=output, config=cfg)


if __name__ == "__main__":
    main()
