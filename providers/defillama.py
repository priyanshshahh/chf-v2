"""
CHF DeFiLlama Provider
Fetches TVL, protocol revenue, DEX volumes, and fee data from DeFiLlama free API.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

from configs.logging_config import get_logger

logger = get_logger("providers.defillama")

_BASE_URL = "https://api.llama.fi"
_COINS_BASE_URL = "https://coins.llama.fi"

# Mapping from CHF symbol to DeFiLlama protocol slug
_SYMBOL_TO_PROTOCOL: Dict[str, str] = {
    "AAVE": "aave",
    "UNI": "uniswap",
    "COMP": "compound",
    "MKR": "makerdao",
    "SNX": "synthetix",
    "YFI": "yearn-finance",
    "SUSHI": "sushiswap",
    "CRV": "curve",
    "BAL": "balancer",
    "LINK": "chainlink",
    "AVAX": "avalanche",
    "SOL": "solana",
    "MATIC": "polygon",
    "FTM": "fantom",
    "NEAR": "near",
    "ATOM": "cosmos",
    "DOT": "polkadot",
    "ADA": "cardano",
    "ALGO": "algorand",
    "TRX": "tron",
    "EOS": "eos",
    "THETA": "theta",
    "VET": "vechain",
    "CELO": "celo",
    "EGLD": "elrond",
    "FLOW": "flow",
    "ICP": "internet-computer",
    "FIL": "filecoin",
    "ONE": "harmony",
    "HBAR": "hedera-hashgraph",
    "WAVES": "waves",
    "XTZ": "tezos",
    "MANA": "decentraland",
    "SAND": "sandbox",
    "AXS": "axie-infinity",
    "ENJ": "enjin",
    "GALA": "gala",
}


class DeFiLlamaProvider:
    """
    DeFiLlama free API provider.
    No API key required.
    """

    def __init__(
        self,
        rate_limit_sleep: float = 0.3,
        max_retries: int = 5,
        retry_backoff_base: float = 2.0,
    ):
        self.rate_limit_sleep = rate_limit_sleep
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, base_url: str, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """GET with retry and backoff."""
        url = f"{base_url}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    wait = self.rate_limit_sleep * (2 ** attempt)
                    logger.warning(f"Rate limited on {endpoint}, sleeping {wait:.1f}s")
                    time.sleep(wait)
                    continue
                if resp.status_code in (404, 400):
                    return {}
                resp.raise_for_status()
                time.sleep(self.rate_limit_sleep)
                return resp.json()
            except requests.exceptions.RequestException as e:
                wait = self.retry_backoff_base ** attempt
                logger.warning(f"DeFiLlama error attempt {attempt}: {e}")
                if attempt < self.max_retries:
                    time.sleep(wait)
                else:
                    return {}
        return {}

    def get_protocol_tvl_history(self, protocol_slug: str) -> pd.DataFrame:
        """
        Fetch historical TVL for a protocol.
        Returns DataFrame with date_ts, tvl columns.
        """
        data = self._get(_BASE_URL, f"/protocol/{protocol_slug}")
        if not data:
            return pd.DataFrame()

        tvl_data = data.get("tvl", [])
        if not tvl_data:
            return pd.DataFrame()

        rows = [
            {
                "date_ts": pd.Timestamp(entry["date"], unit="s", tz="UTC"),
                "tvl_usd": float(entry.get("totalLiquidityUSD", 0) or 0),
            }
            for entry in tvl_data
            if "date" in entry
        ]
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["protocol"] = protocol_slug
        df = df.sort_values("date_ts").reset_index(drop=True)
        return df

    def get_global_tvl_history(self) -> pd.DataFrame:
        """Fetch global DeFi TVL history."""
        data = self._get(_BASE_URL, "/v2/historicalChainTvl")
        if not data:
            return pd.DataFrame()

        rows = [
            {
                "date_ts": pd.Timestamp(entry["date"], unit="s", tz="UTC"),
                "global_tvl_usd": float(entry.get("tvl", 0) or 0),
            }
            for entry in data
            if "date" in entry
        ]
        return pd.DataFrame(rows).sort_values("date_ts").reset_index(drop=True)

    def get_protocol_fees(self, protocol_slug: str) -> pd.DataFrame:
        """Fetch fee and revenue data for a protocol."""
        data = self._get(_BASE_URL, f"/summary/fees/{protocol_slug}")
        if not data:
            return pd.DataFrame()

        total_data = data.get("totalDataChartBreakdown", []) or data.get("totalDataChart", [])
        if not total_data:
            return pd.DataFrame()

        rows = []
        for entry in total_data:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                rows.append({
                    "date_ts": pd.Timestamp(entry[0], unit="s", tz="UTC"),
                    "fees_usd": float(entry[1]) if entry[1] else 0.0,
                })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["protocol"] = protocol_slug
        return df.sort_values("date_ts").reset_index(drop=True)

    def get_dex_volume(self, protocol_slug: str) -> pd.DataFrame:
        """Fetch DEX volume history for a protocol."""
        data = self._get(_BASE_URL, f"/summary/dexs/{protocol_slug}")
        if not data:
            return pd.DataFrame()

        vol_data = data.get("totalDataChart", [])
        if not vol_data:
            return pd.DataFrame()

        rows = []
        for entry in vol_data:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                rows.append({
                    "date_ts": pd.Timestamp(entry[0], unit="s", tz="UTC"),
                    "dex_volume_usd": float(entry[1]) if entry[1] else 0.0,
                })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["protocol"] = protocol_slug
        return df.sort_values("date_ts").reset_index(drop=True)

    def build_symbol_onchain_features(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """
        Build on-chain feature DataFrame for a symbol using DeFiLlama.
        Returns DataFrame with date_ts, tvl_usd, fees_usd, dex_volume_usd.
        Degrades gracefully if protocol not mapped.
        """
        protocol = _SYMBOL_TO_PROTOCOL.get(symbol.upper())
        if not protocol:
            logger.debug(f"No DeFiLlama protocol mapping for {symbol}")
            return pd.DataFrame()

        retrieved_at = datetime.now(timezone.utc).isoformat()
        start_ts = pd.Timestamp(start_date, tz="UTC")
        end_ts = pd.Timestamp(end_date, tz="UTC")

        # TVL
        tvl_df = self.get_protocol_tvl_history(protocol)
        if not tvl_df.empty:
            tvl_df = tvl_df[
                (tvl_df["date_ts"] >= start_ts) & (tvl_df["date_ts"] <= end_ts)
            ][["date_ts", "tvl_usd"]]

        # Fees
        fee_df = self.get_protocol_fees(protocol)
        if not fee_df.empty:
            fee_df = fee_df[
                (fee_df["date_ts"] >= start_ts) & (fee_df["date_ts"] <= end_ts)
            ][["date_ts", "fees_usd"]]

        # DEX volume
        dex_df = self.get_dex_volume(protocol)
        if not dex_df.empty:
            dex_df = dex_df[
                (dex_df["date_ts"] >= start_ts) & (dex_df["date_ts"] <= end_ts)
            ][["date_ts", "dex_volume_usd"]]

        # Merge all
        base_dates = pd.date_range(start=start_ts, end=end_ts, freq="D", tz="UTC")
        result = pd.DataFrame({"date_ts": base_dates})

        for df, col in [(tvl_df, "tvl_usd"), (fee_df, "fees_usd"), (dex_df, "dex_volume_usd")]:
            if not df.empty:
                result = result.merge(df, on="date_ts", how="left")
            else:
                result[col] = None

        result["symbol"] = symbol.upper()
        result["source"] = "defillama"
        result["snapshot_id"] = snapshot_id
        result["retrieved_at"] = retrieved_at
        result["protocol"] = protocol

        logger.info(
            f"DeFiLlama: built {len(result)} rows for {symbol} (protocol={protocol})"
        )
        return result

    def fetch_bulk_tvl(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """Fetch DeFiLlama on-chain features for multiple symbols."""
        all_dfs = []
        for sym in symbols:
            try:
                df = self.build_symbol_onchain_features(
                    sym, start_date, end_date, snapshot_id
                )
                if not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                logger.warning(f"DeFiLlama failed for {sym}: {e}")

        if not all_dfs:
            return pd.DataFrame()
        return pd.concat(all_dfs, ignore_index=True)
