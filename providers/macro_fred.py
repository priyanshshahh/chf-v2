"""Keyless FRED macro collector for CHF's data-breadth layer.

The St. Louis Fed's ``fredgraph.csv`` download form needs no API key:

    GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>

Series collected (override via configs/data_sources.yaml):

    DTWEXBGS  Nominal Broad USD Index (a keyless DXY proxy)
    DGS2      2-Year Treasury yield
    DGS10     10-Year Treasury yield
    VIXCLS    CBOE Volatility Index (VIX)
    T10Y2Y    10Y-2Y spread (yield-curve slope)
    WM2NS     M2 money stock (weekly)

These macro regressors are the orthogonal risk-appetite / liquidity backdrop a
crypto macro overlay is built from.

Output: ``data/external/macro_fred.parquet`` in tidy long format

    date    tz-naive calendar date (UTC-normalized, no time component)
    series  FRED series id
    value   float observation ("." missing markers dropped)

Idempotent upsert on (date, series). If a single series 404s or errors it is
logged and skipped — the run never fails as a whole.

Runnable as::

    .venv/bin/python -m providers.macro_fred
"""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yaml

from configs.logging_config import get_logger
from src.cmc.storage import upsert

logger = get_logger("providers.macro_fred")

CONFIG_PATH = "configs/data_sources.yaml"
OUTPUT_KEYS = ["date", "series"]

DEFAULT_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DEFAULT_OUTPUT = "data/external/macro_fred.parquet"
DEFAULT_SERIES: Dict[str, str] = {
    "DTWEXBGS": "Nominal Broad USD Index (DXY proxy)",
    "DGS2": "2-Year Treasury Constant Maturity Yield",
    "DGS10": "10-Year Treasury Constant Maturity Yield",
    "VIXCLS": "CBOE Volatility Index (VIX)",
    "T10Y2Y": "10Y-2Y Treasury Spread (yield-curve slope)",
    "WM2NS": "M2 Money Stock (weekly)",
}
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = 1.0


def load_config(config_path: str | Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load the ``macro_fred`` section of data_sources.yaml (best effort)."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return (yaml.safe_load(f) or {}).get("macro_fred", {}) or {}


def _get_csv(session: requests.Session, url: str, params: Optional[dict] = None) -> str:
    """Single network chokepoint: GET a FRED CSV and return its text body.

    Raises ``requests.HTTPError`` on non-2xx (e.g. a 404 for an unknown series)
    so the caller can log-and-skip. Tests monkeypatch this to inject CSV text.
    """
    resp = session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_fred_csv(csv_text: str, series_id: str) -> List[Dict[str, Any]]:
    """Parse a fredgraph CSV body into long-format rows {date, series, value}.

    FRED CSVs have a ``DATE`` (or ``observation_date``) column plus one column
    named after the series id; missing observations are the literal ``"."``.
    """
    frame = pd.read_csv(io.StringIO(csv_text))
    if frame.empty or frame.shape[1] < 2:
        return []
    date_col = frame.columns[0]
    # value column is the series id when present, else the second column
    value_col = series_id if series_id in frame.columns else frame.columns[1]
    dates = pd.to_datetime(frame[date_col], errors="coerce")
    values = pd.to_numeric(frame[value_col], errors="coerce")  # "." -> NaN
    rows: List[Dict[str, Any]] = []
    for date, value in zip(dates, values):
        if pd.isna(date) or pd.isna(value):
            continue
        rows.append(
            {
                "date": date.normalize(),
                "series": series_id,
                "value": float(value),
            }
        )
    return rows


def collect_macro_fred(
    output_path: str | Path = DEFAULT_OUTPUT,
    config: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
    write: bool = True,
) -> pd.DataFrame:
    """Download each configured FRED series and merge into the parquet.

    Returns the newly-parsed long-format frame. A 404 or parse failure on any
    single series is logged and skipped so the rest of the run proceeds.
    """
    cfg = config if config is not None else load_config()
    base_url = str(cfg.get("base_url", DEFAULT_BASE_URL))
    series_map: Dict[str, str] = cfg.get("series", DEFAULT_SERIES)

    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = "chf-macro-collector/1.0"

    rows: List[Dict[str, Any]] = []
    for series_id in series_map:
        try:
            csv_text = _get_csv(session, base_url, {"id": series_id})
            parsed = parse_fred_csv(csv_text, series_id)
            rows.extend(parsed)
            logger.info("fred %s: %d observations", series_id, len(parsed))
        except Exception as exc:  # noqa: BLE001 - log and skip this series
            logger.warning("fred %s failed (skipped): %s", series_id, exc)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not rows:
        logger.warning("fred: no series collected")
        return pd.DataFrame(columns=["date", "series", "value"])

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=OUTPUT_KEYS, keep="last").reset_index(drop=True)

    if write:
        path = Path(output_path)
        total = upsert(df, OUTPUT_KEYS, path)
        logger.info("fred: wrote %d new rows, %d total -> %s", len(df), total, path)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect FRED macro series via keyless CSV")
    parser.add_argument("--output", default=None, help="override output parquet path")
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    cfg = load_config(args.config)
    output = args.output or cfg.get("output_path", DEFAULT_OUTPUT)
    collect_macro_fred(output_path=output, config=cfg)


if __name__ == "__main__":
    main()
