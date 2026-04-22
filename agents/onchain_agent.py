"""
CHF OnChainAgent
Fetches on-chain metrics from CoinMetrics Community and DeFiLlama.
Degrades gracefully for unsupported assets/networks.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from agents.base import AgentBase
from providers.coinmetrics import CoinMetricsProvider
from providers.defillama import DeFiLlamaProvider


class OnChainAgent(AgentBase):
    """
    Fetches on-chain data from free sources.

    Pipeline position: After MarketDataAgent.
    Outputs: data/raw/onchain/{SYMBOL}_onchain.parquet
             data/raw/onchain/coverage_report.parquet
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        symbols: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        super().__init__(config)
        self.symbols = symbols or []
        self.start_date = start_date or (
            datetime.now(timezone.utc) - timedelta(days=730)
        ).strftime("%Y-%m-%d")
        self.end_date = end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        oc_cfg = self.cfg.get("on_chain", {})
        self.cm_provider = CoinMetricsProvider(
            rate_limit_sleep=0.5,
            max_retries=5,
        )
        self.dl_provider = DeFiLlamaProvider(
            rate_limit_sleep=0.3,
            max_retries=5,
        )
        self.metrics_list = oc_cfg.get("metrics", [
            "AdrActCnt", "TxCnt", "CapRealUSD", "CapMVRVCur",
            "NVTAdj", "FeeTotUSD", "TxTfrValAdjUSD",
        ])
        self._coverage_records: List[Dict] = []

    def prepare(self) -> None:
        """Load symbols from universe if not provided."""
        if not self.symbols:
            self._load_symbols_from_universe()
        out_dir = self.get_path("raw") / "onchain"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"OnChainAgent prepared | {len(self.symbols)} symbols | "
            f"{self.start_date} → {self.end_date}"
        )

    def _load_symbols_from_universe(self) -> None:
        """Load eligible symbols from the latest universe snapshot."""
        universe_dir = self.get_path("raw") / "universe"
        files = sorted(universe_dir.glob("universe_*.parquet"), reverse=True)
        if files:
            df = pd.read_parquet(files[0])
            self.symbols = df["symbol"].tolist()
        else:
            self.symbols = ["BTC", "ETH", "SOL", "ADA", "AVAX"]

    def run(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch on-chain data for all symbols from CoinMetrics + DeFiLlama.
        Returns dict: symbol -> merged DataFrame.
        """
        self.generate_snapshot_id(f"onchain:{self.start_date}:{self.end_date}")
        result: Dict[str, pd.DataFrame] = {}

        for i, symbol in enumerate(self.symbols):
            self.logger.info(
                f"Fetching on-chain for {symbol} ({i+1}/{len(self.symbols)})"
            )
            merged = self._fetch_symbol(symbol)
            if not merged.empty:
                result[symbol] = merged

        self.metrics["symbols_with_onchain"] = len(result)
        self.metrics["symbols_no_onchain"] = len(self.symbols) - len(result)
        return result

    def _fetch_symbol(self, symbol: str) -> pd.DataFrame:
        """Fetch and merge CoinMetrics + DeFiLlama data for one symbol."""
        frames = []

        # CoinMetrics
        try:
            cm_df = self.cm_provider.get_asset_metrics(
                symbol=symbol,
                metrics=self.metrics_list,
                start_date=self.start_date,
                end_date=self.end_date,
                snapshot_id=self.snapshot_id,
            )
            if not cm_df.empty:
                frames.append(cm_df)
                self._record_coverage(symbol, "coinmetrics", cm_df)
        except Exception as e:
            self.logger.warning(f"CoinMetrics failed for {symbol}: {e}")

        # DeFiLlama
        try:
            dl_df = self.dl_provider.build_symbol_onchain_features(
                symbol=symbol,
                start_date=self.start_date,
                end_date=self.end_date,
                snapshot_id=self.snapshot_id,
            )
            if not dl_df.empty:
                # Keep only date_ts and value columns
                keep_cols = ["date_ts"] + [
                    c for c in dl_df.columns
                    if c in ("tvl_usd", "fees_usd", "dex_volume_usd")
                ]
                dl_df = dl_df[keep_cols].copy()
                dl_df["symbol"] = symbol.upper()
                frames.append(dl_df)
                self._record_coverage(symbol, "defillama", dl_df)
        except Exception as e:
            self.logger.warning(f"DeFiLlama failed for {symbol}: {e}")

        if not frames:
            return pd.DataFrame()

        # Merge all frames on date_ts + symbol
        merged = frames[0]
        for df in frames[1:]:
            # Find common key columns
            merge_on = ["date_ts", "symbol"]
            merge_on = [c for c in merge_on if c in df.columns and c in merged.columns]
            merged = merged.merge(df, on=merge_on, how="outer", suffixes=("", "_dup"))
            # Drop duplicate columns
            dup_cols = [c for c in merged.columns if c.endswith("_dup")]
            merged = merged.drop(columns=dup_cols)

        merged = merged.sort_values("date_ts").reset_index(drop=True)
        return merged

    def _record_coverage(self, symbol: str, source: str, df: pd.DataFrame) -> None:
        """Record coverage metrics for the QA report."""
        if df.empty:
            return
        value_cols = [
            c for c in df.columns
            if c not in ("date_ts", "symbol", "source", "snapshot_id",
                         "retrieved_at", "protocol")
        ]
        for col in value_cols:
            if col in df.columns:
                non_null = df[col].notna().sum()
                total = len(df)
                self._coverage_records.append({
                    "symbol": symbol,
                    "metric_name": col,
                    "source": source,
                    "available_days": int(non_null),
                    "total_days": int(total),
                    "coverage_pct": float(non_null / total) if total > 0 else 0.0,
                    "run_id": self.run_id,
                })

    def persist(self, result: Dict[str, pd.DataFrame]) -> None:
        """Save on-chain data and coverage report."""
        out_dir = self.get_path("raw") / "onchain"
        out_dir.mkdir(parents=True, exist_ok=True)

        for symbol, df in result.items():
            if df.empty:
                continue
            path = out_dir / f"{symbol}_onchain.parquet"
            df.to_parquet(path, index=False)
            self.output_paths[f"onchain_{symbol}"] = str(path)

        # Coverage report
        if self._coverage_records:
            cov_df = pd.DataFrame(self._coverage_records)
            cov_path = out_dir / "coverage_report.parquet"
            cov_df.to_parquet(cov_path, index=False)
            self.output_paths["coverage_report"] = str(cov_path)
            self.logger.info(f"Coverage report saved: {cov_path}")

        manifest = {
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "symbols_with_data": list(result.keys()),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(out_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        self.logger.info(
            f"OnChainAgent persisted {len(result)} symbols | "
            f"snapshot_id={self.snapshot_id}"
        )

    def load_onchain(self, symbol: str) -> pd.DataFrame:
        """Load on-chain data for a single symbol."""
        path = self.get_path("raw") / "onchain" / f"{symbol}_onchain.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def load_coverage_report(self) -> pd.DataFrame:
        """Load the coverage report."""
        path = self.get_path("raw") / "onchain" / "coverage_report.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)
