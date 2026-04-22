"""
CHF CCXT Binance Provider
Fetches daily OHLCV data using CCXT with Binance public endpoints.
No API key required for market data.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from configs.logging_config import get_logger

logger = get_logger("providers.ccxt_binance")


class CCXTBinanceProvider:
    """
    OHLCV data provider using CCXT + Binance public API.
    Supports backfill and incremental updates with retry logic.
    """

    def __init__(
        self,
        quote_currency: str = "USDT",
        timeframe: str = "1d",
        rate_limit_sleep: float = 0.5,
        max_retries: int = 5,
        retry_backoff_base: float = 2.0,
    ):
        self.quote_currency = quote_currency
        self.timeframe = timeframe
        self.rate_limit_sleep = rate_limit_sleep
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self._exchange = None

    def _get_exchange(self):
        """Lazily initialize the CCXT Binance exchange."""
        if self._exchange is None:
            try:
                import ccxt
                self._exchange = ccxt.binance({
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                })
            except ImportError:
                raise ImportError("ccxt not installed. Run: pip install ccxt")
        return self._exchange

    def _symbol_to_pair(self, symbol: str) -> str:
        """Convert bare symbol (e.g. BTC) to CCXT pair (e.g. BTC/USDT)."""
        symbol = symbol.upper().replace("/USDT", "").replace("-USDT", "")
        return f"{symbol}/{self.quote_currency}"

    def fetch_ohlcv(
        self,
        symbol: str,
        since_dt: Optional[datetime] = None,
        limit: int = 1000,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV for a symbol from Binance.
        Returns a DataFrame with columns: symbol, date_ts, open, high, low, close, volume.
        """
        exchange = self._get_exchange()
        pair = self._symbol_to_pair(symbol)
        since_ms = None
        if since_dt:
            since_ms = int(since_dt.timestamp() * 1000)

        all_bars = []
        retrieved_at = datetime.now(timezone.utc).isoformat()

        for attempt in range(1, self.max_retries + 1):
            try:
                bars = exchange.fetch_ohlcv(
                    pair, timeframe=self.timeframe, since=since_ms, limit=limit
                )
                all_bars.extend(bars)
                break
            except Exception as e:
                wait = self.retry_backoff_base ** attempt
                logger.warning(
                    f"OHLCV fetch error for {pair} attempt {attempt}: {e} | "
                    f"sleeping {wait:.1f}s"
                )
                if attempt < self.max_retries:
                    time.sleep(wait)
                else:
                    logger.error(f"Max retries exceeded for {pair}")
                    return pd.DataFrame()

        if not all_bars:
            logger.warning(f"No OHLCV data returned for {pair}")
            return pd.DataFrame()

        df = pd.DataFrame(
            all_bars, columns=["timestamp_ms", "open", "high", "low", "close", "volume"]
        )
        df["date_ts"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df["symbol"] = symbol.upper()
        df["source"] = "binance"
        df["retrieved_at"] = retrieved_at
        df["snapshot_id"] = snapshot_id
        df = df.drop(columns=["timestamp_ms"])
        df = df[["symbol", "date_ts", "open", "high", "low", "close", "volume",
                 "source", "retrieved_at", "snapshot_id"]]
        df = df.sort_values("date_ts").reset_index(drop=True)

        logger.info(f"Fetched {len(df)} bars for {symbol} from Binance")
        return df

    def backfill_ohlcv(
        self,
        symbol: str,
        days: int = 730,
        snapshot_id: str = "",
    ) -> pd.DataFrame:
        """
        Backfill OHLCV data for the past N days.
        Handles pagination for large date ranges.
        """
        from datetime import timedelta
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        exchange = self._get_exchange()
        pair = self._symbol_to_pair(symbol)

        all_bars = []
        since_ms = int(since_dt.timestamp() * 1000)
        retrieved_at = datetime.now(timezone.utc).isoformat()

        while True:
            for attempt in range(1, self.max_retries + 1):
                try:
                    bars = exchange.fetch_ohlcv(
                        pair, timeframe=self.timeframe, since=since_ms, limit=1000
                    )
                    break
                except Exception as e:
                    wait = self.retry_backoff_base ** attempt
                    logger.warning(
                        f"Backfill error for {pair} attempt {attempt}: {e}"
                    )
                    if attempt < self.max_retries:
                        time.sleep(wait)
                    else:
                        logger.error(f"Backfill failed for {pair}")
                        bars = []
                        break

            if not bars:
                break

            all_bars.extend(bars)
            last_ts = bars[-1][0]
            if len(bars) < 1000:
                break
            since_ms = last_ts + 1
            time.sleep(self.rate_limit_sleep)

        if not all_bars:
            logger.warning(f"No backfill data for {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(
            all_bars, columns=["timestamp_ms", "open", "high", "low", "close", "volume"]
        )
        df["date_ts"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
        df["symbol"] = symbol.upper()
        df["source"] = "binance"
        df["retrieved_at"] = retrieved_at
        df["snapshot_id"] = snapshot_id
        df = df.drop(columns=["timestamp_ms"])
        df = df[["symbol", "date_ts", "open", "high", "low", "close", "volume",
                 "source", "retrieved_at", "snapshot_id"]]
        df = df.drop_duplicates(subset=["symbol", "date_ts"])
        df = df.sort_values("date_ts").reset_index(drop=True)

        logger.info(f"Backfilled {len(df)} bars for {symbol}")
        return df

    def get_available_symbols(self) -> List[str]:
        """Return list of available USDT pairs on Binance."""
        exchange = self._get_exchange()
        markets = exchange.load_markets()
        usdt_pairs = [
            m.split("/")[0]
            for m in markets
            if m.endswith(f"/{self.quote_currency}")
            and markets[m].get("active", False)
            and markets[m].get("spot", False)
        ]
        return sorted(usdt_pairs)

    def validate_ohlcv(self, df: pd.DataFrame) -> Dict:
        """
        Validate OHLCV data quality.
        Returns a dict of QA metrics.
        """
        if df.empty:
            return {"valid": False, "reason": "empty_dataframe"}

        qa = {
            "symbol": df["symbol"].iloc[0] if "symbol" in df.columns else "unknown",
            "total_bars": len(df),
            "duplicate_bars": int(df.duplicated(subset=["symbol", "date_ts"]).sum()),
            "missing_close": int(df["close"].isnull().sum()),
            "missing_volume": int(df["volume"].isnull().sum()),
            "zero_volume_bars": int((df["volume"] == 0).sum()),
            "negative_close": int((df["close"] <= 0).sum()),
            "utc_validated": all(
                getattr(ts, "tzinfo", None) is not None for ts in df["date_ts"]
            ) if "date_ts" in df.columns else False,
        }

        if "date_ts" in df.columns and len(df) > 1:
            df_sorted = df.sort_values("date_ts")
            diffs = df_sorted["date_ts"].diff().dropna()
            expected_diff = pd.Timedelta("1D")
            gaps = (diffs > expected_diff * 1.5).sum()
            qa["gap_count"] = int(gaps)
            qa["first_date"] = str(df_sorted["date_ts"].iloc[0])
            qa["last_date"] = str(df_sorted["date_ts"].iloc[-1])
        else:
            qa["gap_count"] = 0

        qa["valid"] = (
            qa["duplicate_bars"] == 0
            and qa["missing_close"] == 0
            and qa["negative_close"] == 0
        )
        return qa
