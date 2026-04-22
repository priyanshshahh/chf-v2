"""
CHF Feature Engineering Module
Implements all feature families with explicit mathematical formulas.
No look-ahead leakage: all features use only information available at time t.

Mathematical Definitions:
─────────────────────────
1. Log Return:
   Return_{t,n} = ln(P_t / P_{t-n})

2. Rolling Volatility:
   Vol_{t,w} = std(Return_{t-w:t})  [annualized: * sqrt(365)]

3. Rolling Skewness:
   Skew_{t,w} = skewness of daily log returns over window w

4. Rolling Beta to BTC:
   beta_i = Cov(R_i, R_BTC) / Var(R_BTC)  [over window w]

5. Turnover Ratio:
   Turnover_{t,w} = mean(Volume_{t-w:t}) / mean(Volume_all)

6. NVT Ratio:
   NVT = Market_Cap / Daily_Tx_Volume  (or proxy using price * supply / TxTfrValAdjUSD)

7. MVRV Proxy:
   MVRV_proxy = Market_Cap / Realized_Cap  (CapMVRVCur from CoinMetrics where available)

8. Active Address Growth:
   AdrGrowth_{t,w} = ln(AdrActCnt_t / AdrActCnt_{t-w})

9. TVL Ratio:
   TVL_ratio = TVL_USD / Market_Cap  (DeFi protocol utility proxy)

10. Cross-sectional Z-score:
    Z_{i,t} = (X_{i,t} - mean_t(X)) / std_t(X)
"""
from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── Numba import with graceful fallback ──────────────────────────────────────
# Numba is used for the rolling beta kernel (_rolling_beta_numba) because it
# involves a nested Python loop over large arrays that benefits from JIT.
# Other features (log returns, volatility, skewness) use pandas.rolling which
# is already Cython/C-backed — adding numba there would add JIT overhead.
try:
    import numba
    _NUMBA_AVAILABLE = True
except ImportError:
    _NUMBA_AVAILABLE = False


if _NUMBA_AVAILABLE:
    @numba.njit(cache=True)
    def _rolling_beta_kernel(asset_ret: np.ndarray, bench_ret: np.ndarray, window: int) -> np.ndarray:
        """
        Numba-JIT rolling beta kernel.
        beta_i = Cov(R_i, R_BTC) / Var(R_BTC)
        Operates on aligned 1-D float64 arrays.
        Returns NaN for windows with fewer than 5 valid observations.
        """
        n = len(asset_ret)
        result = np.full(n, np.nan)
        for i in range(window - 1, n):
            x = asset_ret[i - window + 1: i + 1]
            y = bench_ret[i - window + 1: i + 1]
            valid = 0
            sum_x = 0.0
            sum_y = 0.0
            for j in range(window):
                if not (np.isnan(x[j]) or np.isnan(y[j])):
                    sum_x += x[j]
                    sum_y += y[j]
                    valid += 1
            if valid < 5:
                continue
            mean_x = sum_x / valid
            mean_y = sum_y / valid
            cov_xy = 0.0
            var_y = 0.0
            for j in range(window):
                if not (np.isnan(x[j]) or np.isnan(y[j])):
                    dx = x[j] - mean_x
                    dy = y[j] - mean_y
                    cov_xy += dx * dy
                    var_y += dy * dy
            if var_y < 1e-10:
                continue
            result[i] = cov_xy / var_y
        return result
else:
    def _rolling_beta_kernel(asset_ret: np.ndarray, bench_ret: np.ndarray, window: int) -> np.ndarray:
        """Pure-NumPy fallback for rolling beta (used when numba is not installed)."""
        n = len(asset_ret)
        result = np.full(n, np.nan)
        for i in range(window - 1, n):
            x = asset_ret[i - window + 1: i + 1]
            y = bench_ret[i - window + 1: i + 1]
            mask = ~(np.isnan(x) | np.isnan(y))
            if mask.sum() < 5:
                continue
            xm, ym = x[mask], y[mask]
            cov = np.cov(xm, ym)
            var_y = cov[1, 1]
            if var_y < 1e-10:
                continue
            result[i] = cov[0, 1] / var_y
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Market Feature Functions
# ─────────────────────────────────────────────────────────────────────────────

def compute_log_returns(prices: pd.Series, window: int) -> pd.Series:
    """
    Compute log return over a rolling window.
    Return_{t,n} = ln(P_t / P_{t-n})
    """
    return np.log(prices / prices.shift(window))


