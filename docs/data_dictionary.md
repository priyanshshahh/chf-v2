# CHF Data Dictionary

This document defines every feature, label, and metric used in the CHF system.
All formulas are written in standard mathematical notation.
All features are computed with **no look-ahead**: at time `t`, only data available at or before `t` is used.

---

## 1. Raw Data Tables

### 1.1 OHLCV (`data/raw/market/year=YYYY/month=MM/SYMBOL.parquet`)

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Ticker symbol (e.g., `BTC`, `ETH`) |
| `date_ts` | datetime[UTC] | UTC midnight timestamp |
| `open` | float64 | Opening price in USD |
| `high` | float64 | Intraday high price in USD |
| `low` | float64 | Intraday low price in USD |
| `close` | float64 | Closing price in USD |
| `volume` | float64 | 24h trading volume in USD |
| `snapshot_id` | str | SHA-256 run identifier |

Source: Binance via CCXT. Hive-partitioned by year and month.

### 1.2 Universe (`data/raw/universe/universe_YYYYMMDD.parquet`)

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Ticker symbol |
| `name` | str | Full asset name |
| `market_cap_usd` | float64 | Market cap at snapshot date |
| `rank` | int | CoinGecko market cap rank |
| `eligible` | bool | Passes all filters |
| `snapshot_date` | date | Date of snapshot |

Source: CoinGecko `/coins/markets`. Excludes stablecoins, wrapped tokens, synthetic assets.

### 1.3 On-Chain (`data/raw/onchain/SYMBOL_onchain.parquet`)

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Ticker symbol |
| `date_ts` | datetime[UTC] | UTC midnight timestamp |
| `active_addresses` | float64 | Daily active addresses (AdrActCnt) |
| `tx_volume_usd` | float64 | Adjusted transfer volume in USD (TxTfrValAdjUSD) |
| `realized_cap_usd` | float64 | Realized market cap in USD (CapRealUSD) |
| `mvrv` | float64 | MVRV ratio (CapMVRVCur, if available) |
| `tvl_usd` | float64 | Total Value Locked in USD (DeFiLlama) |

Sources: CoinMetrics Community API, DeFiLlama.

---

## 2. Feature Store (`data/features/feature_store.parquet`)

### 2.1 Momentum Features

| Feature | Formula | Window | Source |
|---|---|---|---|
| `momentum_7d` | `ln(P_t / P_{t-7})` | 7 days | OHLCV close |
| `momentum_14d` | `ln(P_t / P_{t-14})` | 14 days | OHLCV close |
| `momentum_30d` | `ln(P_t / P_{t-30})` | 30 days | OHLCV close |
| `momentum_90d` | `ln(P_t / P_{t-90})` | 90 days | OHLCV close |

**Implementation:** `features/feature_engineering.py::compute_log_returns()`

### 2.2 Volatility Features

| Feature | Formula | Window | Source |
|---|---|---|---|
| `volatility_30d` | `σ(r_{t-30:t}) × √365` | 30 days | Daily log returns |
| `skewness_30d` | `skew(r_{t-30:t})` | 30 days | Daily log returns |

Where `r_t = ln(P_t / P_{t-1})` (daily log return).

**Implementation:** `features/feature_engineering.py::compute_rolling_volatility()`, `compute_rolling_skewness()`

### 2.3 Beta Feature

| Feature | Formula | Window | Source |
|---|---|---|---|
| `beta_60d` | `Cov(R_i, R_BTC) / Var(R_BTC)` | 60 days | Daily log returns vs BTC |

**Implementation:** `features/feature_engineering.py::compute_rolling_beta()`

**Numba acceleration:** The rolling beta kernel `_rolling_beta_kernel()` is decorated with `@numba.njit(cache=True)` when numba is installed. The kernel iterates over a sliding window computing covariance and variance from scratch, avoiding Python overhead. When numba is not installed, a pure-NumPy fallback is used automatically.

**Why numba only here:** `pandas.rolling()` (used for volatility, skewness, momentum) is already backed by Cython/C. Adding `@numba.jit` to those would add JIT compilation overhead without speed benefit. The beta kernel requires a nested loop over two aligned arrays simultaneously, which pandas cannot express natively — that is where numba provides a real speedup.

### 2.4 Volume Feature

| Feature | Formula | Window | Source |
|---|---|---|---|
| `turnover_ratio` | `MA(Volume_{t-30:t}) / mean(Volume_all)` | 30 days | OHLCV volume |

**Implementation:** `features/feature_engineering.py::compute_turnover_ratio()`

### 2.5 On-Chain Features

| Feature | Formula | Source | Proxy? |
|---|---|---|---|
| `nvt_ratio` | `log1p(Market_Cap / MA(TxTfrValAdjUSD, 7))` | CoinMetrics | Proxy: uses TxTfrValAdjUSD as tx volume |
| `mvrv_proxy` | `Market_Cap / CapRealUSD` | CoinMetrics | Proxy: uses realized cap from CoinMetrics |
| `active_address_growth` | `ln(AdrActCnt_t / AdrActCnt_{t-30})` | CoinMetrics | No |
| `tvl_ratio` | `TVL_USD / Market_Cap` | DeFiLlama | Proxy: only available for DeFi protocols |

