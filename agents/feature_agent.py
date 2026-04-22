"""
CHF FeatureAgent (V1 + V2)
Builds the complete feature store from cleaned market and on-chain data.
Implements leakage-free as-of alignment, winsorization, and cross-sectional z-scoring.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from agents.base import AgentBase
from features.feature_engineering import (
    FEATURE_DICTIONARY,
    compute_active_address_growth,
    compute_atr_proxy,
    compute_cross_sectional_rank,
    compute_fee_intensity,
    compute_log_returns,
    compute_mvrv_proxy,
    compute_nvt_ratio,
    compute_nvt_signal,
    compute_realized_cap_change,
    compute_reversal,
    compute_rolling_beta,
    compute_rolling_skewness,
    compute_rolling_volatility,
    compute_tvl_growth,
    compute_tvl_ratio,
    compute_tx_count_growth,
    compute_volume_ratio,
    cross_sectional_zscore,
    winsorize_series,
    compute_correlation_clusters,
)

# Fix import name
from features.feature_engineering import cross_sectional_rank as compute_cross_sectional_rank


class FeatureAgentV1(AgentBase):
    """
    Builds market features from OHLCV data.
    Outputs: data/features/market_features.parquet
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._market_df: Optional[pd.DataFrame] = None
        self._btc_returns: Optional[pd.Series] = None

    def prepare(self) -> None:
        """Load cleaned market data."""
        cleaned_dir = self.get_path("cleaned")
        market_files = list(cleaned_dir.glob("*_ohlcv_clean.parquet"))

        # Fallback to raw if cleaned not available
        if not market_files:
            raw_dir = self.get_path("raw") / "market"
            market_files = list(raw_dir.glob("*_ohlcv.parquet"))

        if not market_files:
            raise FileNotFoundError("No OHLCV data found. Run MarketDataAgent first.")

        dfs = [pd.read_parquet(f) for f in market_files]
        self._market_df = pd.concat(dfs, ignore_index=True)
        self._market_df["date_ts"] = pd.to_datetime(self._market_df["date_ts"], utc=True)
        self._market_df = self._market_df.sort_values(["symbol", "date_ts"])

        # Extract BTC returns for beta computation
        btc_df = self._market_df[self._market_df["symbol"] == "BTC"].copy()
        if not btc_df.empty:
            btc_df = btc_df.set_index("date_ts")
            self._btc_returns = compute_log_returns(btc_df["close"], 1)
        else:
            self._btc_returns = None

        self.logger.info(
            f"FeatureAgentV1 prepared | {self._market_df['symbol'].nunique()} symbols | "
            f"{len(self._market_df)} rows"
        )

    def run(self) -> pd.DataFrame:
        """Compute all market features for all symbols."""
        fcfg = self.cfg.get("features", {})
        return_windows = fcfg.get("return_windows", [3, 7, 14, 30, 90])
        vol_window = fcfg.get("volatility_window", 30)
        beta_window = fcfg.get("beta_window", 60)
        skew_window = fcfg.get("skewness_window", 30)
        vol_windows = fcfg.get("volume_windows", [7, 14, 30])
        winsorize_lower = fcfg.get("winsorize_lower", 0.01)
        winsorize_upper = fcfg.get("winsorize_upper", 0.99)

        self.generate_snapshot_id("features_v1")
        all_feature_dfs = []

        for symbol, grp in self._market_df.groupby("symbol"):
            grp = grp.set_index("date_ts").sort_index()
            close = grp["close"]
            volume = grp["volume"]
            high = grp["high"]
            low = grp["low"]

            # Daily log returns
            daily_ret = compute_log_returns(close, 1)

            feat = pd.DataFrame(index=grp.index)
            feat["symbol"] = symbol

            # Log returns over multiple windows
            for w in return_windows:
                feat[f"ret_{w}d"] = compute_log_returns(close, w)

            # Volatility
            feat[f"vol_{vol_window}d"] = compute_rolling_volatility(daily_ret, vol_window)

            # Skewness
            feat[f"skew_{skew_window}d"] = compute_rolling_skewness(daily_ret, skew_window)

            # Beta to BTC
            if self._btc_returns is not None:
                feat[f"beta_btc_{beta_window}d"] = compute_rolling_beta(
                    daily_ret, self._btc_returns, beta_window
                )
            else:
                feat[f"beta_btc_{beta_window}d"] = np.nan

            # Volume ratios
            for w in vol_windows:
                feat[f"vol_ratio_{w}d"] = compute_volume_ratio(volume, w)

            # Reversal
            feat["reversal_3_30"] = compute_reversal(daily_ret, 3, 30)

            # ATR proxy
            feat["atr_14d"] = compute_atr_proxy(high, low, close, 14)

            feat = feat.reset_index()
            feat = feat.rename(columns={"index": "date_ts"})
            all_feature_dfs.append(feat)

        if not all_feature_dfs:
            return pd.DataFrame()

        result = pd.concat(all_feature_dfs, ignore_index=True)

        # Winsorize all numeric feature columns
        feature_cols = [
            c for c in result.columns
            if c not in ("symbol", "date_ts")
        ]
        for col in feature_cols:
            result[col] = winsorize_series(result[col], winsorize_lower, winsorize_upper)

        # Cross-sectional z-scores
        if fcfg.get("zscore_cross_sectional", True):
            for col in feature_cols:
                try:
                    result[f"{col}_cs"] = cross_sectional_zscore(result, col)
                except Exception:
                    pass

        result["feature_version"] = fcfg.get("feature_version", "v1")
        result["snapshot_id"] = self.snapshot_id
        result["run_id"] = self.run_id

        self.logger.info(
            f"FeatureAgentV1: computed {len(feature_cols)} features for "
            f"{result['symbol'].nunique()} symbols | {len(result)} rows"
        )
        self.metrics["feature_count"] = len(feature_cols)
        self.metrics["symbol_count"] = result["symbol"].nunique()
        return result

    def persist(self, result: pd.DataFrame) -> None:
        """Save feature store to Parquet."""
        features_dir = self.get_path("features")
        features_dir.mkdir(parents=True, exist_ok=True)

        path = features_dir / "market_features.parquet"
        result.to_parquet(path, index=False)
        self.output_paths["market_features"] = str(path)

        # Save feature dictionary
        dict_path = features_dir / "feature_dictionary.json"
        with open(dict_path, "w") as f:
            json.dump(FEATURE_DICTIONARY, f, indent=2)
        self.output_paths["feature_dictionary"] = str(dict_path)

        self.logger.info(f"Market features saved: {path}")


