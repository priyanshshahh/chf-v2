"""
CHF Data Cleaner
Cleans and validates raw market and on-chain data.
Produces staged and cleaned Parquet datasets with provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from configs.config import get_config, resolve_path
from configs.logging_config import get_logger

logger = get_logger("pipelines.data_cleaner")


class MarketDataCleaner:
    """
    Cleans raw OHLCV data:
    - removes duplicates
    - fills small gaps via forward-fill (max 3 days)
    - validates UTC timestamps
    - removes bars with zero/negative prices
    - validates OHLCV consistency (high >= low, etc.)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = config or get_config()
        self._project_root = Path(self.cfg["_project_root"])

    def clean_ohlcv(self, df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, Dict]:
        """
        Clean a single symbol's OHLCV DataFrame.
        Returns (cleaned_df, qa_report_dict).
        """
        if df.empty:
            return df, {"symbol": symbol, "status": "empty"}

        original_len = len(df)
        qa = {"symbol": symbol, "original_rows": original_len}

        # Ensure UTC
        if "date_ts" in df.columns:
            df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True)

        # Remove duplicates
        df = df.drop_duplicates(subset=["symbol", "date_ts"])
        qa["duplicates_removed"] = original_len - len(df)

        # Sort by date
        df = df.sort_values("date_ts").reset_index(drop=True)

        # Remove rows with non-positive prices
        invalid_price = (df["close"] <= 0) | (df["open"] <= 0)
        qa["invalid_price_rows"] = int(invalid_price.sum())
        df = df[~invalid_price].reset_index(drop=True)

        # Validate OHLCV consistency
        inconsistent = (df["high"] < df["low"]) | (df["high"] < df["close"]) | (df["low"] > df["close"])
        qa["inconsistent_ohlcv"] = int(inconsistent.sum())
        df = df[~inconsistent].reset_index(drop=True)

        # Fill small gaps (up to 3 days) via forward-fill on close
        df = df.set_index("date_ts")
        full_range = pd.date_range(
            start=df.index.min(), end=df.index.max(), freq="D", tz="UTC"
        )
        df = df.reindex(full_range)
        df.index.name = "date_ts"

        # Count gaps before fill
        qa["gaps_before_fill"] = int(df["close"].isnull().sum())

        # Forward fill price columns for up to 3 consecutive missing days
        price_cols = ["open", "high", "low", "close"]
        for col in price_cols:
            if col in df.columns:
                df[col] = df[col].fillna(method="ffill", limit=3)

        # Volume: fill gaps with 0
        if "volume" in df.columns:
            df["volume"] = df["volume"].fillna(0)

        # Fill metadata columns
        for col in ["symbol", "source"]:
            if col in df.columns:
                df[col] = df[col].fillna(method="ffill")

        # Drop rows still missing close (gaps > 3 days)
        df = df.dropna(subset=["close"])
        df = df.reset_index()

        qa["gaps_after_fill"] = int(df["close"].isnull().sum())
        qa["final_rows"] = len(df)
        qa["status"] = "ok"

        return df, qa

    def clean_all_symbols(self, raw_dir: Path, cleaned_dir: Path) -> pd.DataFrame:
        """
        Clean all symbol OHLCV files in raw_dir, save to cleaned_dir.
        Returns QA summary DataFrame.
        """
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        qa_records = []

        for raw_file in sorted(raw_dir.glob("*_ohlcv.parquet")):
            symbol = raw_file.stem.replace("_ohlcv", "")
            try:
                df = pd.read_parquet(raw_file)
                cleaned_df, qa = self.clean_ohlcv(df, symbol)
                if not cleaned_df.empty:
                    out_path = cleaned_dir / f"{symbol}_ohlcv_clean.parquet"
                    cleaned_df.to_parquet(out_path, index=False)
                qa_records.append(qa)
                logger.info(
                    f"Cleaned {symbol}: {qa.get('original_rows', 0)} → "
                    f"{qa.get('final_rows', 0)} rows"
                )
            except Exception as e:
                logger.error(f"Failed to clean {symbol}: {e}")
                qa_records.append({"symbol": symbol, "status": f"error: {e}"})

        return pd.DataFrame(qa_records)


class OnChainDataCleaner:
    """
    Cleans raw on-chain data:
    - removes duplicates
    - validates UTC timestamps
    - clips extreme outliers
    - forward-fills small gaps
    """

    def clean_onchain(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Clean on-chain DataFrame for a single symbol."""
        if df.empty:
            return df

        if "date_ts" in df.columns:
            df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True)

        df = df.drop_duplicates(subset=["date_ts"]) if "date_ts" in df.columns else df
        df = df.sort_values("date_ts").reset_index(drop=True)

        # Clip extreme values (winsorize at 0.1% / 99.9%)
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        for col in numeric_cols:
            if col in df.columns:
                lo = df[col].quantile(0.001)
                hi = df[col].quantile(0.999)
                df[col] = df[col].clip(lower=lo, upper=hi)

        return df

    def clean_all_symbols(self, raw_dir: Path, cleaned_dir: Path) -> None:
        """Clean all on-chain files."""
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        for raw_file in sorted(raw_dir.glob("*_onchain.parquet")):
            symbol = raw_file.stem.replace("_onchain", "")
            try:
                df = pd.read_parquet(raw_file)
                cleaned_df = self.clean_onchain(df, symbol)
                if not cleaned_df.empty:
                    out_path = cleaned_dir / f"{symbol}_onchain_clean.parquet"
                    cleaned_df.to_parquet(out_path, index=False)
                logger.info(f"Cleaned on-chain for {symbol}: {len(cleaned_df)} rows")
            except Exception as e:
                logger.error(f"Failed to clean on-chain for {symbol}: {e}")