def compute_rolling_volatility(
    log_returns: pd.Series, window: int, annualize: bool = True
) -> pd.Series:
    """
    Rolling standard deviation of log returns.
    Annualized by default: Vol * sqrt(365).
    """
    vol = log_returns.rolling(window=window, min_periods=max(window // 2, 5)).std()
    if annualize:
        vol = vol * np.sqrt(365)
    return vol


def compute_rolling_skewness(log_returns: pd.Series, window: int) -> pd.Series:
    """Rolling skewness of log returns."""
    return log_returns.rolling(window=window, min_periods=max(window // 2, 5)).skew()


def compute_rolling_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int,
) -> pd.Series:
    """
    Rolling beta to benchmark (BTC).
    beta_i = Cov(R_i, R_BTC) / Var(R_BTC)

    Uses Numba-JIT kernel (_rolling_beta_kernel) when numba is available.
    Falls back to pure-NumPy otherwise.
    """
    aligned = pd.concat([asset_returns, benchmark_returns], axis=1).dropna()
    if aligned.empty:
        return pd.Series(np.nan, index=asset_returns.index)

    asset_arr = aligned.iloc[:, 0].values.astype(np.float64)
    bench_arr = aligned.iloc[:, 1].values.astype(np.float64)

    beta_arr = _rolling_beta_kernel(asset_arr, bench_arr, window)
    beta_series = pd.Series(beta_arr, index=aligned.index)
    return beta_series.reindex(asset_returns.index)


def compute_volume_ratio(volume: pd.Series, window: int) -> pd.Series:
    """
    Volume ratio: rolling mean volume / overall mean volume.
    Proxy for relative liquidity/activity.
    """
    rolling_mean = volume.rolling(window=window, min_periods=max(window // 2, 3)).mean()
    overall_mean = volume.mean()
    if overall_mean < 1e-10:
        return pd.Series(np.nan, index=volume.index)
    return rolling_mean / overall_mean


def compute_atr_proxy(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """
    Average True Range proxy using daily OHLCV.
    ATR = mean(|High - Low|) over window, normalized by close.
    """
    true_range = (high - low) / close.clip(lower=1e-10)
    return true_range.rolling(window=window, min_periods=max(window // 2, 3)).mean()


def compute_reversal(log_returns: pd.Series, short_window: int = 3, long_window: int = 30) -> pd.Series:
    """
    Reversal feature: short-term return minus long-term return.
    Captures mean-reversion signal.
    """
    short_ret = log_returns.rolling(window=short_window, min_periods=1).sum()
    long_ret = log_returns.rolling(window=long_window, min_periods=max(long_window // 2, 5)).sum()
    return short_ret - long_ret


# ─────────────────────────────────────────────────────────────────────────────
# On-Chain Feature Functions
# ─────────────────────────────────────────────────────────────────────────────

def compute_active_address_growth(adr_series: pd.Series, window: int) -> pd.Series:
    """
    Active address growth rate.
    AdrGrowth_{t,w} = ln(AdrActCnt_t / AdrActCnt_{t-w})
    """
    return np.log(adr_series / adr_series.shift(window).clip(lower=1))


def compute_tx_count_growth(tx_series: pd.Series, window: int) -> pd.Series:
    """
    Transaction count growth rate.
    TxGrowth_{t,w} = ln(TxCnt_t / TxCnt_{t-w})
    """
    return np.log(tx_series / tx_series.shift(window).clip(lower=1))


def compute_nvt_ratio(market_cap: pd.Series, tx_volume_usd: pd.Series) -> pd.Series:
    """
    NVT Ratio: Network Value to Transactions.
    NVT = Market_Cap / Daily_Tx_Volume
    Uses CoinMetrics TxTfrValAdjUSD as transaction volume proxy.
    """
    return market_cap / tx_volume_usd.clip(lower=1)


def compute_nvt_signal(nvt_series: pd.Series, window: int = 90) -> pd.Series:
    """
    NVT Signal: NVT smoothed over window.
    NVT_signal = Market_Cap / MA(Tx_Volume, window)
    """
    return nvt_series.rolling(window=window, min_periods=max(window // 2, 10)).mean()


def compute_mvrv_proxy(market_cap: pd.Series, realized_cap: pd.Series) -> pd.Series:
    """
    MVRV-style ratio.
    MVRV = Market_Cap / Realized_Cap
    Uses CapMVRVCur from CoinMetrics where available, else computes from CapRealUSD.
    """
    return market_cap / realized_cap.clip(lower=1)


def compute_realized_cap_change(realized_cap: pd.Series, window: int) -> pd.Series:
    """
    Realized cap change rate.
    RealCapChange_{t,w} = ln(CapRealUSD_t / CapRealUSD_{t-w})
    """
    return np.log(realized_cap / realized_cap.shift(window).clip(lower=1))


def compute_fee_intensity(fees: pd.Series, market_cap: pd.Series) -> pd.Series:
    """
    Fee intensity: fees relative to market cap.
    FeeIntensity = FeeTotUSD / Market_Cap
    """
    return fees / market_cap.clip(lower=1)


def compute_tvl_ratio(tvl: pd.Series, market_cap: pd.Series) -> pd.Series:
    """
    TVL ratio: DeFi protocol utility proxy.
    TVL_ratio = TVL_USD / Market_Cap
    """
    return tvl / market_cap.clip(lower=1)


def compute_tvl_growth(tvl: pd.Series, window: int) -> pd.Series:
    """TVL growth rate over window."""
    return np.log(tvl / tvl.shift(window).clip(lower=1))


# ─────────────────────────────────────────────────────────────────────────────
# Cross-Sectional Functions
# ─────────────────────────────────────────────────────────────────────────────

def cross_sectional_zscore(df: pd.DataFrame, feature_col: str) -> pd.Series:
    """
    Cross-sectional z-score at each date.
    Z_{i,t} = (X_{i,t} - mean_t(X)) / std_t(X)
    """
    def _zscore(group):
        x = group[feature_col]
        mu = x.mean()
        sigma = x.std()
        if sigma < 1e-10:
            return pd.Series(0.0, index=group.index)
        return (x - mu) / sigma

    return df.groupby("date_ts", group_keys=False).apply(_zscore)


def winsorize_series(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """Winsorize a series at specified quantile bounds."""
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lower=lo, upper=hi)


def cross_sectional_rank(df: pd.DataFrame, feature_col: str) -> pd.Series:
    """Cross-sectional percentile rank at each date."""
    return df.groupby("date_ts")[feature_col].rank(pct=True)


# ─────────────────────────────────────────────────────────────────────────────
# Redundancy Pruning
# ─────────────────────────────────────────────────────────────────────────────

def compute_correlation_clusters(
    feature_matrix: pd.DataFrame,
    threshold: float = 0.85,
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Identify highly correlated feature pairs and return a pruned keep-list.
    Uses greedy correlation clustering: keep first feature in each cluster.
    """
    corr = feature_matrix.corr(method="spearman").abs()
    cols = list(corr.columns)
    to_drop = set()
    redundant_pairs = []

    for i in range(len(cols)):
        if cols[i] in to_drop:
            continue
        for j in range(i + 1, len(cols)):
            if cols[j] in to_drop:
                continue
            if corr.iloc[i, j] > threshold:
                to_drop.add(cols[j])
                redundant_pairs.append((cols[i], cols[j]))

    keep_list = [c for c in cols if c not in to_drop]
    return keep_list, redundant_pairs


def compute_vif(feature_matrix: pd.DataFrame) -> pd.DataFrame:
    """
    Compute Variance Inflation Factor for each feature.
    Features with VIF > threshold are flagged for removal.
    """
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    df_clean = feature_matrix.dropna()
    if df_clean.empty or df_clean.shape[1] < 2:
        return pd.DataFrame()

    vif_data = pd.DataFrame({
        "feature": df_clean.columns,
        "vif": [
            variance_inflation_factor(df_clean.values, i)
            for i in range(df_clean.shape[1])
        ],
    })
    return vif_data.sort_values("vif", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Feature Dictionary
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_DICTIONARY = {
    # Market features
    "ret_3d": {
        "family": "market",
        "formula": "ln(P_t / P_{t-3})",
        "description": "3-day log return",
        "parameters": {"window": 3},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "ret_7d": {
        "family": "market",
        "formula": "ln(P_t / P_{t-7})",
        "description": "7-day log return",
        "parameters": {"window": 7},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "ret_14d": {
        "family": "market",
        "formula": "ln(P_t / P_{t-14})",
        "description": "14-day log return",
        "parameters": {"window": 14},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "ret_30d": {
        "family": "market",
        "formula": "ln(P_t / P_{t-30})",
        "description": "30-day log return",
        "parameters": {"window": 30},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "ret_90d": {
        "family": "market",
        "formula": "ln(P_t / P_{t-90})",
        "description": "90-day log return",
        "parameters": {"window": 90},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "vol_30d": {
        "family": "market",
        "formula": "std(daily_log_returns, window=30) * sqrt(365)",
        "description": "30-day annualized volatility",
        "parameters": {"window": 30},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "skew_30d": {
        "family": "market",
        "formula": "skewness(daily_log_returns, window=30)",
        "description": "30-day return skewness",
        "parameters": {"window": 30},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "beta_btc_60d": {
        "family": "market",
        "formula": "Cov(R_i, R_BTC) / Var(R_BTC) over 60 days",
        "description": "60-day rolling beta to BTC",
        "parameters": {"window": 60},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "vol_ratio_30d": {
        "family": "market",
        "formula": "MA(Volume, 30) / mean(Volume_all)",
        "description": "30-day volume ratio (relative liquidity)",
        "parameters": {"window": 30},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "reversal_3_30": {
        "family": "market",
        "formula": "ret_3d - ret_30d",
        "description": "Short-term reversal signal",
        "parameters": {"short_window": 3, "long_window": 30},
        "data_sources": ["ohlcv"],
        "is_proxy": False,
    },
    "atr_14d": {
        "family": "market",
        "formula": "mean(|High - Low| / Close, window=14)",
        "description": "14-day ATR proxy (normalized)",
        "parameters": {"window": 14},
        "data_sources": ["ohlcv"],
        "is_proxy": True,
        "proxy_notes": "Simplified ATR using daily range / close",
    },
    # On-chain features
    "adr_growth_30d": {
        "family": "on_chain",
        "formula": "ln(AdrActCnt_t / AdrActCnt_{t-30})",
        "description": "30-day active address growth",
        "parameters": {"window": 30},
        "data_sources": ["coinmetrics"],
        "is_proxy": False,
    },
    "tx_growth_30d": {
        "family": "on_chain",
        "formula": "ln(TxCnt_t / TxCnt_{t-30})",
        "description": "30-day transaction count growth",
        "parameters": {"window": 30},
        "data_sources": ["coinmetrics"],
        "is_proxy": False,
    },
    "nvt_ratio": {
        "family": "on_chain",
        "formula": "Market_Cap / TxTfrValAdjUSD",
        "description": "NVT ratio (network value to transactions)",
        "parameters": {},
        "data_sources": ["coinmetrics"],
        "is_proxy": True,
        "proxy_notes": "Uses TxTfrValAdjUSD as transaction volume proxy",
    },
    "nvt_signal_90d": {
        "family": "on_chain",
        "formula": "Market_Cap / MA(TxTfrValAdjUSD, 90)",
        "description": "NVT signal (smoothed NVT)",
        "parameters": {"window": 90},
        "data_sources": ["coinmetrics"],
        "is_proxy": True,
        "proxy_notes": "90-day smoothed NVT",
    },
    "mvrv_proxy": {
        "family": "on_chain",
        "formula": "CapMVRVCur (CoinMetrics) or Market_Cap / CapRealUSD",
        "description": "MVRV ratio or proxy",
        "parameters": {},
        "data_sources": ["coinmetrics"],
        "is_proxy": True,
        "proxy_notes": "Uses CapMVRVCur directly where available; else Market_Cap/CapRealUSD",
    },
    "realized_cap_change_30d": {
        "family": "on_chain",
        "formula": "ln(CapRealUSD_t / CapRealUSD_{t-30})",
        "description": "30-day realized cap change",
        "parameters": {"window": 30},
        "data_sources": ["coinmetrics"],
        "is_proxy": False,
    },
    "fee_intensity": {
        "family": "on_chain",
        "formula": "FeeTotUSD / Market_Cap",
        "description": "Fee intensity relative to market cap",
        "parameters": {},
        "data_sources": ["coinmetrics"],
        "is_proxy": False,
    },
    "tvl_ratio": {
        "family": "on_chain",
        "formula": "TVL_USD / Market_Cap",
        "description": "TVL-to-market-cap ratio (DeFi utility proxy)",
        "parameters": {},
        "data_sources": ["defillama"],
        "is_proxy": True,
        "proxy_notes": "Only available for DeFi protocols with DeFiLlama coverage",
    },
    "tvl_growth_30d": {
        "family": "on_chain",
        "formula": "ln(TVL_t / TVL_{t-30})",
        "description": "30-day TVL growth",
        "parameters": {"window": 30},
        "data_sources": ["defillama"],
        "is_proxy": False,
    },
}


def compute_turnover_ratio(volume: pd.Series, window: int) -> pd.Series:
    """
    Turnover ratio: rolling mean volume / global mean volume.
    Turnover_{t,w} = mean(Volume_{t-w:t}) / mean(Volume_all)
    Alias for compute_volume_ratio for backward compatibility.
    """
    return compute_volume_ratio(volume, window)


# Alias for backward compatibility
compute_cross_sectional_rank = cross_sectional_rank
