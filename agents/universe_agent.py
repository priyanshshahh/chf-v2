"""
CHF UniverseAgent
Constructs monthly eligible universe snapshots using CoinGecko free API.
Handles stablecoin/wrapped exclusions, monthly snapshots, and eligibility reports.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from agents.base import AgentBase
from providers.coingecko import CoinGeckoProvider


class UniverseAgent(AgentBase):
    """
    Constructs the monthly eligible crypto universe.

    Pipeline position: First agent in the DAG.
    Outputs: data/raw/universe/universe_YYYYMM.parquet
             data/raw/universe/exclusions_YYYYMM.parquet
    """
¸
    def __init__(self, config: Optional[Dict[str, Any]] = None, snapshot_date: Optional[str] = None):
        super().__init__(config)
        self.snapshot_date = snapshot_date or datetime.now(timezone.utc).strftime("%Y-%m")
        self.provider = CoinGeckoProvider(
            rate_limit_sleep=1.5,
            max_retries=5,
        )
        self._raw_coins: List[Dict] = []
        self._eligible: List[Dict] = []
        self._excluded: List[Dict] = []

    def prepare(self) -> None:
        """Validate config and output paths."""
        ucfg = self.cfg.get("universe", {})
        assert ucfg.get("top_n", 0) > 0, "universe.top_n must be > 0"
        out_dir = self.get_path("raw") / "universe"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(
            f"UniverseAgent prepared | snapshot_date={self.snapshot_date} | "
            f"top_n={ucfg['top_n']}"
        )

    def run(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch and filter the universe.
        Returns dict with 'eligible' and 'excluded' DataFrames.
        """
        ucfg = self.cfg["universe"]
        top_n = ucfg["top_n"]
        min_vol = ucfg["min_daily_volume_usd"]
        stable_kw = ucfg["stablecoin_keywords"]
        wrapped_kw = ucfg["wrapped_keywords"]

        self.generate_snapshot_id(f"universe:{self.snapshot_date}")

        self.logger.info(f"Fetching CoinGecko top {top_n} coins...")
        all_coins = self.provider.build_universe_snapshot(
            top_n=top_n,
            min_volume_usd=min_vol,
            stablecoin_keywords=stable_kw,
            wrapped_keywords=wrapped_kw,
            snapshot_id=self.snapshot_id,
            run_id=self.run_id,
        )

        eligible = [c for c in all_coins if not c.get("is_excluded", False)]
        excluded = [c for c in all_coins if c.get("is_excluded", False)]

        self._eligible = eligible
        self._excluded = excluded

        self.logger.info(
            f"Universe built: {len(eligible)} eligible, {len(excluded)} excluded"
        )
        self.metrics["eligible_count"] = len(eligible)
        self.metrics["excluded_count"] = len(excluded)

        return {
            "eligible": pd.DataFrame(eligible),
            "excluded": pd.DataFrame(excluded),
        }

    def persist(self, result: Dict[str, pd.DataFrame]) -> None:
        """Save universe snapshots to Parquet."""
        out_dir = self.get_path("raw") / "universe"
        out_dir.mkdir(parents=True, exist_ok=True)

        month_str = self.snapshot_date.replace("-", "")

        # Eligible universe
        eligible_df = result["eligible"]
        if not eligible_df.empty:
            eligible_path = out_dir / f"universe_{month_str}.parquet"
            eligible_df.to_parquet(eligible_path, index=False)
            self.output_paths["eligible"] = str(eligible_path)
            self.logger.info(f"Saved eligible universe: {eligible_path}")

        # Excluded assets
        excluded_df = result["excluded"]
        if not excluded_df.empty:
            excluded_path = out_dir / f"exclusions_{month_str}.parquet"
            excluded_df.to_parquet(excluded_path, index=False)
            self.output_paths["excluded"] = str(excluded_path)

        # Snapshot metadata
        meta = {
            "snapshot_id": self.snapshot_id,
            "snapshot_date": self.snapshot_date,
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "eligible_count": len(eligible_df),
            "excluded_count": len(excluded_df),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "eligible_symbols": eligible_df["symbol"].tolist() if not eligible_df.empty else [],
        }
        meta_path = out_dir / f"snapshot_meta_{month_str}.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        self.output_paths["meta"] = str(meta_path)
        self.logger.info(f"Universe snapshot saved | snapshot_id={self.snapshot_id}")

    def get_eligible_symbols(self) -> List[str]:
        """Return list of eligible symbols from last run."""
        return [c["symbol"] for c in self._eligible]

    def load_latest_universe(self) -> pd.DataFrame:
        """Load the most recent universe snapshot from disk."""
        universe_dir = self.get_path("raw") / "universe"
        files = sorted(universe_dir.glob("universe_*.parquet"), reverse=True)
        if not files:
            return pd.DataFrame()
        return pd.read_parquet(files[0])

    def load_universe_history(self) -> pd.DataFrame:
        """Load all historical universe snapshots concatenated."""
        universe_dir = self.get_path("raw") / "universe"
        files = sorted(universe_dir.glob("universe_*.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in files]
        return pd.concat(dfs, ignore_index=True)