**Implementation:** `features/feature_engineering.py::compute_nvt_ratio()`, `compute_mvrv_proxy()`, `compute_active_address_growth()`, `compute_tvl_ratio()`

### 2.6 Cross-Sectional Normalization

All features are normalized cross-sectionally at each date `t`:

```
Z_{i,t} = (X_{i,t} - mean_t(X)) / std_t(X)
```

Where `mean_t` and `std_t` are computed across all assets in the universe at date `t`.

**Implementation:** `features/feature_engineering.py::cross_sectional_zscore()`

---

## 3. Labels (`data/labels/labels_7d.parquet`)

| Label | Formula | Horizon |
|---|---|---|
| `fwd_return_7d` | `ln(P_{t+7} / P_t)` | 7 days |
| `fwd_return_14d` | `ln(P_{t+14} / P_t)` | 14 days |
| `fwd_return_30d` | `ln(P_{t+30} / P_t)` | 30 days |

**Look-ahead prevention:** The final `horizon` days of each symbol's time series are dropped (they would require future prices not yet available).

**Implementation:** `agents/label_agent.py`

---

## 4. Model Outputs (`data/predictions/predictions_*.parquet`)

| Column | Description |
|---|---|
| `symbol` | Asset ticker |
| `date_ts` | Prediction date |
| `predicted_score` | Model output (continuous rank score) |
| `model_name` | `lightgbm` or `random_forest` |
| `horizon` | Label horizon in days |
| `fold_id` | Walk-forward fold index |
| `snapshot_id` | Run identifier |

---

## 5. Walk-Forward Cross-Validation

The model is trained using **purged + embargoed expanding walk-forward CV**:

```
Train window: [t_0, t_train_end]
Embargo gap:  [t_train_end, t_train_end + embargo_days]   ← dropped
Test window:  [t_train_end + embargo_days, t_test_end]
```

- **Purging:** Samples whose label period overlaps with the test period are removed from training.
- **Embargo:** A 7-day gap between train end and test start prevents leakage via autocorrelated features.
- **Expanding window:** Each fold adds more training data; test window is fixed at 90 days.

**Implementation:** `models/walk_forward.py::WalkForwardValidator`

---

## 6. Evaluation Metrics

### 6.1 Rank IC (Information Coefficient)

```
Rank_IC = Spearman(predicted_rank, realized_rank)
```

Computed per fold and averaged. Target: `|IC| > 0.02`.

### 6.2 Hit Rate

```
Hit_Rate = mean(sign(predicted_score) == sign(realized_return))
```

### 6.3 Portfolio Performance Metrics

| Metric | Formula |
|---|---|
| CAGR | `(Final_Value / Initial_Value)^(252/N_days) - 1` |
| Annualized Vol | `std(daily_ret) × √252` |
| Sharpe | `(CAGR - rf) / Ann_Vol` where `rf = 0` for crypto |
| Sortino | `(CAGR - rf) / Downside_Vol` |
| Calmar | `CAGR / |Max_Drawdown|` |
| Max Drawdown | `max((Peak - Trough) / Peak)` |

---

## 7. Portfolio Allocation

### 7.1 Top-K Equal Weight

Select top-K assets by predicted score. Assign equal weight `1/K` to each.

```
w_i = 1/K  if rank(score_i) <= K  else  0
```

### 7.2 Score-Proportional

Normalize positive scores to sum to 1:

```
w_i = max(score_i, 0) / sum(max(score_j, 0) for j in universe)
```

**Implementation:** `agents/portfolio_agent.py`

---

## 8. Transaction Costs

Applied at each rebalance date:

```
cost = portfolio_value × turnover × (cost_bps / 10000)
turnover = sum(|w_i_new - w_i_old|) for all i
```

Default: 20 bps (0.20%). Sweep range: 10, 20, 30, 50 bps.

---

## 9. Benchmarks

| Benchmark | Description |
|---|---|
| BTC buy-and-hold | 100% allocation to BTC, no rebalancing |
| EW Top-100 | Equal weight all universe assets, monthly rebalanced, 20 bps cost |

**Implementation:** `agents/backtest_agent.py::_run_btc_benchmark()`, `_run_ew_top100_benchmark()`

---

## 10. Ablation Study

Two model variants are compared to isolate the marginal value of on-chain features:

| Variant | Features Used |
|---|---|
| `market_only` | momentum_7d, momentum_14d, momentum_30d, momentum_90d, volatility_30d, beta_60d, skewness_30d, turnover_ratio |
| `market_plus_onchain` | All market features + nvt_ratio, mvrv_proxy, active_address_growth, tvl_ratio |

**Marginal IC lift** = `IC(market_plus_onchain) - IC(market_only)`

**Implementation:** `models/ablation.py::run_ablation()`

---

## 11. Data Providers

| Provider | Endpoint | Key Required | Rate Limit |
|---|---|---|---|
| CoinGecko | `GET /coins/markets` | No (free tier) | 30 req/min |
| Binance/CCXT | `GET /api/v3/klines` | No | 1200 req/min |
| CoinMetrics | `GET /timeseries/asset-metrics` | No (community) | 10 req/min |
| DeFiLlama | `GET /protocols`, `GET /tvl/{protocol}` | No | 300 req/min |

All providers implement exponential backoff with configurable `max_retries` and `retry_backoff_base`.

---

*Generated by CHF v1.0 — Last updated: 2026-04-07*
