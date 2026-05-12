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

### 1.2 Universe (`data/raw/universe/universe_YYYYMM.parquet`)

Monthly snapshot built by `UniverseAgent` from CoinGecko `/coins/markets` (+ per-coin metadata for categories).

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Ticker symbol (uppercased) |
| `coingecko_id` | str | CoinGecko coin id |
| `name` | str | Full asset name |
| `rank` | int | CoinGecko market cap rank |
| `market_cap_usd` | float64 | Market cap at snapshot time |
| `volume_24h_usd` | float64 | 24h volume at snapshot time |
| `categories` | object | Category list (may be empty) |
| `is_stablecoin` | bool | Stablecoin heuristic flag |
| `is_wrapped` | bool | Wrapped/synthetic heuristic flag |
| `is_excluded` | bool | True if filtered out |
| `exclusion_reason` | str | Reason string when excluded |
| `retrieved_at` | datetime[str] | UTC timestamp string when fetched |
| `snapshot_id` | str | Snapshot identifier |
| `run_id` | str | Agent run identifier |
| `source` | str | Data source tag (e.g. `coingecko`) |

### 1.3 On-Chain (`data/raw/onchain/SYMBOL_onchain.parquet`)

| Column | Type | Description |
|---|---|---|
| `symbol` | str | Ticker symbol |
| `date_ts` | datetime[UTC] | UTC midnight timestamp |
| `AdrActCnt` | float64 | Daily active addresses (CoinMetrics) |
| `TxCnt` | float64 | Daily transaction count (CoinMetrics) |
| `CapRealUSD` | float64 | Realized market cap in USD (CoinMetrics) |
| `CapMVRVCur` | float64 | MVRV ratio (CoinMetrics, if available) |
| `TxTfrValAdjUSD` | float64 | Adjusted transfer volume in USD (CoinMetrics, if available) |
| `FeeTotUSD` | float64 | Total fees in USD (CoinMetrics, if available) |
| `NVTAdj` | float64 | NVT adjusted (CoinMetrics, if available) |
| `tvl_usd` | float64 | Total Value Locked in USD (DeFiLlama, if available) |
| `fees_usd` | float64 | Protocol fees in USD (DeFiLlama, if available) |
| `dex_volume_usd` | float64 | DEX volume in USD (DeFiLlama, if available) |

Sources: CoinMetrics Community API, DeFiLlama.

---

## 2. Feature Store (`data/features/market_features.parquet`, `data/features/full_features.parquet`)

CHF writes two feature-store files:

- `market_features.parquet` from `FeatureAgentV1` (market-derived)
- `full_features.parquet` from `FeatureAgentV2` (market + on-chain merge + pruning metadata)

### 2.1 Market Features (FeatureAgentV1)

All are computed per-symbol and aligned “as of” `date_ts` (no look-ahead):

| Feature | Formula / Meaning |
|---|---|
| `ret_{n}d` | `ln(P_t / P_{t-n})` for `n` in `features.return_windows` |
| `vol_{w}d` | rolling `std(daily_log_return, w) * sqrt(365)` |
| `skew_{w}d` | rolling skewness of daily log returns |
| `beta_btc_{w}d` | `Cov(R_i, R_BTC) / Var(R_BTC)` over window `w` |
| `vol_ratio_{w}d` | rolling mean volume / overall mean volume |
| `reversal_3_30` | short-term return minus long-term return |
| `atr_14d` | ATR proxy: rolling mean of `(high-low)/close` |

Cross-sectional z-scores are appended with a `_cs` suffix (example: `ret_7d_cs`) when enabled by `features.zscore_cross_sectional`.

Implementation: `agents/feature_agent.py::FeatureAgentV1` and `features/feature_engineering.py`.

### 2.2 On-Chain Features (FeatureAgentV2)

These are computed from `data/raw/onchain/*` and merged onto the market feature store:

| Feature | Meaning |
|---|---|
| `adr_growth_30d` | `ln(AdrActCnt_t / AdrActCnt_{t-30})` |
| `tx_growth_30d` | `ln(TxCnt_t / TxCnt_{t-30})` |
| `nvt_ratio` | network value to transactions proxy (price / TxTfrValAdjUSD) |
| `nvt_signal_90d` | rolling mean of `nvt_ratio` (90d) |
| `mvrv_proxy` | proxy from CoinMetrics `CapMVRVCur`, else computed from realized cap |
| `realized_cap_change_30d` | `ln(CapRealUSD_t / CapRealUSD_{t-30})` |
| `fee_intensity` | fees scaled by a market-cap proxy |
| `tvl_ratio` | TVL scaled by a market-cap proxy |
| `tvl_growth_30d` | `ln(TVL_t / TVL_{t-30})` |

### 2.3 Feature Dictionary / Keep List

- `data/features/feature_dictionary.json`: human-readable definitions (from `features.feature_engineering.FEATURE_DICTIONARY`)
- `data/features/feature_keep_list.json`: correlation-cluster pruning output (written by `FeatureAgentV2`)

---

## 3. Labels (`data/labels/labels_{horizon}d.parquet`)

Labels are stored in long format with one numeric target:

`label_value = ln(P_{t+h} / P_t)`

| Column | Description |
|---|---|
| `symbol` | Asset ticker |
| `date_ts` | Feature/label “as of” timestamp |
| `horizon_days` | Horizon `h` in days |
| `label_value` | Forward log return |
| `label_type` | Always `log_return` in the current implementation |
| `is_complete` | True when `P_{t+h}` exists (incomplete tails are optionally dropped) |
| `snapshot_id` | Snapshot identifier |
| `run_id` | Agent run identifier |

Implementation: `agents/label_agent.py::LabelAgent`

---

## 4. Model Outputs (`data/predictions/predictions_*.parquet`)

| Column | Description |
|---|---|
| `symbol` | Asset ticker |
| `date_ts` | Prediction date |
| `predicted_return` | Model output (regression prediction for the configured label) |
| `actual_return` | Realized label value for the fold (validation rows only) |
| `model_name` | `lightgbm` or `random_forest` |
| `horizon_days` | Label horizon in days |
| `fold_id` | Walk-forward fold index |
| `model_version` | Model code version tag (string) |
| `feature_version` | Feature version tag (string) |
| `snapshot_id` | Snapshot identifier |
| `run_id` | Agent run identifier |

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
Hit_Rate = mean(sign(predicted_return) == sign(actual_return))
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

Select top-K assets by `predicted_return`. Assign equal weight `1/K` to each.

```
w_i = 1/K  if rank(score_i) <= K  else  0
```

### 7.2 Score-Proportional

Normalize positive `predicted_return` values to sum to 1:

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