class FeatureAgentV2(AgentBase):
    """
    Builds on-chain features and merges with market features.
    Outputs: data/features/full_features.parquet
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._market_features: Optional[pd.DataFrame] = None
        self._onchain_df: Optional[pd.DataFrame] = None
        self._market_df: Optional[pd.DataFrame] = None

    def prepare(self) -> None:
        """Load market features and on-chain data."""
        features_dir = self.get_path("features")
        mf_path = features_dir / "market_features.parquet"
        if not mf_path.exists():
            raise FileNotFoundError("Market features not found. Run FeatureAgentV1 first.")
        self._market_features = pd.read_parquet(mf_path)
        self._market_features["date_ts"] = pd.to_datetime(
            self._market_features["date_ts"], utc=True
        )

        # Load on-chain data
        onchain_dir = self.get_path("raw") / "onchain"
        oc_files = list(onchain_dir.glob("*_onchain.parquet"))
        if oc_files:
            dfs = [pd.read_parquet(f) for f in oc_files]
            self._onchain_df = pd.concat(dfs, ignore_index=True)
            self._onchain_df["date_ts"] = pd.to_datetime(
                self._onchain_df["date_ts"], utc=True
            )
        else:
            self._onchain_df = pd.DataFrame()

        # Load market data for market cap proxy
        raw_dir = self.get_path("raw") / "market"
        mkt_files = list(raw_dir.glob("*_ohlcv.parquet"))
        if mkt_files:
            dfs = [pd.read_parquet(f) for f in mkt_files]
            self._market_df = pd.concat(dfs, ignore_index=True)
            self._market_df["date_ts"] = pd.to_datetime(
                self._market_df["date_ts"], utc=True
            )

        self.logger.info("FeatureAgentV2 prepared")

    def run(self) -> pd.DataFrame:
        """Compute on-chain features and merge with market features."""
        fcfg = self.cfg.get("features", {})
        winsorize_lower = fcfg.get("winsorize_lower", 0.01)
        winsorize_upper = fcfg.get("winsorize_upper", 0.99)

        self.generate_snapshot_id("features_v2")
        result = self._market_features.copy()

        if self._onchain_df is None or self._onchain_df.empty:
            self.logger.warning("No on-chain data available, skipping on-chain features")
            result["feature_version"] = "v2_market_only"
            result["snapshot_id"] = self.snapshot_id
            return result

        # Build on-chain features per symbol
        oc_feature_dfs = []
        for symbol, grp in self._onchain_df.groupby("symbol"):
            grp = grp.set_index("date_ts").sort_index()
            feat = pd.DataFrame(index=grp.index)
            feat["symbol"] = symbol

            # Active address growth
            if "AdrActCnt" in grp.columns:
                feat["adr_growth_30d"] = compute_active_address_growth(
                    grp["AdrActCnt"].ffill(), 30
                )

            # Transaction count growth
            if "TxCnt" in grp.columns:
                feat["tx_growth_30d"] = compute_tx_count_growth(
                    grp["TxCnt"].ffill(), 30
                )

            # NVT ratio
            if "TxTfrValAdjUSD" in grp.columns and self._market_df is not None:
                mkt_sym = self._market_df[self._market_df["symbol"] == symbol].set_index("date_ts")
                if not mkt_sym.empty:
                    # Market cap proxy: close * circulating supply not available, use close as proxy
                    # NVT = close_price_index / tx_volume (relative measure)
                    tx_vol = grp["TxTfrValAdjUSD"].reindex(feat.index).ffill()
                    close_price = mkt_sym["close"].reindex(feat.index).ffill()
                    feat["nvt_ratio"] = compute_nvt_ratio(close_price, tx_vol.clip(lower=1))
                    feat["nvt_signal_90d"] = compute_nvt_signal(feat["nvt_ratio"], 90)

            # MVRV proxy
            if "CapMVRVCur" in grp.columns:
                feat["mvrv_proxy"] = grp["CapMVRVCur"].reindex(feat.index).ffill()
            elif "CapRealUSD" in grp.columns and self._market_df is not None:
                mkt_sym = self._market_df[self._market_df["symbol"] == symbol].set_index("date_ts")
                if not mkt_sym.empty:
                    real_cap = grp["CapRealUSD"].reindex(feat.index).ffill()
                    close_price = mkt_sym["close"].reindex(feat.index).ffill()
                    feat["mvrv_proxy"] = compute_mvrv_proxy(close_price, real_cap.clip(lower=1))

            # Realized cap change
            if "CapRealUSD" in grp.columns:
                feat["realized_cap_change_30d"] = compute_realized_cap_change(
                    grp["CapRealUSD"].reindex(feat.index).ffill(), 30
                )

            # Fee intensity
            if "FeeTotUSD" in grp.columns and self._market_df is not None:
                mkt_sym = self._market_df[self._market_df["symbol"] == symbol].set_index("date_ts")
                if not mkt_sym.empty:
                    fees = grp["FeeTotUSD"].reindex(feat.index).fillna(0)
                    close_price = mkt_sym["close"].reindex(feat.index).ffill()
                    feat["fee_intensity"] = compute_fee_intensity(fees, close_price.clip(lower=1))

            # TVL features (from DeFiLlama)
            if "tvl_usd" in grp.columns and self._market_df is not None:
                mkt_sym = self._market_df[self._market_df["symbol"] == symbol].set_index("date_ts")
                if not mkt_sym.empty:
                    tvl = grp["tvl_usd"].reindex(feat.index).ffill()
                    close_price = mkt_sym["close"].reindex(feat.index).ffill()
                    feat["tvl_ratio"] = compute_tvl_ratio(tvl, close_price.clip(lower=1))
                    feat["tvl_growth_30d"] = compute_tvl_growth(tvl, 30)

            feat = feat.reset_index()
            oc_feature_dfs.append(feat)

        if oc_feature_dfs:
            oc_features = pd.concat(oc_feature_dfs, ignore_index=True)

            # Winsorize on-chain features
            oc_cols = [
                c for c in oc_features.columns
                if c not in ("symbol", "date_ts")
            ]
            for col in oc_cols:
                oc_features[col] = winsorize_series(
                    oc_features[col], winsorize_lower, winsorize_upper
                )

            # Merge with market features
            result = result.merge(
                oc_features,
                on=["symbol", "date_ts"],
                how="left",
            )

        # Redundancy pruning
        feature_cols = [
            c for c in result.columns
            if c not in ("symbol", "date_ts", "feature_version",
                         "snapshot_id", "run_id")
            and not c.endswith("_cs")
        ]
        feat_matrix = result[feature_cols].dropna(axis=1, thresh=len(result) // 2)
        if not feat_matrix.empty:
            keep_list, redundant_pairs = compute_correlation_clusters(
                feat_matrix,
                threshold=fcfg.get("correlation_threshold", 0.85),
            )
            self.logger.info(
                f"Redundancy pruning: keeping {len(keep_list)}/{len(feature_cols)} features, "
                f"{len(redundant_pairs)} redundant pairs"
            )
            self.metrics["features_kept"] = len(keep_list)
            self.metrics["redundant_pairs"] = len(redundant_pairs)

            # Save keep list
            features_dir = self.get_path("features")
            with open(features_dir / "feature_keep_list.json", "w") as f:
                json.dump({
                    "keep_list": keep_list,
                    "redundant_pairs": redundant_pairs,
                    "snapshot_id": self.snapshot_id,
                }, f, indent=2)

        result["feature_version"] = "v2"
        result["snapshot_id"] = self.snapshot_id
        result["run_id"] = self.run_id

        self.logger.info(
            f"FeatureAgentV2: {len(result.columns)} total columns | "
            f"{result['symbol'].nunique()} symbols | {len(result)} rows"
        )
        return result

    def persist(self, result: pd.DataFrame) -> None:
        """Save full feature store."""
        features_dir = self.get_path("features")
        features_dir.mkdir(parents=True, exist_ok=True)

        path = features_dir / "full_features.parquet"
        result.to_parquet(path, index=False)
        self.output_paths["full_features"] = str(path)
        self.logger.info(f"Full feature store saved: {path}")
