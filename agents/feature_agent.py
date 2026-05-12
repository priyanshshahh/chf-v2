from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb
import numpy as np
import pandas as pd

from agents.base import AgentBase
from features.feature_engineering import (
    METADATA_COLUMNS,
    build_feature_dictionary,
    check_for_prohibited_columns,
    cross_sectional_winsorize_by_date,
    cross_sectional_zscore_by_date,
    deterministic_correlation_prune,
    ensure_utc,
    infer_feature_group,
    iterative_vif_prune,
    rolling_beta_and_corr,
    rolling_downside_vol,
    rolling_zscore,
    safe_log_ratio,
)


MARKET_META_COLUMNS = [
    "date_ts",
    "symbol",
    "feature_set",
    "feature_version",
    "snapshot_id",
    "run_id",
    "created_at_utc",
]

ONCHAIN_META_COLUMNS = MARKET_META_COLUMNS + ["onchain_lag_days"]

FULL_META_COLUMNS = MARKET_META_COLUMNS + ["onchain_lag_days"]

ONCHAIN_RAW_METRICS = [
    "adr_active_count",
    "tx_count",
    "current_supply",
    "issuance_total_usd",
    "market_cap_usd",
    "mvrv_current",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "dex_volume_usd",
]

ONCHAIN_MISSINGNESS_METRICS = [
    "adr_active_count",
    "tx_count",
    "mvrv_current",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "dex_volume_usd",
]

VERIFIER_METADATA_EXEMPT = set(MARKET_META_COLUMNS + ["onchain_available", "coinmetrics_available", "defillama_available", "onchain_feature_count_non_null", "market_data_available", "market_history_days_available", "is_forward_filled_market"])

DIAGNOSTIC_FEATURE_COLUMNS = {
    "onchain_available",
    "coinmetrics_available",
    "defillama_available",
    "onchain_feature_count_non_null",
    "market_data_available",
    "market_history_days_available",
    "is_forward_filled_market",
    "onchain_lag_days",
}


class FeatureAgentError(RuntimeError):
    pass


