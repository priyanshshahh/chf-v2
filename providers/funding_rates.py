"""Keyless perpetual funding-rate collector for the CHF carry sleeve.

Primary source: Binance USD-M futures public endpoint (no API key):
    GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000
paginated forward by ``startTime`` (1000 events x 8h ~= 333 days per page).

Fallback: OKX public funding-rate history (recent window only):
    GET https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USD-SWAP

Output: data/external/funding_rates.parquet with columns
    symbol            base asset (BTC / ETH / SOL)
    funding_time_utc  tz-aware UTC timestamp of the funding event
    funding_rate      per-interval funding rate (typically 8h)
plus a ``source`` provenance column. Idempotent: merges with any existing
parquet, de-duplicated on (symbol, funding_time_utc).

Be gentle with the public endpoints: only a handful of requests per run.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

BINANCE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OKX_URL = "https://www.okx.com/api/v5/public/funding-rate-history"

DEFAULT_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
OKX_INSTRUMENTS = {"BTC": "BTC-USD-SWAP", "ETH": "ETH-USD-SWAP", "SOL": "SOL-USD-SWAP"}

DEFAULT_OUTPUT = "data/external/funding_rates.parquet"
DEFAULT_START = "2024-01-01"
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0  # seconds; be gentle
MAX_PAGES_PER_SYMBOL = 5


def fetch_binance_funding(
    pair: str,
    start_ms: int,
    session: requests.Session,
    max_pages: int = MAX_PAGES_PER_SYMBOL,
) -> List[dict]:
    """Paginate Binance fundingRate forward from start_ms. Raises on HTTP error."""
    rows: List[dict] = []
    cursor = start_ms
    for _ in range(max_pages):
        resp = session.get(
            BINANCE_URL,
            params={"symbol": pair, "startTime": cursor, "limit": 1000},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            rows.append(
                {
                    "funding_time_ms": int(item["fundingTime"]),
                    "funding_rate": float(item["fundingRate"]),
                    "source": "binance",
                }
            )
        last_ms = int(batch[-1]["fundingTime"])
        if len(batch) < 1000:
            break
        cursor = last_ms + 1
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    return rows


def fetch_okx_funding(
    inst_id: str,
    session: requests.Session,
    max_pages: int = 4,
) -> List[dict]:
    """OKX fallback: paginate backward (newest first) via the ``after`` cursor."""
    rows: List[dict] = []
    after: Optional[int] = None
    for _ in range(max_pages):
        params = {"instId": inst_id, "limit": 100}
        if after is not None:
            params["after"] = after  # records with fundingTime earlier than this
        resp = session.get(OKX_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"okx error for {inst_id}: {payload.get('msg')}")
        batch = payload.get("data", [])
        if not batch:
            break
        for item in batch:
            rows.append(
                {
                    "funding_time_ms": int(item["fundingTime"]),
                    "funding_rate": float(item["fundingRate"]),
                    "source": "okx",
                }
            )
        after = min(int(item["fundingTime"]) for item in batch)
        if len(batch) < 100:
            break
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    return rows


def collect_funding_rates(
    output_path: str | Path = DEFAULT_OUTPUT,
    symbols: Optional[Dict[str, str]] = None,
    start_date: str = DEFAULT_START,
) -> pd.DataFrame:
    """Collect funding rates for all symbols and write/merge the parquet."""
    symbols = symbols or DEFAULT_SYMBOLS
    start_ms = int(
        datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    session = requests.Session()
    session.headers["User-Agent"] = "chf-funding-collector/1.0"

    frames: List[pd.DataFrame] = []
    for base, pair in symbols.items():
        rows: List[dict] = []
        try:
            rows = fetch_binance_funding(pair, start_ms, session)
            print(f"[funding] binance {pair}: {len(rows)} events")
        except Exception as exc:  # noqa: BLE001 - fall back to OKX
            print(f"[funding] binance {pair} failed ({exc}); trying OKX")
        if not rows:
            inst = OKX_INSTRUMENTS.get(base)
            if inst:
                try:
                    rows = fetch_okx_funding(inst, session)
                    print(f"[funding] okx {inst}: {len(rows)} events")
                except Exception as exc:  # noqa: BLE001
                    print(f"[funding] okx {inst} failed ({exc}); skipping {base}")
        if rows:
            df = pd.DataFrame(rows)
            df["symbol"] = base
            frames.append(df)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not frames:
        raise RuntimeError("funding collector: no data from any provider")

    out = pd.concat(frames, ignore_index=True)
    out["funding_time_utc"] = pd.to_datetime(out["funding_time_ms"], unit="ms", utc=True)
    out = out[["symbol", "funding_time_utc", "funding_rate", "source"]]

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        existing["funding_time_utc"] = pd.to_datetime(existing["funding_time_utc"], utc=True)
        out = pd.concat([existing, out], ignore_index=True)
    out = (
        out.drop_duplicates(subset=["symbol", "funding_time_utc"], keep="last")
        .sort_values(["symbol", "funding_time_utc"])
        .reset_index(drop=True)
    )
    out.to_parquet(path, index=False)
    print(f"[funding] wrote {len(out)} rows -> {path}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect perp funding rates (keyless)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD backfill start")
    args = parser.parse_args()
    collect_funding_rates(output_path=args.output, start_date=args.start)


if __name__ == "__main__":
    main()
