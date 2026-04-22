"""
CHF MarketDataAgent
Fetches and stores daily OHLCV data for all universe symbols using CCXT/Binance.
Supports backfill, incremental updates, and data QA.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from agents.base import AgentBase
from providers.ccxt_binance import CCXTBinanceProvider


class MarketDataAgent(AgentBase):
    """
    Fetches daily OHLCV data for universe symbols.

    Pipeline position: After UniverseAgent.
    Outputs: data/raw/market/{SYMBOL}_ohlcv.parquet
             data/raw/market/qa_report.parquet
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        symbols: Optional[List[str]] = None,
    ):
        super().__init__(config)
        self.symbols = symbols or []
        self.provider = CCXTBinanceProvider(
            quote_currency=self.cfg.get("market_data", {}).get("quote_currency", "USDT"),
            timeframe=self.cfg.get("market_data", {}).get("timeframe", "1d"),
            rate_limit_sleep=self.cfg.get("market_data", {}).get("rate_limit_sleep", 0.5),
            max_retries=self.cfg.get("market_data", {}).get("retry_attempts", 5),
            retry_backoff_base=self.cfg.get("market_data", {}).get("retry_backoff_base", 2.0),
        )
        self._qa_records: List[Dict] = []

    def prepare(self) -> None:
        """Load symbols from universe if not provided."""
        if not self.symbols:
            self._load_symbols_from_universe()

        # Always include benchmarks
        benchmarks = self.cfg.get("market_data", {}).get("benchmarks", ["BTC", "ETH"])
        for b in benchmarks:
            if b not in self.symbols:
                self.symbols.append(b)

        out_dir = self.get_path("raw") / "market"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"MarketDataAgent prepared | {len(self.symbols)} symbols"
        )

    def _load_symbols_from_universe(self) -> None:
        """Load eligible symbols from the latest universe snapshot."""
        universe_dir = self.get_path("raw") / "universe"
        files = sorted(universe_dir.glob("universe_*.parquet"), reverse=True)
        if files:
            df = pd.read_parquet(files[0])
            self.symbols = df["symbol"].tolist()
            self.logger.info(f"Loaded {len(self.symbols)} symbols from universe")
        else:
            self.logger.warning("No universe snapshot found, using default symbols")
            self.symbols = ["BTC", "ETH", "BNB", "SOL", "ADA", "AVAX", "DOT", "LINK"]

    def run(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch OHLCV for all symbols.
        Returns dict: symbol -> DataFrame.
        """
        backfill_days = self.cfg.get("market_data", {}).get("backfill_days", 730)
        self.generate_snapshot_id(f"market:{','.join(sorted(self.symbols))}")

        out_dir = self.get_path("raw") / "market"
        all_data: Dict[str, pd.DataFrame] = {}
        failed_symbols = []

        for i, symbol in enumerate(self.symbols):
            self.logger.info(
                f"Fetching OHLCV for {symbol} ({i+1}/{len(self.symbols)})"
            )

            # Check if we have existing data for incremental update
            existing_path = out_dir / f"{symbol}_ohlcv.parquet"
            if existing_path.exists():
                existing_df = pd.read_parquet(existing_path)
                if not existing_df.empty:
                    last_date = existing_df["date_ts"].max()
                    days_since = (
                        datetime.now(timezone.utc) - last_date.to_pydatetime()
                    ).days
                    if days_since <= 1:
                        self.logger.info(f"{symbol} is up to date, skipping")
                        all_data[symbol] = existing_df
                        continue
                    # Incremental: fetch only missing days
                    new_df = self.provider.fetch_ohlcv(
                        symbol,
                        since_dt=last_date.to_pydatetime(),
                        snapshot_id=self.snapshot_id,
                    )
                    if not new_df.empty:
                        combined = pd.concat([existing_df, new_df], ignore_index=True)
                        combined = combined.drop_duplicates(subset=["symbol", "date_ts"])
                        combined = combined.sort_values("date_ts").reset_index(drop=True)
                        all_data[symbol] = combined
                    else:
                        all_data[symbol] = existing_df
                    continue

            # Full backfill
            df = self.provider.backfill_ohlcv(
                symbol, days=backfill_days, snapshot_id=self.snapshot_id
            )
            if df.empty:
                self.logger.warning(f"No data for {symbol}")
                failed_symbols.append(symbol)
            else:
                all_data[symbol] = df

            # QA
            qa = self.provider.validate_ohlcv(df)
            qa["run_id"] = self.run_id
            qa["checked_at"] = datetime.now(timezone.utc).isoformat()
            self._qa_records.append(qa)

        self.metrics["symbols_fetched"] = len(all_data)
        self.metrics["symbols_failed"] = len(failed_symbols)
        if failed_symbols:
            self.logger.warning(f"Failed symbols: {failed_symbols}")

        return all_data

    def persist(self, result: Dict[str, pd.DataFrame]) -> None:
        """Save OHLCV data with hive-style partitioning AND flat files.

        Hive layout: data/raw/market/year=YYYY/month=MM/SYMBOL.parquet
        Flat layout:  data/raw/market/SYMBOL_ohlcv.parquet  (backward compat)
        """
        out_dir = self.get_path("raw") / "market"
        out_dir.mkdir(parents=True, exist_ok=True)

        for symbol, df in result.items():
            if df.empty:
                continue
            # Flat file (backward compatibility)
            flat_path = out_dir / f"{symbol}_ohlcv.parquet"
            df.to_parquet(flat_path, index=False)
            self.output_paths[f"ohlcv_{symbol}"] = str(flat_path)

            # Hive-partitioned: data/raw/market/year=YYYY/month=MM/SYMBOL.parquet
            df_ts = df.copy()
            df_ts["date_ts"] = pd.to_datetime(df_ts["date_ts"], utc=True)
            for (yr, mo), grp in df_ts.groupby(
                [df_ts["date_ts"].dt.year, df_ts["date_ts"].dt.month]
            ):
                hive_dir = out_dir / f"year={yr}" / f"month={mo:02d}"
                hive_dir.mkdir(parents=True, exist_ok=True)
                hive_path = hive_dir / f"{symbol}.parquet"
                grp.to_parquet(hive_path, index=False)
                self.output_paths[f"hive_{symbol}_{yr}_{mo:02d}"] = str(hive_path)
            self.logger.debug(f"Hive-partitioned {symbol} saved")

        # Save QA report
        if self._qa_records:
            qa_df = pd.DataFrame(self._qa_records)
            qa_path = out_dir / "qa_report.parquet"
            qa_df.to_parquet(qa_path, index=False)
            self.output_paths["qa_report"] = str(qa_path)
            self.logger.info(f"QA report saved: {qa_path}")

        # Save manifest
        manifest = {
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "symbols": list(result.keys()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(out_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        self.logger.info(
            f"MarketDataAgent persisted {len(result)} symbols | "
            f"snapshot_id={self.snapshot_id}"
        )

    def load_ohlcv(self, symbol: str) -> pd.DataFrame:
        """Load OHLCV for a single symbol."""
        path = self.get_path("raw") / "market" / f"{symbol}_ohlcv.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def load_all_ohlcv(self) -> pd.DataFrame:
        """Load all OHLCV data concatenated."""
        market_dir = self.get_path("raw") / "market"
        files = list(market_dir.glob("*_ohlcv.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in files]
        return pd.concat(dfs, ignore_index=True)

    def get_qa_report(self) -> pd.DataFrame:
        """Load the QA report."""
        qa_path = self.get_path("raw") / "market" / "qa_report.parquet"
        if not qa_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(qa_path)
