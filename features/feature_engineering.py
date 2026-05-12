from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd


METADATA_COLUMNS = {
    "date_ts",
    "symbol",
    "feature_set",
    "feature_version",
    "snapshot_id",
    "run_id",
    "created_at_utc",
    "onchain_lag_days",
}

PROHIBITED_COLUMN_TOKENS = ("target", "label", "forward", "future", "ret_fwd", "lead", "next_return")
ALLOWED_PROHIBITED_EXACT = {"is_forward_filled_market"}


@dataclass(frozen=True)
class FeatureDefinitionRecord:
    feature_name: str
    feature_group: str
    formula: str
    source_columns: List[str]
    lookback_window: int | None
    leakage_policy: str
    economic_rationale: str
    null_policy: str
    transformation: str


def ensure_utc(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, utc=True, errors="coerce")
    return dt.dt.normalize()


def safe_log_ratio(current: pd.Series, lagged: pd.Series) -> pd.Series:
    current_num = pd.to_numeric(current, errors="coerce")
    lagged_num = pd.to_numeric(lagged, errors="coerce")
    valid = (current_num > 0) & (lagged_num > 0)
    out = pd.Series(np.nan, index=current.index, dtype="float64")
    out.loc[valid] = np.log(current_num.loc[valid] / lagged_num.loc[valid])
    return out


