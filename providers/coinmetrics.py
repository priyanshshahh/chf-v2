"""
CHF CoinMetrics Community Provider
Fetches free on-chain metrics: active addresses, tx counts, realized cap,
MVRV, NVT, fees, etc. Degrades gracefully for unsupported assets.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

from configs.logging_config import get_logger

logger = get_logger("providers.coinmetrics")

_BASE_URL = "https://community-api.coinmetrics.io/v4"

# Mapping from CHF symbol to CoinMetrics asset ID
_SYMBOL_TO_CM_ASSET: Dict[str, str] = {
    "BTC": "btc",
    "ETH": "eth",
    "LTC": "ltc",
    "BCH": "bch",
    "XRP": "xrp",
    "ADA": "ada",
    "DOT": "dot",
    "LINK": "link",
    "XLM": "xlm",
    "DOGE": "doge",
    "SOL": "sol",
    "AVAX": "avax",
    "MATIC": "matic",
    "ATOM": "atom",
    "UNI": "uni",
    "AAVE": "aave",
    "COMP": "comp",
    "MKR": "mkr",
    "SNX": "snx",
    "YFI": "yfi",
    "SUSHI": "sushi",
    "CRV": "crv",
    "BAL": "bal",
    "ZEC": "zec",
    "XMR": "xmr",
    "ETC": "etc",
    "TRX": "trx",
    "EOS": "eos",
    "NEO": "neo",
    "DASH": "dash",
    "ALGO": "algo",
    "VET": "vet",
    "FIL": "fil",
    "THETA": "theta",
    "XTZ": "xtz",
    "NEAR": "near",
    "ICP": "icp",
    "FTM": "ftm",
    "HBAR": "hbar",
    "EGLD": "egld",
    "FLOW": "flow",
    "MANA": "mana",
    "SAND": "sand",
    "AXS": "axs",
    "ENJ": "enj",
    "CHZ": "chz",
    "GALA": "gala",
    "ONE": "one",
    "CELO": "celo",
    "ZIL": "zil",
    "ICX": "icx",
    "WAVES": "waves",
    "QTUM": "qtum",
    "OMG": "omg",
    "BTT": "btt",
    "HOT": "hot",
    "NANO": "nano",
    "SC": "sc",
    "RVN": "rvn",
    "DGB": "dgb",
    "SYS": "sys",
    "STEEM": "steem",
    "HIVE": "hive",
    "DCR": "dcr",
    "LSK": "lsk",
    "ARK": "ark",
    "XEM": "xem",
    "PIVX": "pivx",
    "ARDR": "ardr",
    "NXT": "nxt",
}

# Metrics available in CoinMetrics Community tier
_COMMUNITY_METRICS = [
    "AdrActCnt",    # Active address count
    "TxCnt",        # Transaction count
    "CapRealUSD",   # Realized cap in USD
    "CapMVRVCur",   # Market value to realized value ratio
    "NVTAdj",       # NVT adjusted (90-day MA)
    "NVTAdj90",     # NVT adjusted 90-day
    "FeeTotUSD",    # Total fees in USD
    "IssTotUSD",    # Total issuance in USD
    "SplyAct1yr",   # Supply active in last 1 year
    "HashRate",     # Hash rate (PoW only)
    "DiffMean",     # Mean difficulty (PoW only)
    "TxTfrValAdjUSD",  # Adjusted transfer value USD
]


class CoinMetricsProvider:
    """
    CoinMetrics Community API provider.
    No API key required for community tier.
    """

    def __init__(
        self,
        rate_limit_sleep: float = 0.5,
        max_retries: int = 5,
        retry_backoff_base: float = 2.0,
    ):
        self.rate_limit_sleep = rate_limit_sleep
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._supported_assets: Optional[List[str]] = None

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """GET with retry and backoff."""
        url = f"{_BASE_URL}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = self.rate_limit_sleep * (2 ** attempt)
                    logger.warning(f"Rate limited, sleeping {wait:.1f}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                time.sleep(self.rate_limit_sleep)
                return resp.json()
            except requests.exceptions.RequestException as e:
                wait = self.retry_backoff_base ** attempt
                logger.warning(f"CoinMetrics error attempt {attempt}: {e}")
                if attempt < self.max_retries:
                    time.sleep(wait)
                else:
                    logger.error(f"Max retries exceeded for {endpoint}")
                    return {}
        return {}

    def get_supported_assets(self) -> List[str]:
        """Return list of assets supported by CoinMetrics Community."""
        if self._supported_assets is not None:
            return self._supported_assets
        data = self._get("/catalog/assets")
        assets = [a["asset"] for a in data.get("data", [])]
        self._supported_assets = assets
        return assets

    def get_asset_metrics(
        self,
        symbol: str,
        metrics: List[str],
        start_date: str,
        end_date: str,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """
        Fetch time-series metrics for a symbol.
        Returns DataFrame with date_ts, metric columns, and provenance fields.
        Degrades gracefully: returns empty DataFrame if asset not supported.
        """
        cm_asset = _SYMBOL_TO_CM_ASSET.get(symbol.upper())
        if not cm_asset:
            logger.debug(f"No CoinMetrics mapping for {symbol}, skipping")
            return pd.DataFrame()

        # Filter to metrics available in community tier
        available_metrics = [m for m in metrics if m in _COMMUNITY_METRICS]
        if not available_metrics:
            return pd.DataFrame()

        metrics_str = ",".join(available_metrics)
        data = self._get(
            "/timeseries/asset-metrics",
            params={
                "assets": cm_asset,
                "metrics": metrics_str,
                "start_time": start_date,
                "end_time": end_date,
                "frequency": "1d",
                "page_size": 10000,
            },
        )

        if not data or "data" not in data:
            logger.debug(f"No CoinMetrics data for {symbol} ({cm_asset})")
            return pd.DataFrame()

        rows = data["data"]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        if "time" not in df.columns:
            return pd.DataFrame()

        df["date_ts"] = pd.to_datetime(df["time"], utc=True)
        df["symbol"] = symbol.upper()
        df["source"] = "coinmetrics_community"
        df["snapshot_id"] = snapshot_id
        df["retrieved_at"] = datetime.now(timezone.utc).isoformat()

        # Rename metric columns to numeric
        for col in available_metrics:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        drop_cols = ["time", "asset"]
        df = df.drop(columns=[c for c in drop_cols if c in df.columns])
        df = df.sort_values("date_ts").reset_index(drop=True)

        logger.info(
            f"CoinMetrics: fetched {len(df)} rows for {symbol} "
            f"metrics={available_metrics}"
        )
        return df

    def fetch_bulk_metrics(
        self,
        symbols: List[str],
        metrics: List[str],
        start_date: str,
        end_date: str,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """Fetch metrics for multiple symbols, concatenate results."""
        all_dfs = []
        for sym in symbols:
            try:
                df = self.get_asset_metrics(
                    sym, metrics, start_date, end_date, snapshot_id
                )
                if not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                logger.warning(f"Failed to fetch CoinMetrics for {sym}: {e}")

        if not all_dfs:
            return pd.DataFrame()
        return pd.concat(all_dfs, ignore_index=True)
