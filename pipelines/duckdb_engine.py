"""
CHF DuckDB Analytics Engine
Provides analytical views, joins, and QA queries over the Parquet data lake.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from configs.config import get_config
from configs.logging_config import get_logger

logger = get_logger("pipelines.duckdb_engine")


class DuckDBEngine:
    """
    DuckDB-based analytics engine for the CHF data lake.
    All data is stored as Parquet; DuckDB provides SQL-based views and joins.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = config or get_config()
        self._project_root = Path(self.cfg["_project_root"])
        self._conn = None

    def _get_conn(self):
        """Lazily initialize DuckDB connection."""
        if self._conn is None:
            try:
                import duckdb
                self._conn = duckdb.connect(database=":memory:")
                logger.info("DuckDB in-memory connection established")
            except ImportError:
                raise ImportError("duckdb not installed. Run: pip install duckdb")
        return self._conn

    def _resolve(self, key: str) -> Path:
        """Resolve a data path."""
        raw = self.cfg["paths"].get(key, key)
        p = Path(raw)
        if not p.is_absolute():
            p = self._project_root / p
        return p

    # ─────────────────────────────────────────
    # Market data queries
    # ─────────────────────────────────────────

    def load_market_data(self, symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """Load all OHLCV data from Parquet into a DataFrame."""
        market_dir = self._resolve("raw") / "market"
        if not market_dir.exists():
            return pd.DataFrame()

        pattern = str(market_dir / "*_ohlcv.parquet")
        conn = self._get_conn()
        try:
            df = conn.execute(
                f"SELECT * FROM read_parquet('{pattern}') ORDER BY symbol, date_ts"
            ).df()
            if symbols:
                df = df[df["symbol"].isin([s.upper() for s in symbols])]
            return df
        except Exception as e:
            logger.warning(f"DuckDB market load failed: {e}, falling back to pandas")
            return self._pandas_load_market(symbols)

    def _pandas_load_market(self, symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """Fallback: load market data using pandas."""
        market_dir = self._resolve("raw") / "market"
        files = list(market_dir.glob("*_ohlcv.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in files]
        df = pd.concat(dfs, ignore_index=True)
        if symbols:
            df = df[df["symbol"].isin([s.upper() for s in symbols])]
        return df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)

    def load_onchain_data(self, symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """Load on-chain data from Parquet."""
        onchain_dir = self._resolve("raw") / "onchain"
        if not onchain_dir.exists():
            return pd.DataFrame()

        files = list(onchain_dir.glob("*_onchain.parquet"))
        if not files:
            return pd.DataFrame()

        dfs = [pd.read_parquet(f) for f in files]
        df = pd.concat(dfs, ignore_index=True)
        if symbols:
            df = df[df["symbol"].isin([s.upper() for s in symbols])]
        return df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)

    def load_features(self) -> pd.DataFrame:
        """Load the feature store."""
        features_dir = self._resolve("features")
        files = list(features_dir.glob("features_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in sorted(files)]
        return pd.concat(dfs, ignore_index=True)

    def load_labels(self, horizon: Optional[int] = None) -> pd.DataFrame:
        """Load labels, optionally filtered by horizon."""
        labels_dir = self._resolve("labels")
        files = list(labels_dir.glob("labels_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in sorted(files)]
        df = pd.concat(dfs, ignore_index=True)
        if horizon is not None:
            df = df[df["horizon_days"] == horizon]
        return df

    def load_predictions(self) -> pd.DataFrame:
        """Load model predictions."""
        pred_dir = self._resolve("predictions")
        files = list(pred_dir.glob("predictions_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in sorted(files)]
        return pd.concat(dfs, ignore_index=True)

    def load_allocations(self) -> pd.DataFrame:
        """Load portfolio allocations."""
        alloc_dir = self._resolve("allocations")
        files = list(alloc_dir.glob("allocations_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in sorted(files)]
        return pd.concat(dfs, ignore_index=True)

    def load_backtest_results(self) -> pd.DataFrame:
        """Load backtest summary results."""
        bt_dir = self._resolve("backtests")
        files = list(bt_dir.glob("backtest_summary_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in sorted(files)]
        return pd.concat(dfs, ignore_index=True)

    def get_market_coverage_summary(self) -> pd.DataFrame:
        """Compute market data coverage summary per symbol."""
        df = self.load_market_data()
        if df.empty:
            return pd.DataFrame()

        summary = (
            df.groupby("symbol")
            .agg(
                total_bars=("date_ts", "count"),
                first_date=("date_ts", "min"),
                last_date=("date_ts", "max"),
                null_close=("close", lambda x: x.isnull().sum()),
                zero_volume=("volume", lambda x: (x == 0).sum()),
            )
            .reset_index()
        )
        summary["coverage_days"] = (
            (summary["last_date"] - summary["first_date"]).dt.days + 1
        )
        summary["coverage_pct"] = (
            summary["total_bars"] / summary["coverage_days"].clip(lower=1)
        ).clip(upper=1.0)
        return summary

    def run_qa_report(self) -> Dict[str, pd.DataFrame]:
        """Run comprehensive QA across all data layers."""
        report = {}

        # Market QA
        market_qa_path = self._resolve("raw") / "market" / "qa_report.parquet"
        if market_qa_path.exists():
            report["market_qa"] = pd.read_parquet(market_qa_path)

        # On-chain coverage
        coverage_path = self._resolve("raw") / "onchain" / "coverage_report.parquet"
        if coverage_path.exists():
            report["onchain_coverage"] = pd.read_parquet(coverage_path)

        # Market coverage summary
        report["market_coverage"] = self.get_market_coverage_summary()

        return report

    def query(self, sql: str) -> pd.DataFrame:
        """Execute arbitrary SQL against the DuckDB connection."""
        conn = self._get_conn()
        return conn.execute(sql).df()

    def query_dataframe(self, df: "pd.DataFrame", sql: str) -> "pd.DataFrame":
        """
        Execute SQL against an in-memory DataFrame.
        The DataFrame is registered as 'df' in DuckDB's context.

        Parameters
        ----------
        df : pd.DataFrame
            Input DataFrame, registered as 'df' in the SQL query.
        sql : str
            SQL query referencing 'df'.

        Returns
        -------
        pd.DataFrame with query results.
        """
        import duckdb
        conn = duckdb.connect(":memory:")
        conn.register("df", df)
        return conn.execute(sql).df()