def rolling_zscore(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    min_periods = min_periods or max(window // 2, 5)
    mean = series.rolling(window=window, min_periods=min_periods).mean()
    std = series.rolling(window=window, min_periods=min_periods).std()
    return (series - mean) / std.replace(0, np.nan)


def rolling_downside_vol(log_returns: pd.Series, window: int, annualize: bool = True) -> pd.Series:
    clipped = log_returns.clip(upper=0)
    downside = clipped.pow(2).rolling(window=window, min_periods=max(window // 2, 5)).mean().pow(0.5)
    return downside * np.sqrt(365) if annualize else downside


def rolling_beta_and_corr(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int,
    min_periods: int | None = None,
) -> Tuple[pd.Series, pd.Series]:
    min_periods = min_periods or max(window // 2, 10)
    aligned = pd.concat(
        [asset_returns.rename("asset"), benchmark_returns.rename("bench")],
        axis=1,
    ).sort_index()
    rolling_cov = aligned["asset"].rolling(window=window, min_periods=min_periods).cov(aligned["bench"])
    rolling_var = aligned["bench"].rolling(window=window, min_periods=min_periods).var()
    rolling_corr = aligned["asset"].rolling(window=window, min_periods=min_periods).corr(aligned["bench"])
    beta = rolling_cov / rolling_var.replace(0, np.nan)
    return beta.reindex(asset_returns.index), rolling_corr.reindex(asset_returns.index)


def cross_sectional_winsorize_by_date(
    df: pd.DataFrame,
    columns: Sequence[str],
    lower_quantile: float,
    upper_quantile: float,
) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue

        def _winsorize(group: pd.DataFrame) -> pd.Series:
            series = pd.to_numeric(group[col], errors="coerce")
            valid = series.dropna()
            if valid.empty:
                return series
            lo = valid.quantile(lower_quantile)
            hi = valid.quantile(upper_quantile)
            return series.clip(lower=lo, upper=hi)

        out[col] = out.groupby("date_ts", group_keys=False).apply(_winsorize)
    return out


def cross_sectional_zscore_by_date(
    df: pd.DataFrame,
    columns: Sequence[str],
    min_assets_per_date: int,
) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            continue

        def _zscore(group: pd.DataFrame) -> pd.Series:
            series = pd.to_numeric(group[col], errors="coerce")
            valid = series.dropna()
            if len(valid) < min_assets_per_date:
                return pd.Series(np.nan, index=group.index)
            std = valid.std()
            if pd.isna(std) or std == 0:
                return pd.Series(np.nan, index=group.index)
            return (series - valid.mean()) / std

        out[f"{col}_cs_z"] = out.groupby("date_ts", group_keys=False).apply(_zscore)
    return out


def deterministic_correlation_prune(
    feature_df: pd.DataFrame,
    candidate_cols: Sequence[str],
    threshold: float,
    max_final_features: int | None = None,
    min_final_features: int | None = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    cols = [c for c in candidate_cols if c in feature_df.columns]
    if len(cols) <= 1:
        return cols, []
    corr = feature_df[cols].corr(method="spearman").abs()
    null_pct = feature_df[cols].isna().mean().to_dict()
    avg_abs_corr = {
        col: float(corr[col].drop(labels=[col]).mean()) if len(corr[col]) > 1 else 0.0
        for col in cols
    }
    dropped: List[Dict[str, Any]] = []
    keep = list(cols)

    for i, left in enumerate(cols):
        if left not in keep:
            continue
        for right in cols[i + 1 :]:
            if right not in keep:
                continue
            corr_val = corr.loc[left, right]
            if pd.isna(corr_val) or corr_val < threshold:
                continue
            ordered = sorted(
                [left, right],
                key=lambda col: (null_pct.get(col, 1.0), avg_abs_corr.get(col, np.inf), col),
            )
            winner, loser = ordered[0], ordered[1]
            if min_final_features and len(keep) <= min_final_features:
                continue
            if loser in keep:
                keep.remove(loser)
                dropped.append(
                    {
                        "feature": loser,
                        "reason": "high_spearman_correlation",
                        "detail": f"corr_with={winner}, corr={corr_val:.4f}",
                    }
                )
    if max_final_features and len(keep) > max_final_features:
        ranked = sorted(
            keep,
            key=lambda col: (null_pct.get(col, 1.0), avg_abs_corr.get(col, np.inf), col),
        )
        survivors = ranked[:max_final_features]
        for col in keep:
            if col not in survivors:
                dropped.append(
                    {
                        "feature": col,
                        "reason": "max_final_features_cap",
                        "detail": f"max_final_features={max_final_features}",
                    }
                )
        keep = survivors
    return keep, dropped


def iterative_vif_prune(
    feature_df: pd.DataFrame,
    candidate_cols: Sequence[str],
    vif_threshold: float,
    min_final_features: int,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    cols = [c for c in candidate_cols if c in feature_df.columns]
    if len(cols) < 2:
        return cols, []
    dropped: List[Dict[str, Any]] = []
    try:
        from statsmodels.stats.outliers_influence import variance_inflation_factor
    except Exception:
        return cols, dropped

    working = feature_df[cols].copy()
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(working[c])]
    if len(numeric_cols) < 2:
        return numeric_cols, dropped
    working = working[numeric_cols].replace([np.inf, -np.inf], np.nan)
    working = working.loc[:, working.notna().sum() > 0]
    if working.shape[1] < 2:
        return list(working.columns), dropped
    working = working.fillna(working.median(numeric_only=True))
    if len(working) > 10000:
        working = working.sample(n=10000, random_state=42)

    keep = list(working.columns)
    while len(keep) > max(min_final_features, 1):
        matrix = working[keep]
        try:
            vifs = [
                variance_inflation_factor(matrix.values, i)
                for i in range(matrix.shape[1])
            ]
        except Exception:
            break
        vif_pairs = list(zip(keep, vifs))
        worst_feature, worst_vif = max(vif_pairs, key=lambda item: item[1])
        if pd.isna(worst_vif) or worst_vif <= vif_threshold:
            break
        keep.remove(worst_feature)
        dropped.append(
            {
                "feature": worst_feature,
                "reason": "high_vif",
                "detail": f"vif={worst_vif:.4f}, threshold={vif_threshold}",
            }
        )
    return keep, dropped


def list_feature_columns(df: pd.DataFrame) -> List[str]:
    return [
        c for c in df.columns
        if c not in METADATA_COLUMNS and c not in {"onchain_available", "coinmetrics_available", "defillama_available", "onchain_feature_count_non_null"}
    ]


def check_for_prohibited_columns(columns: Iterable[str]) -> List[str]:
    bad: List[str] = []
    for col in columns:
        if col in ALLOWED_PROHIBITED_EXACT:
            continue
        lower = col.lower()
        if any(token in lower for token in PROHIBITED_COLUMN_TOKENS):
            bad.append(col)
    return bad


def infer_feature_group(feature_name: str) -> str:
    if feature_name.startswith("missing_") or feature_name in {
        "onchain_available",
        "coinmetrics_available",
        "defillama_available",
        "onchain_feature_count_non_null",
        "onchain_lag_days",
    }:
        return "onchain_missingness"
    if feature_name.endswith("_cs_z"):
        return "cross_sectional_zscore"
    if feature_name in {
        "is_forward_filled_market",
        "market_data_available",
        "market_history_days_available",
    }:
        return "market_quality"
    if feature_name.startswith("log_ret_") or feature_name.startswith("momentum_") or feature_name.startswith("reversal_"):
        return "market_returns_momentum"
    if "vol" in feature_name or feature_name.startswith("skew_") or feature_name.startswith("downside_vol_"):
        return "market_risk_liquidity" if "volume" in feature_name or "dollar_volume" in feature_name else "market_risk"
    if feature_name.startswith("price_sma_gap_") or feature_name.startswith("zscore_close_"):
        return "market_mean_reversion"
    if feature_name.startswith("dollar_volume") or feature_name.startswith("log_dollar_volume") or feature_name.startswith("volume_"):
        return "market_liquidity"
    if feature_name.startswith("hl_range") or feature_name.startswith("atr_"):
        return "market_range"
    if feature_name.startswith("drawdown_") or feature_name.startswith("distance_from_"):
        return "market_drawdown"
    if feature_name.startswith("beta_btc_") or feature_name.startswith("corr_btc_"):
        return "market_beta"
    if feature_name in {"adr_active_count", "tx_count", "log_adr_active_count"} or feature_name.startswith("adr_active_") or feature_name.startswith("tx_count_"):
        return "onchain_network_activity"
    if feature_name.startswith("mvrv") or feature_name.startswith("realized_cap_proxy") or feature_name.startswith("nvt_"):
        return "onchain_valuation"
    if feature_name.startswith("chain_tvl") or feature_name.startswith("protocol_tvl") or feature_name.startswith("fees_") or feature_name.startswith("dex_volume_") or feature_name.endswith("_to_tvl"):
        return "onchain_defi"
    if feature_name.startswith("current_supply") or feature_name.startswith("supply_growth") or feature_name.startswith("issuance") or feature_name.startswith("market_cap"):
        return "onchain_supply_capital"
    return "misc"


def build_feature_dictionary(
    feature_columns: Sequence[str],
    kept_features: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    keep_set = set(kept_features)
    definitions: Dict[str, FeatureDefinitionRecord] = {}

    def add(name: str, group: str, formula: str, source: List[str], window: int | None, rationale: str, null_policy: str, transformation: str) -> None:
        definitions[name] = FeatureDefinitionRecord(
            feature_name=name,
            feature_group=group,
            formula=formula,
            source_columns=source,
            lookback_window=window,
            leakage_policy="backward-looking only; on-chain features lagged before market join where applicable",
            economic_rationale=rationale,
            null_policy=null_policy,
            transformation=transformation,
        )

    for name in feature_columns:
        if name.endswith("_cs_z"):
            base = name[:-5]
            add(name, "cross_sectional_zscore", f"same-date cross-sectional z-score of {base}", [base], None, "Normalizes signals across the tradable cross-section without using future dates.", "null when fewer than required assets exist on a date", "cross-sectional z-score")
        elif name.startswith("log_ret_"):
            window = int(name.split("_")[-1].replace("d", ""))
            add(name, "market_returns_momentum", f"log(close_t / close_t-{window})", ["close"], window, "Short and medium-term price momentum.", "null during warmup", "log return")
        elif name in {"momentum_7_30", "momentum_14_90"}:
            add(name, "market_returns_momentum", "difference of two backward-looking log returns", ["close"], None, "Relative trend persistence across horizons.", "null during warmup", "difference")
        elif name.startswith("realized_vol_"):
            window = int(name.split("_")[-1].replace("d", ""))
            add(name, "market_risk", "rolling std of daily log returns * sqrt(365)", ["close"], window, "Realized risk and dispersion.", "null during warmup", "annualized rolling std")
        elif name.startswith("skew_") or name.startswith("downside_vol_"):
            add(name, "market_risk", "rolling distribution statistic on daily log returns", ["close"], 30, "Captures asymmetry and downside concentration.", "null during warmup", "rolling statistic")
        elif name.startswith("price_sma_gap_") or name == "zscore_close_30d" or name == "reversal_3_30":
            add(name, "market_mean_reversion", "backward-looking mean reversion signal", ["close"], 30, "Distance from local equilibrium or reversal pressure.", "null during warmup", "rolling normalization")
        elif name.startswith("dollar_volume") or name.startswith("log_dollar_volume") or name.startswith("volume_"):
            add(name, "market_liquidity", "volume/liquidity transform using current and rolling trailing statistics", ["close", "volume"], 30, "Tradability and participation intensity.", "null during warmup where trailing windows apply", "ratio or z-score")
        elif name in {"hl_range_pct", "atr_proxy_14d"}:
            add(name, "market_range", "range-based volatility from daily OHLC", ["high", "low", "close"], 14, "Intraday price dispersion.", "null during warmup", "range normalization")
        elif name.startswith("drawdown_") or name.startswith("distance_from_"):
            add(name, "market_drawdown", "distance from trailing rolling high", ["close"], 90 if "90" in name else 30, "Pain from local peak and recovery state.", "null during warmup", "rolling peak distance")
        elif name.startswith("beta_btc_") or name.startswith("corr_btc_"):
            add(name, "market_beta", "rolling covariance/correlation with BTC daily log returns", ["close"], 60, "Common crypto market exposure.", "null during warmup or if BTC absent", "rolling covariance")
        elif name in {"is_forward_filled_market", "market_data_available", "market_history_days_available"}:
            add(name, "market_quality", "quality/control flag derived from market input completeness", ["is_forward_filled", "close"], None, "Helps downstream models understand data reliability.", "never null after market backbone load", "indicator / count")
        elif name in {"adr_active_count", "tx_count", "current_supply", "issuance_total_usd", "market_cap_usd", "mvrv_current", "chain_tvl_usd", "protocol_tvl_usd", "fees_usd", "dex_volume_usd", "realized_cap_proxy", "nvt_tx_proxy", "nvt_dex_proxy"}:
            add(name, infer_feature_group(name), "raw or directly derived on-chain / DeFi metric", [name], None, "Level information about network usage, valuation, or DeFi capital.", "null when unavailable upstream; lagged before join", "level / proxy")
        elif name.startswith("missing_") or name in {"onchain_available", "coinmetrics_available", "defillama_available", "onchain_feature_count_non_null", "onchain_lag_days"}:
            add(name, "onchain_missingness", "availability/missingness indicator after lagged as-of alignment", [name.replace("missing_", "")], None, "Sparse data coverage itself can be predictive and is needed for robust modeling.", "never uses future data", "indicator")
        elif name.startswith("adr_active_growth_") or name.startswith("tx_count_growth_") or name == "log_adr_active_count" or name == "tx_count_zscore_30d":
            add(name, "onchain_network_activity", "backward-looking growth or normalization of network activity metrics", ["adr_active_count", "tx_count"], 30, "Captures acceleration or deceleration in network usage.", "null during warmup or sparse upstream coverage", "growth / z-score")
        elif name.startswith("mvrv_change_") or name.startswith("mvrv_zscore_"):
            add(name, "onchain_valuation", "change or normalization of valuation ratio", ["mvrv_current"], 90 if "90" in name else 30, "Tracks valuation stretch and normalization.", "null during warmup or if source ratio unavailable", "difference / z-score")
        elif name.startswith("chain_tvl_growth_") or name.startswith("protocol_tvl_growth_") or name.startswith("fees_growth_") or name.startswith("dex_volume_growth_") or name in {"fees_to_tvl", "dex_volume_to_tvl"}:
            add(name, "onchain_defi", "backward-looking DeFi capital flow transform", ["chain_tvl_usd", "protocol_tvl_usd", "fees_usd", "dex_volume_usd"], 30, "Captures capital formation, utilization, and monetization in DeFi.", "null when source metric absent or denominator unavailable", "growth / ratio")
        elif name.startswith("supply_growth_") or name in {"issuance_to_market_cap", "market_cap_growth_30d"}:
            add(name, "onchain_supply_capital", "backward-looking supply or capital formation transform", ["current_supply", "issuance_total_usd", "market_cap_usd"], 30, "Captures token issuance pressure and capital base growth.", "null during warmup or missing upstream coverage", "growth / ratio")
        else:
            add(name, infer_feature_group(name), "derived feature", [name], None, "Derived research feature.", "null during warmup where applicable", "derived")

    return {
        name: {
            "feature_name": rec.feature_name,
            "feature_group": rec.feature_group,
            "formula": rec.formula,
            "source_columns": rec.source_columns,
            "lookback_window": rec.lookback_window,
            "leakage_policy": rec.leakage_policy,
            "economic_rationale": rec.economic_rationale,
            "null_policy": rec.null_policy,
            "transformation": rec.transformation,
            "included_in_final_keep_list": name in keep_set,
        }
        for name, rec in definitions.items()
    }