class FeatureAgent(AgentBase):
    """Research-grade, leakage-safe feature generation using canonical upstream outputs."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.fcfg = self.cfg.get("features", {})
        self.output_dir = self._resolve_dir(self.fcfg.get("output_dir", "data/features"))
        self.market_input_path = self._resolve_dir(self.fcfg.get("input_market_path", "data/raw/market/market_ohlcv.parquet"))
        self.onchain_wide_input_path = self._resolve_dir(self.fcfg.get("input_onchain_wide_path", "data/raw/onchain/onchain_wide.parquet"))
        self.onchain_obs_input_path = self._resolve_dir(self.fcfg.get("input_onchain_observations_path", "data/raw/onchain/onchain_observations.parquet"))
        self.universe_input_path = self._resolve_dir(self.fcfg.get("input_universe_path", "data/raw/universe/universe_monthly.parquet"))
        self.market_manifest_path = self._resolve_dir(self.fcfg.get("input_market_manifest_path", "data/raw/market/market_manifest.json"))
        self.onchain_manifest_path = self._resolve_dir(self.fcfg.get("input_onchain_manifest_path", "data/raw/onchain/onchain_manifest.json"))
        self.market_df: pd.DataFrame | None = None
        self.onchain_wide_df: pd.DataFrame | None = None
        self.onchain_obs_df: pd.DataFrame | None = None
        self.market_manifest: Dict[str, Any] = {}
        self.onchain_manifest: Dict[str, Any] = {}
        self.universe_manifest: Dict[str, Any] = {}
        self.allowed_symbols: List[str] = []
        self.universe_snapshot_id: str = ""
        self.market_snapshot_id: str = ""
        self.onchain_snapshot_id: str = ""
        self.warnings: List[str] = []
        self.limitations: List[str] = []

    def _resolve_dir(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def _now_utc(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        required = [
            self.market_input_path,
            self.onchain_wide_input_path,
            self.onchain_obs_input_path,
            self.universe_input_path,
            self.market_manifest_path,
            self.onchain_manifest_path,
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing and self.fcfg.get("fail_on_missing_inputs", True):
            raise FileNotFoundError(f"Missing canonical feature inputs: {missing}")

        self.market_manifest = self._read_json(self.market_manifest_path)
        self.onchain_manifest = self._read_json(self.onchain_manifest_path)
        universe_manifest_path = self._resolve_dir("data/raw/universe/universe_manifest.json")
        if universe_manifest_path.exists():
            self.universe_manifest = self._read_json(universe_manifest_path)

        self.allowed_symbols, self.universe_snapshot_id = self._load_allowed_symbols()
        self.market_snapshot_id = str(self.market_manifest.get("snapshot_id", ""))
        self.onchain_snapshot_id = str(self.onchain_manifest.get("snapshot_id", ""))
        self.market_df = self._load_market()
        self.onchain_wide_df = self._load_onchain_wide()
        self.onchain_obs_df = self._load_onchain_observations()
        if self.market_df.empty:
            raise FeatureAgentError("Canonical market_ohlcv.parquet produced no usable rows")
        self.generate_snapshot_id(
            f"features:{self.universe_snapshot_id}:{self.market_snapshot_id}:{self.onchain_snapshot_id}:{len(self.market_df)}"
        )

    def _read_json(self, path: Path) -> Dict[str, Any]:
        with open(path, "r") as f:
            return json.load(f)

    def _read_parquet(self, path: Path, columns: Sequence[str] | None = None) -> pd.DataFrame:
        if self.fcfg.get("use_duckdb", True):
            con = duckdb.connect(database=":memory:")
            selected = "*" if columns is None else ", ".join(columns)
            return con.execute(f"SELECT {selected} FROM read_parquet('{path}')").df()
        return pd.read_parquet(path, columns=columns)

    def _load_allowed_symbols(self) -> Tuple[List[str], str]:
        universe = self._read_parquet(self.universe_input_path)
        universe["snapshot_date"] = ensure_utc(universe["snapshot_date"])
        membership_path = self._resolve_dir("data/raw/universe/universe_membership.parquet")
        survivor_only = self.universe_manifest.get("survivor_only_universe")
        if survivor_only is False:
            if membership_path.exists():
                self.limitations.append(
                    "Point-in-time universe membership file detected; FeatureAgent is prepared for historical membership filtering, "
                    "but current feature generation still materializes the configured canonical symbol/date panel."
                )
            else:
                self.limitations.append(
                    "Universe manifest reports non-survivor historical mode, but universe_membership.parquet is missing; "
                    "point-in-time membership filtering was not applied."
                )
        elif survivor_only is not False:
            self.limitations.append(
                "Feature generation is using latest eligible universe membership; results retain latest-survivor universe limitations."
            )
        latest_snapshot = universe["snapshot_date"].max()
        latest = universe[(universe["snapshot_date"] == latest_snapshot) & (universe["is_eligible"] == True)].copy()  # noqa: E712
        latest["symbol"] = latest["symbol"].astype(str).str.upper()
        latest = latest.sort_values(["market_cap_rank", "symbol"])
        max_symbols = self.fcfg.get("max_symbols")
        if max_symbols:
            latest = latest.head(int(max_symbols)).copy()
        symbols = latest["symbol"].tolist()
        if not symbols:
            raise FeatureAgentError("Universe intersection produced no symbols for feature generation")
        snapshot_id = str(latest["snapshot_id"].iloc[0]) if "snapshot_id" in latest.columns and not latest.empty else ""
        return symbols, snapshot_id

    def _load_market(self) -> pd.DataFrame:
        market = self._read_parquet(self.market_input_path)
        market["date_ts"] = ensure_utc(market["date_ts"])
        market["symbol"] = market["symbol"].astype(str).str.upper()
        market = market[market["symbol"].isin(self.allowed_symbols)].copy()
        if "is_full_ohlcv" in market.columns:
            market = market[market["is_full_ohlcv"] == True].copy()  # noqa: E712
        market = market.sort_values(["symbol", "date_ts"]).drop_duplicates(["symbol", "date_ts"], keep="last")
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            market[col] = pd.to_numeric(market[col], errors="coerce")
        market = market[(market["close"] > 0) & (market["open"] > 0) & (market["high"] > 0) & (market["low"] > 0)]
        return market.reset_index(drop=True)

    def _load_onchain_wide(self) -> pd.DataFrame:
        onchain = self._read_parquet(self.onchain_wide_input_path)
        onchain["date_ts"] = ensure_utc(onchain["date_ts"])
        onchain["symbol"] = onchain["symbol"].astype(str).str.upper()
        onchain = onchain[onchain["symbol"].isin(self.allowed_symbols)].copy()
        onchain = onchain.sort_values(["symbol", "date_ts"]).drop_duplicates(["symbol", "date_ts"], keep="last")
        return onchain.reset_index(drop=True)

    def _load_onchain_observations(self) -> pd.DataFrame:
        obs = self._read_parquet(self.onchain_obs_input_path)
        obs["date_ts"] = ensure_utc(obs["date_ts"])
        obs["symbol"] = obs["symbol"].astype(str).str.upper()
        obs = obs[obs["symbol"].isin(self.allowed_symbols)].copy()
        obs = obs.sort_values(["symbol", "date_ts", "source", "metric_name"]).drop_duplicates(
            ["symbol", "date_ts", "source", "metric_name"],
            keep="last",
        )
        return obs.reset_index(drop=True)

    def run(self) -> Dict[str, Any]:
        market_features = self._build_market_features(self.market_df.copy())
        onchain_features = self._build_onchain_features(self.market_df[["date_ts", "symbol"]].copy(), self.onchain_wide_df.copy(), self.onchain_obs_df.copy())
        full_features = market_features.merge(
            onchain_features.drop(columns=["feature_set", "feature_version", "snapshot_id", "run_id", "created_at_utc"], errors="ignore"),
            on=["date_ts", "symbol"],
            how="left",
            validate="one_to_one",
        )
        full_features["feature_set"] = "full"
        full_features["feature_version"] = self.fcfg.get("feature_versions", {}).get("full", "full_v1")
        full_features["snapshot_id"] = self.snapshot_id
        full_features["run_id"] = self.run_id
        full_features["created_at_utc"] = self._now_utc().isoformat()

        market_features = self._drop_all_null_features(market_features, "market")
        onchain_features = self._drop_all_null_features(onchain_features, "onchain")
        full_features = self._drop_all_null_features(full_features, "full")

        self._validate_feature_frame(market_features, "market_features")
        self._validate_feature_frame(onchain_features, "onchain_features")
        self._validate_feature_frame(full_features, "full_features")

        coverage = pd.concat(
            [
                self._coverage_for_set(market_features, "market"),
                self._coverage_for_set(onchain_features, "onchain"),
                self._coverage_for_set(full_features, "full"),
            ],
            ignore_index=True,
        )
        if coverage.empty:
            raise FeatureAgentError("feature_coverage_report would be empty")

        keep_info, pruned_full = self._prune_full_features(full_features)
        dictionary = build_feature_dictionary(
            self._feature_columns(full_features),
            keep_info["kept_features"],
        )

        self.metrics.update(
            {
                "market_rows": int(len(market_features)),
                "onchain_rows": int(len(onchain_features)),
                "full_rows": int(len(full_features)),
                "market_symbols": int(market_features["symbol"].nunique()),
                "onchain_symbols": int(onchain_features.loc[onchain_features["onchain_available"] == True, "symbol"].nunique()),  # noqa: E712
                "full_symbols": int(full_features["symbol"].nunique()),
                "market_feature_count": int(len(self._feature_columns(market_features))),
                "onchain_feature_count": int(len(self._feature_columns(onchain_features))),
                "full_feature_count": int(len(self._feature_columns(full_features))),
                "final_kept_feature_count": int(len(keep_info["kept_features"])),
            }
        )
        fatal_errors = self._fatal_errors(market_features, onchain_features, full_features, coverage)
        return {
            "market_features": market_features,
            "onchain_features": onchain_features,
            "full_features": full_features,
            "full_features_pruned": pruned_full,
            "coverage": coverage,
            "dictionary": dictionary,
            "keep_info": keep_info,
            "fatal_errors": fatal_errors,
        }

    def _build_market_features(self, market: pd.DataFrame) -> pd.DataFrame:
        windows = self.fcfg.get("market_windows", {})
        market = market.sort_values(["symbol", "date_ts"]).copy()
        market["log_ret_1d"] = np.nan
        all_rows: List[pd.DataFrame] = []
        btc_benchmark = (
            market.loc[market["symbol"] == "BTC", ["date_ts", "close"]]
            .drop_duplicates("date_ts")
            .sort_values("date_ts")
        )
        btc_benchmark["btc_log_ret_1d"] = safe_log_ratio(btc_benchmark["close"], btc_benchmark["close"].shift(1))
        btc_ret = btc_benchmark.set_index("date_ts")["btc_log_ret_1d"]

        for symbol, grp in market.groupby("symbol", sort=False):
            grp = grp.sort_values("date_ts").copy()
            close = grp["close"]
            open_ = grp["open"]
            high = grp["high"]
            low = grp["low"]
            volume = grp["volume"]
            grp["log_ret_1d"] = safe_log_ratio(close, close.shift(1))
            for window in windows.get("returns", [1, 3, 7, 14, 30, 60, 90]):
                grp[f"log_ret_{window}d"] = safe_log_ratio(close, close.shift(window))
            grp["momentum_7_30"] = grp["log_ret_7d"] - grp["log_ret_30d"]
            grp["momentum_14_90"] = grp["log_ret_14d"] - grp["log_ret_90d"]
            for window in windows.get("volatility", [7, 14, 30, 60]):
                grp[f"realized_vol_{window}d"] = grp["log_ret_1d"].rolling(window=window, min_periods=max(window // 2, 5)).std() * np.sqrt(365)
            grp["skew_30d"] = grp["log_ret_1d"].rolling(window=30, min_periods=15).skew()
            grp["downside_vol_30d"] = rolling_downside_vol(grp["log_ret_1d"], 30)
            sma14 = close.rolling(window=14, min_periods=7).mean()
            sma30 = close.rolling(window=30, min_periods=15).mean()
            grp["reversal_3_30"] = grp["log_ret_3d"] - grp["log_ret_30d"]
            grp["price_sma_gap_14d"] = (close / sma14) - 1
            grp["price_sma_gap_30d"] = (close / sma30) - 1
            grp["zscore_close_30d"] = rolling_zscore(close, 30, min_periods=15)
            grp["dollar_volume"] = close * volume
            grp["log_dollar_volume"] = np.log(grp["dollar_volume"].where(grp["dollar_volume"] > 0))
            vol_mean_7 = volume.rolling(window=7, min_periods=4).mean()
            vol_mean_30 = volume.rolling(window=30, min_periods=15).mean()
            dol_mean_30 = grp["dollar_volume"].rolling(window=30, min_periods=15).mean()
            grp["volume_ratio_7d"] = volume / vol_mean_7.replace(0, np.nan)
            grp["volume_ratio_30d"] = volume / vol_mean_30.replace(0, np.nan)
            grp["dollar_volume_ratio_30d"] = grp["dollar_volume"] / dol_mean_30.replace(0, np.nan)
            grp["volume_zscore_30d"] = rolling_zscore(volume, 30, min_periods=15)
            grp["hl_range_pct"] = (high - low) / close.replace(0, np.nan)
            prev_close = close.shift(1)
            true_range = pd.concat(
                [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            grp["atr_proxy_14d"] = (true_range / close.replace(0, np.nan)).rolling(window=14, min_periods=7).mean()
            rolling_high_30 = close.rolling(window=30, min_periods=15).max()
            rolling_high_90 = close.rolling(window=90, min_periods=45).max()
            grp["drawdown_30d"] = (close / rolling_high_30) - 1
            grp["drawdown_90d"] = (close / rolling_high_90) - 1
            grp["distance_from_30d_high"] = (rolling_high_30 - close) / rolling_high_30.replace(0, np.nan)
            grp["distance_from_90d_high"] = (rolling_high_90 - close) / rolling_high_90.replace(0, np.nan)
            beta, corr = rolling_beta_and_corr(
                grp.set_index("date_ts")["log_ret_1d"],
                btc_ret,
                window=60,
                min_periods=30,
            )
            grp["beta_btc_60d"] = beta.values
            grp["corr_btc_60d"] = corr.values
            grp["is_forward_filled_market"] = grp.get("is_forward_filled", False).astype(bool)
            grp["market_data_available"] = True
            grp["market_history_days_available"] = np.arange(1, len(grp) + 1)
            all_rows.append(grp)

        market_features = pd.concat(all_rows, ignore_index=True)
        market_feature_cols = [
            "log_ret_1d", "log_ret_3d", "log_ret_7d", "log_ret_14d", "log_ret_30d", "log_ret_60d", "log_ret_90d",
            "momentum_7_30", "momentum_14_90",
            "realized_vol_7d", "realized_vol_14d", "realized_vol_30d", "realized_vol_60d",
            "skew_30d", "downside_vol_30d",
            "reversal_3_30", "price_sma_gap_14d", "price_sma_gap_30d", "zscore_close_30d",
            "dollar_volume", "log_dollar_volume", "volume_ratio_7d", "volume_ratio_30d", "dollar_volume_ratio_30d", "volume_zscore_30d",
            "hl_range_pct", "atr_proxy_14d",
            "drawdown_30d", "drawdown_90d", "distance_from_30d_high", "distance_from_90d_high",
            "beta_btc_60d", "corr_btc_60d",
            "is_forward_filled_market", "market_data_available", "market_history_days_available",
        ]
        market_features = market_features[["date_ts", "symbol"] + [c for c in market_feature_cols if c in market_features.columns]].copy()
        market_features["feature_set"] = "market"
        market_features["feature_version"] = self.fcfg.get("feature_versions", {}).get("market", "market_v1")
        market_features["snapshot_id"] = self.snapshot_id
        market_features["run_id"] = self.run_id
        market_features["created_at_utc"] = self._now_utc().isoformat()

        feature_cols = self._feature_columns(market_features)
        market_features = self._replace_inf(market_features, feature_cols)
        market_features = self._apply_cross_sectional_post_processing(market_features, feature_cols)
        return market_features

    def _build_onchain_features(self, market_backbone: pd.DataFrame, onchain_wide: pd.DataFrame, onchain_obs: pd.DataFrame) -> pd.DataFrame:
        lag_days = int(self.fcfg.get("onchain_lag_days", 1))
        base = onchain_wide.sort_values(["symbol", "date_ts"]).copy()
        source_flags = (
            onchain_obs.groupby(["symbol", "date_ts", "source"]).size().rename("n").reset_index()
            if not onchain_obs.empty else pd.DataFrame(columns=["symbol", "date_ts", "source", "n"])
        )
        if not source_flags.empty:
            source_flags["present"] = True
            source_flags = (
                source_flags.pivot_table(index=["symbol", "date_ts"], columns="source", values="present", aggfunc="max")
                .reset_index()
                .rename_axis(None, axis=1)
            )
        else:
            source_flags = pd.DataFrame(columns=["symbol", "date_ts", "coinmetrics", "defillama"])
        base = base.merge(source_flags, on=["symbol", "date_ts"], how="left")
        base["coinmetrics_available"] = self._series_or_false(base, "coinmetrics")
        base["defillama_available"] = self._series_or_false(base, "defillama")
        for col in ["coinmetrics", "defillama"]:
            if col in base.columns:
                base = base.drop(columns=[col])
        for metric in ONCHAIN_RAW_METRICS:
            if metric in base.columns:
                base[metric] = pd.to_numeric(base[metric], errors="coerce")
            else:
                base[metric] = np.nan
        base["onchain_available"] = base[ONCHAIN_RAW_METRICS].notna().any(axis=1)
        base["onchain_feature_count_non_null"] = base[ONCHAIN_RAW_METRICS].notna().sum(axis=1)
        for metric in ONCHAIN_MISSINGNESS_METRICS:
            base[f"missing_{metric}"] = base[metric].isna().astype(int)
        base["log_adr_active_count"] = np.log(base["adr_active_count"].where(base["adr_active_count"] > 0))
        base["adr_active_growth_7d"] = base.groupby("symbol")["adr_active_count"].transform(lambda s: safe_log_ratio(s, s.shift(7)))
        base["adr_active_growth_30d"] = base.groupby("symbol")["adr_active_count"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["tx_count_growth_7d"] = base.groupby("symbol")["tx_count"].transform(lambda s: safe_log_ratio(s, s.shift(7)))
        base["tx_count_growth_30d"] = base.groupby("symbol")["tx_count"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["tx_count_zscore_30d"] = base.groupby("symbol")["tx_count"].transform(lambda s: rolling_zscore(s, 30, min_periods=15))
        base["mvrv_change_30d"] = base.groupby("symbol")["mvrv_current"].transform(lambda s: (s / s.shift(30)) - 1)
        base["mvrv_zscore_90d"] = base.groupby("symbol")["mvrv_current"].transform(lambda s: rolling_zscore(s, 90, min_periods=45))
        base["realized_cap_proxy"] = np.where(base["mvrv_current"] > 0, base["market_cap_usd"] / base["mvrv_current"], np.nan)
        tx_mean_30 = base.groupby("symbol")["tx_count"].transform(lambda s: s.rolling(window=30, min_periods=15).mean())
        dex_mean_30 = base.groupby("symbol")["dex_volume_usd"].transform(lambda s: s.rolling(window=30, min_periods=15).mean())
        base["nvt_tx_proxy"] = base["market_cap_usd"] / tx_mean_30.replace(0, np.nan)
        base["nvt_dex_proxy"] = base["market_cap_usd"] / dex_mean_30.replace(0, np.nan)
        base["chain_tvl_growth_7d"] = base.groupby("symbol")["chain_tvl_usd"].transform(lambda s: safe_log_ratio(s, s.shift(7)))
        base["chain_tvl_growth_30d"] = base.groupby("symbol")["chain_tvl_usd"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["protocol_tvl_growth_30d"] = base.groupby("symbol")["protocol_tvl_usd"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["fees_growth_30d"] = base.groupby("symbol")["fees_usd"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["dex_volume_growth_30d"] = base.groupby("symbol")["dex_volume_usd"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["fees_to_tvl"] = base["fees_usd"] / base["protocol_tvl_usd"].replace(0, np.nan)
        base["dex_volume_to_tvl"] = base["dex_volume_usd"] / base["protocol_tvl_usd"].replace(0, np.nan)
        base["supply_growth_30d"] = base.groupby("symbol")["current_supply"].transform(lambda s: safe_log_ratio(s, s.shift(30)))
        base["issuance_to_market_cap"] = base["issuance_total_usd"] / base["market_cap_usd"].replace(0, np.nan)
        base["market_cap_growth_30d"] = base.groupby("symbol")["market_cap_usd"].transform(lambda s: safe_log_ratio(s, s.shift(30)))

        feature_cols = [c for c in base.columns if c not in {"date_ts", "symbol", "snapshot_id", "fetched_at_utc"}]
        shift_cols = [c for c in feature_cols if c not in {"coinmetrics_available", "defillama_available", "onchain_available"}]
        base = base.sort_values(["symbol", "date_ts"])
        base[shift_cols] = base.groupby("symbol")[shift_cols].shift(lag_days)
        base["coinmetrics_available"] = base.groupby("symbol")["coinmetrics_available"].shift(lag_days).fillna(False).astype(bool)
        base["defillama_available"] = base.groupby("symbol")["defillama_available"].shift(lag_days).fillna(False).astype(bool)
        base["onchain_available"] = base.groupby("symbol")["onchain_available"].shift(lag_days).fillna(False).astype(bool)
        base["onchain_feature_count_non_null"] = pd.to_numeric(base["onchain_feature_count_non_null"], errors="coerce")
        base["onchain_lag_days"] = lag_days

        joined = market_backbone.sort_values(["symbol", "date_ts"]).merge(
            base.drop(columns=["snapshot_id", "fetched_at_utc"], errors="ignore"),
            on=["symbol", "date_ts"],
            how="left",
            validate="one_to_one",
        )
        joined["onchain_available"] = self._series_or_false(joined, "onchain_available")
        joined["coinmetrics_available"] = self._series_or_false(joined, "coinmetrics_available")
        joined["defillama_available"] = self._series_or_false(joined, "defillama_available")
        joined["onchain_feature_count_non_null"] = pd.to_numeric(joined.get("onchain_feature_count_non_null"), errors="coerce").fillna(0)
        joined["onchain_lag_days"] = lag_days
        onchain_feature_cols = [
            "adr_active_count", "tx_count", "current_supply", "issuance_total_usd", "market_cap_usd", "mvrv_current",
            "chain_tvl_usd", "protocol_tvl_usd", "fees_usd", "dex_volume_usd",
            "onchain_available", "coinmetrics_available", "defillama_available",
            "missing_adr_active_count", "missing_tx_count", "missing_mvrv_current", "missing_chain_tvl_usd",
            "missing_protocol_tvl_usd", "missing_fees_usd", "missing_dex_volume_usd",
            "onchain_feature_count_non_null", "log_adr_active_count", "adr_active_growth_7d", "adr_active_growth_30d",
            "tx_count_growth_7d", "tx_count_growth_30d", "tx_count_zscore_30d", "mvrv_change_30d", "mvrv_zscore_90d",
            "realized_cap_proxy", "nvt_tx_proxy", "nvt_dex_proxy", "chain_tvl_growth_7d", "chain_tvl_growth_30d",
            "protocol_tvl_growth_30d", "fees_growth_30d", "dex_volume_growth_30d", "fees_to_tvl", "dex_volume_to_tvl",
            "supply_growth_30d", "issuance_to_market_cap", "market_cap_growth_30d", "onchain_lag_days",
        ]
        joined = joined[["date_ts", "symbol"] + [c for c in onchain_feature_cols if c in joined.columns]].copy()
        joined["feature_set"] = "onchain"
        joined["feature_version"] = self.fcfg.get("feature_versions", {}).get("onchain", "onchain_v1")
        joined["snapshot_id"] = self.snapshot_id
        joined["run_id"] = self.run_id
        joined["created_at_utc"] = self._now_utc().isoformat()

        feature_cols = self._feature_columns(joined)
        joined = self._replace_inf(joined, feature_cols)
        joined = self._apply_cross_sectional_post_processing(joined, feature_cols)
        return joined

    def _apply_cross_sectional_post_processing(self, df: pd.DataFrame, feature_cols: Sequence[str]) -> pd.DataFrame:
        winsor_cfg = self.fcfg.get("winsorization", {})
        excluded_prefixes = ("missing_",)
        excluded_exact = {
            "is_forward_filled_market",
            "market_data_available",
            "market_history_days_available",
            "onchain_available",
            "coinmetrics_available",
            "defillama_available",
            "onchain_feature_count_non_null",
            "onchain_lag_days",
        }
        numeric_feature_cols = [
            c for c in feature_cols
            if c in df.columns
            and pd.api.types.is_numeric_dtype(df[c])
            and not pd.api.types.is_bool_dtype(df[c])
            and c not in excluded_exact
            and not c.startswith(excluded_prefixes)
        ]
        raw_numeric = [c for c in numeric_feature_cols if not c.endswith("_cs_z")]
        if winsor_cfg.get("enabled", True):
            df = cross_sectional_winsorize_by_date(
                df,
                raw_numeric,
                float(winsor_cfg.get("lower_quantile", 0.01)),
                float(winsor_cfg.get("upper_quantile", 0.99)),
            )
        z_cfg = self.fcfg.get("cross_sectional_zscore", {})
        if z_cfg.get("enabled", True):
            df = cross_sectional_zscore_by_date(
                df,
                raw_numeric,
                int(z_cfg.get("min_assets_per_date", 10)),
            )
        return df

    def _replace_inf(self, df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
        out = df.copy()
        out[list(cols)] = out[list(cols)].replace([np.inf, -np.inf], np.nan)
        return out

    def _drop_all_null_features(self, df: pd.DataFrame, label: str) -> pd.DataFrame:
        feature_cols = self._feature_columns(df)
        drop_cols = [c for c in feature_cols if df[c].isna().all()]
        if drop_cols:
            self.warnings.append(f"{label}: dropped all-null features {drop_cols}")
            df = df.drop(columns=drop_cols)
        return df

    def _feature_columns(self, df: pd.DataFrame) -> List[str]:
        return [
            c for c in df.columns
            if c not in METADATA_COLUMNS and c not in {"date_ts", "symbol", "feature_set", "feature_version", "snapshot_id", "run_id", "created_at_utc"}
        ]

    def _model_feature_columns(self, df: pd.DataFrame) -> List[str]:
        cols = self._feature_columns(df)
        if self.fcfg.get("allow_diagnostic_model_features", False):
            return cols
        return [c for c in cols if c not in DIAGNOSTIC_FEATURE_COLUMNS]

    def _series_or_false(self, df: pd.DataFrame, col: str) -> pd.Series:
        if col in df.columns:
            return df[col].fillna(False).astype(bool)
        return pd.Series(False, index=df.index, dtype=bool)

    def _validate_feature_frame(self, df: pd.DataFrame, label: str) -> None:
        df["date_ts"] = ensure_utc(df["date_ts"])
        if df.duplicated(["symbol", "date_ts"]).any() and self.fcfg.get("fail_on_duplicate_symbol_date", True):
            raise FeatureAgentError(f"{label} has duplicate symbol + date_ts rows")
        bad_cols = check_for_prohibited_columns(df.columns)
        if bad_cols and self.fcfg.get("fail_on_target_leakage", True):
            raise FeatureAgentError(f"{label} contains prohibited columns: {bad_cols}")
        numeric_cols = [c for c in self._feature_columns(df) if pd.api.types.is_numeric_dtype(df[c])]
        if numeric_cols:
            inf_mask = np.isinf(df[numeric_cols].to_numpy(dtype="float64", copy=True))
            if inf_mask.any():
                raise FeatureAgentError(f"{label} contains infinite numeric values")

    def _coverage_for_set(self, df: pd.DataFrame, feature_set: str) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        feature_cols = self._feature_columns(df)
        for col in feature_cols:
            series = pd.to_numeric(df[col], errors="coerce") if pd.api.types.is_numeric_dtype(df[col]) else df[col]
            numeric = pd.to_numeric(series, errors="coerce") if not pd.api.types.is_numeric_dtype(series) else series.astype("float64")
            null_mask = numeric.isna()
            finite_mask = np.isfinite(numeric.to_numpy(dtype="float64", na_value=np.nan))
            valid = numeric[~null_mask]
            rows.append(
                {
                    "feature_name": col,
                    "feature_group": infer_feature_group(col),
                    "feature_set": feature_set,
                    "non_null_count": int((~null_mask).sum()),
                    "null_count": int(null_mask.sum()),
                    "null_pct": float(null_mask.mean()),
                    "finite_count": int(np.nansum(finite_mask)),
                    "infinite_count": int((~np.isnan(numeric.to_numpy(dtype="float64", na_value=np.nan)) & ~finite_mask).sum()),
                    "symbols_with_non_null": int(df.loc[~null_mask, "symbol"].nunique()) if "symbol" in df.columns else 0,
                    "first_valid_date": df.loc[~null_mask, "date_ts"].min().isoformat() if (~null_mask).any() else None,
                    "last_valid_date": df.loc[~null_mask, "date_ts"].max().isoformat() if (~null_mask).any() else None,
                    "mean": float(valid.mean()) if len(valid) else None,
                    "std": float(valid.std()) if len(valid) else None,
                    "min": float(valid.min()) if len(valid) else None,
                    "p01": float(valid.quantile(0.01)) if len(valid) else None,
                    "p50": float(valid.quantile(0.50)) if len(valid) else None,
                    "p99": float(valid.quantile(0.99)) if len(valid) else None,
                    "max": float(valid.max()) if len(valid) else None,
                    "passed_qa": not numeric.isna().all(),
                    "failure_reason": "" if not numeric.isna().all() else "all_null_feature",
                }
            )
        return pd.DataFrame(rows)

    def _prune_full_features(self, full_features: pd.DataFrame) -> Tuple[Dict[str, Any], pd.DataFrame]:
        pruning = self.fcfg.get("pruning", {})
        feature_cols = self._model_feature_columns(full_features)
        numeric_candidates = [
            c for c in feature_cols
            if pd.api.types.is_numeric_dtype(full_features[c]) and full_features[c].notna().mean() > 0
        ]
        max_null_pct = float(self.fcfg.get("missingness", {}).get("max_null_pct_feature", 0.95))
        numeric_candidates = [
            c for c in numeric_candidates
            if full_features[c].isna().mean() <= max_null_pct
        ]
        dropped: List[Dict[str, Any]] = []
        kept = list(numeric_candidates)
        if pruning.get("enabled", True) and kept:
            kept, corr_dropped = deterministic_correlation_prune(
                full_features[kept],
                kept,
                float(pruning.get("correlation_threshold", 0.85)),
                int(pruning.get("max_final_features", 60)),
                int(pruning.get("min_final_features", 20)),
            )
            dropped.extend(corr_dropped)
            if pruning.get("vif_enabled", True) and kept:
                kept, vif_dropped = iterative_vif_prune(
                    full_features[kept],
                    kept,
                    float(pruning.get("vif_threshold", 10.0)),
                    int(pruning.get("min_final_features", 20)),
                )
                dropped.extend(vif_dropped)

        pruned_cols = ["date_ts", "symbol"] + kept + ["feature_set", "feature_version", "snapshot_id", "run_id", "created_at_utc"]
        if "onchain_available" in full_features.columns and "onchain_available" not in pruned_cols:
            pruned_cols.insert(2, "onchain_available")
        if "onchain_lag_days" in full_features.columns and "onchain_lag_days" not in pruned_cols:
            pruned_cols.append("onchain_lag_days")
        pruned = full_features[pruned_cols].copy()
        keep_info = {
            "all_candidate_features": numeric_candidates,
            "kept_features": kept,
            "dropped_features": [row["feature"] for row in dropped],
            "dropped_reason": dropped,
            "correlation_threshold": float(pruning.get("correlation_threshold", 0.85)),
            "vif_threshold": float(pruning.get("vif_threshold", 10.0)),
            "snapshot_id": self.snapshot_id,
        }
        return keep_info, pruned

    def _fatal_errors(
        self,
        market_features: pd.DataFrame,
        onchain_features: pd.DataFrame,
        full_features: pd.DataFrame,
        coverage: pd.DataFrame,
    ) -> List[str]:
        errors: List[str] = []
        if market_features.empty and self.fcfg.get("fail_on_empty_output", True):
            errors.append("market_features_empty")
        if full_features.empty and self.fcfg.get("fail_on_empty_output", True):
            errors.append("full_features_empty")
        if coverage.empty:
            errors.append("feature_coverage_report_empty")

        market_symbols = int(market_features["symbol"].nunique()) if not market_features.empty else 0
        onchain_symbols = int(onchain_features.loc[onchain_features["onchain_available"] == True, "symbol"].nunique()) if not onchain_features.empty else 0  # noqa: E712
        full_symbols = int(full_features["symbol"].nunique()) if not full_features.empty else 0
        full_rows = int(len(full_features))

        if market_symbols < int(self.fcfg.get("min_market_symbols_required", 90)) and self.fcfg.get("fail_on_low_feature_coverage", True):
            errors.append(f"market_symbols_below_minimum:{market_symbols}<{int(self.fcfg.get('min_market_symbols_required', 90))}")
        if onchain_symbols < int(self.fcfg.get("min_onchain_symbols_required", 40)) and self.fcfg.get("fail_on_low_feature_coverage", True):
            errors.append(f"onchain_symbols_below_minimum:{onchain_symbols}<{int(self.fcfg.get('min_onchain_symbols_required', 40))}")
        if full_symbols < int(self.fcfg.get("min_full_feature_symbols_required", 90)) and self.fcfg.get("fail_on_low_feature_coverage", True):
            errors.append(f"full_symbols_below_minimum:{full_symbols}<{int(self.fcfg.get('min_full_feature_symbols_required', 90))}")
        if full_rows < int(self.fcfg.get("min_rows_required", 50000)) and self.fcfg.get("fail_on_low_feature_coverage", True):
            errors.append(f"full_rows_below_minimum:{full_rows}<{int(self.fcfg.get('min_rows_required', 50000))}")
        if self.fcfg.get("fail_on_all_null_feature", True):
            all_null = coverage[coverage["passed_qa"] == False]  # noqa: E712
            if not all_null.empty:
                errors.append(f"all_null_features_present:{sorted(all_null['feature_name'].tolist())[:10]}")
        return errors

    def persist(self, result: Dict[str, Any]) -> None:
        market_features = result["market_features"].copy()
        onchain_features = result["onchain_features"].copy()
        full_features = result["full_features"].copy()
        full_features_pruned = result["full_features_pruned"].copy()
        coverage = result["coverage"].copy()
        dictionary = result["dictionary"]
        keep_info = result["keep_info"]
        fatal_errors = list(result.get("fatal_errors", []))

        market_path = self.output_dir / "market_features.parquet"
        onchain_path = self.output_dir / "onchain_features.parquet"
        full_path = self.output_dir / "full_features.parquet"
        pruned_path = self.output_dir / "full_features_pruned.parquet"
        coverage_path = self.output_dir / "feature_coverage_report.parquet"
        manifest_path = self.output_dir / "feature_manifest.json"
        dictionary_path = self.output_dir / "feature_dictionary.json"
        keep_list_path = self.output_dir / "feature_keep_list.json"
        quality_path = self.output_dir / "data_quality_features.md"
        partition_root = self.output_dir / "partitioned"

        if fatal_errors:
            self._write_quality_report(quality_path, market_features, onchain_features, full_features, coverage, keep_info, fatal_errors)
            self.output_paths.update({"data_quality_report": str(quality_path)})
            raise FeatureAgentError("; ".join(fatal_errors))

        market_features.to_parquet(market_path, index=False)
        onchain_features.to_parquet(onchain_path, index=False)
        full_features.to_parquet(full_path, index=False)
        if self.fcfg.get("pruning", {}).get("enabled", True):
            full_features_pruned.to_parquet(pruned_path, index=False)
        coverage.to_parquet(coverage_path, index=False)
        with open(dictionary_path, "w") as f:
            json.dump(dictionary, f, indent=2)
        with open(keep_list_path, "w") as f:
            json.dump(keep_info, f, indent=2)
        if partition_root.exists():
            shutil.rmtree(partition_root)
        self._write_partitioned(partition_root, market_features, full_features)
        self._write_quality_report(quality_path, market_features, onchain_features, full_features, coverage, keep_info, fatal_errors)

        manifest = {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": self._now_utc().isoformat(),
            "input_files": {
                "market": str(self.market_input_path),
                "onchain_wide": str(self.onchain_wide_input_path),
                "onchain_observations": str(self.onchain_obs_input_path),
                "universe": str(self.universe_input_path),
                "market_manifest": str(self.market_manifest_path),
                "onchain_manifest": str(self.onchain_manifest_path),
            },
            "input_manifest_summaries": {
                "market": {
                    "snapshot_id": self.market_manifest.get("snapshot_id"),
                    "requested_assets": self.market_manifest.get("requested_assets"),
                    "fetched_assets": self.market_manifest.get("fetched_assets"),
                    "full_ohlcv_assets": self.market_manifest.get("full_ohlcv_assets"),
                },
                "onchain": {
                    "snapshot_id": self.onchain_manifest.get("snapshot_id"),
                    "requested_assets": self.onchain_manifest.get("requested_assets"),
                    "assets_with_any_onchain": self.onchain_manifest.get("assets_with_any_onchain"),
                    "assets_with_defillama": self.onchain_manifest.get("assets_with_defillama"),
                },
                "universe": {
                    "snapshot_hashes": self.universe_manifest.get("snapshot_hashes"),
                    "monthly_snapshot_count": self.universe_manifest.get("monthly_snapshot_count"),
                },
            },
            "output_files": {
                "market_features": str(market_path),
                "onchain_features": str(onchain_path),
                "full_features": str(full_path),
                "full_features_pruned": str(pruned_path) if pruned_path.exists() else None,
                "feature_coverage_report": str(coverage_path),
                "feature_manifest": str(manifest_path),
                "feature_dictionary": str(dictionary_path),
                "feature_keep_list": str(keep_list_path),
                "data_quality_report": str(quality_path),
                "partitioned": str(partition_root),
            },
            "market_rows": int(len(market_features)),
            "onchain_rows": int(len(onchain_features)),
            "full_rows": int(len(full_features)),
            "market_symbols": int(market_features["symbol"].nunique()),
            "onchain_symbols": int(onchain_features.loc[onchain_features["onchain_available"] == True, "symbol"].nunique()),  # noqa: E712
            "full_symbols": int(full_features["symbol"].nunique()),
            "market_feature_count": int(len(self._feature_columns(market_features))),
            "onchain_feature_count": int(len(self._feature_columns(onchain_features))),
            "full_feature_count": int(len(self._feature_columns(full_features))),
            "final_kept_feature_count": int(len(keep_info["kept_features"])),
            "winsorization_config": self.fcfg.get("winsorization", {}),
            "zscore_config": self.fcfg.get("cross_sectional_zscore", {}),
            "pruning_config": self.fcfg.get("pruning", {}),
            "onchain_lag_days": int(self.fcfg.get("onchain_lag_days", 1)),
            "feature_versions": self.fcfg.get("feature_versions", {}),
            "coverage_summary": {
                "market_non_null_features": int((coverage["feature_set"] == "market").sum()),
                "onchain_non_null_features": int((coverage["feature_set"] == "onchain").sum()),
                "full_non_null_features": int((coverage["feature_set"] == "full").sum()),
            },
            "warnings": self.warnings,
            "limitations": self.limitations,
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        self.output_paths.update({k: v for k, v in manifest["output_files"].items() if v})
    def _write_partitioned(self, partition_root: Path, market_features: pd.DataFrame, full_features: pd.DataFrame) -> None:
        for label, df in [("market", market_features), ("full", full_features)]:
            if df.empty:
                continue
            working = df.copy()
            working["year"] = ensure_utc(working["date_ts"]).dt.year
            working["month"] = ensure_utc(working["date_ts"]).dt.month
            for (year, month), grp in working.groupby(["year", "month"]):
                part_dir = partition_root / label / f"year={int(year)}" / f"month={int(month):02d}"
                part_dir.mkdir(parents=True, exist_ok=True)
                grp.drop(columns=["year", "month"]).to_parquet(part_dir / f"part-{self.run_id}.parquet", index=False)

    def _write_quality_report(
        self,
        path: Path,
        market_features: pd.DataFrame,
        onchain_features: pd.DataFrame,
        full_features: pd.DataFrame,
        coverage: pd.DataFrame,
        keep_info: Dict[str, Any],
        fatal_errors: List[str],
    ) -> None:
        lines = [
            "# FeatureAgent Data Quality",
            "",
            "## Input Status",
            f"- Market input: {self.market_input_path}",
            f"- On-chain wide input: {self.onchain_wide_input_path}",
            f"- On-chain observations input: {self.onchain_obs_input_path}",
            f"- Universe input: {self.universe_input_path}",
            "",
            "## Output Summary",
            f"- Market rows: {len(market_features)}",
            f"- On-chain rows: {len(onchain_features)}",
            f"- Full rows: {len(full_features)}",
            f"- Market symbols: {market_features['symbol'].nunique() if not market_features.empty else 0}",
            f"- On-chain symbols with coverage: {onchain_features.loc[onchain_features['onchain_available'] == True, 'symbol'].nunique() if not onchain_features.empty else 0}",  # noqa: E712
            f"- Full symbols: {full_features['symbol'].nunique() if not full_features.empty else 0}",
            f"- Date range: {full_features['date_ts'].min()} -> {full_features['date_ts'].max()}" if not full_features.empty else "- Date range: n/a",
            "",
            "## QA Checks",
            f"- Duplicate symbol/date rows: {bool(full_features.duplicated(['symbol', 'date_ts']).any())}",
            f"- Prohibited columns: {check_for_prohibited_columns(full_features.columns)}",
            f"- All-null features dropped warning count: {len([w for w in self.warnings if 'all-null' in w or 'dropped all-null' in w])}",
            f"- Final kept feature count: {len(keep_info['kept_features'])}",
            "",
            "## Pruning Summary",
            f"- Candidate features: {len(keep_info['all_candidate_features'])}",
            f"- Kept features: {len(keep_info['kept_features'])}",
            f"- Dropped features: {len(keep_info['dropped_features'])}",
            "",
            "## Final Status",
            f"- PASS: {not bool(fatal_errors)}",
        ]
        if self.warnings:
            lines.extend(["", "## Warnings"] + [f"- {warning}" for warning in self.warnings])
        if fatal_errors:
            lines.extend(["", "## Failures"] + [f"- {err}" for err in fatal_errors])
        path.write_text("\n".join(lines))


class FeatureAgentV1(FeatureAgent):
    """Backward-compatible wrapper around canonical FeatureAgent."""


class FeatureAgentV2(FeatureAgent):
    """Backward-compatible wrapper around canonical FeatureAgent."""
