# CHF — Crypto Hedge Fund Portfolio System

A production-grade, config-driven, agent-based crypto portfolio management system with ML/AI signal generation, vectorized backtesting, and a Streamlit analytics dashboard.

---

## Architecture Overview

```
chf/
├── agents/                   # Agent layer (each agent: prepare → run → persist)
│   ├── base.py               # AgentBase with retry, SQLite registry, snapshots
│   ├── universe_agent.py     # CoinGecko universe construction
│   ├── market_data_agent.py  # CCXT/Binance OHLCV ingestion
│   ├── onchain_agent.py      # CoinMetrics + DeFiLlama on-chain data
│   ├── feature_agent.py      # FeatureAgentV1 (market) + V2 (on-chain)
│   ├── label_agent.py        # Forward-return label generation
│   ├── model_agent.py        # Walk-forward ML training + MLflow
│   ├── portfolio_agent.py    # Portfolio allocation strategies
│   └── backtest_agent.py     # Vectorized backtesting engine
├── configs/
│   ├── run_config.yaml       # Master configuration (all parameters)
│   ├── config.py             # Config loader with env overrides
│   └── logging_config.py     # Structured JSON logging
├── features/
│   └── feature_engineering.py  # All feature math with explicit formulas
├── models/
│   └── walk_forward.py       # Purged/embargoed walk-forward CV
├── pipelines/
│   ├── pipeline_runner.py    # Full pipeline orchestrator + CLI
│   ├── data_cleaner.py       # OHLCV and on-chain data cleaning
│   └── duckdb_engine.py      # DuckDB analytics engine
├── providers/
│   ├── coingecko.py          # CoinGecko REST API provider
│   ├── ccxt_binance.py       # CCXT/Binance OHLCV provider
│   ├── coinmetrics.py        # CoinMetrics Community API provider
│   └── defillama.py          # DeFiLlama TVL provider
├── app/
│   ├── dashboard.py          # Streamlit dashboard (6 views)
│   └── api.py                # FastAPI REST endpoints
├── jobs/
│   └── scheduler.py          # APScheduler cron jobs
├── schemas/
│   └── schemas.py            # Pydantic data models
├── tests/
│   └── smoke_test.py         # End-to-end smoke test
├── data/                     # Pipeline outputs (auto-created)
│   ├── raw/                  # Raw ingested data
│   ├── cleaned/              # Cleaned/staged data
│   ├── features/             # Feature store (Parquet)
│   ├── labels/               # Forward-return labels
│   ├── predictions/          # Model predictions
│   ├── allocations/          # Portfolio weights
│   ├── backtests/            # Backtest results
│   └── reports/              # Alpha and research reports
├── artifacts/                # MLflow artifacts, models, SHAP
├── metadata/                 # SQLite agent run registry
├── mlruns/                   # MLflow experiment tracking
├── requirements.txt
├── pyproject.toml
└── run_dashboard.sh          # One-click dashboard launcher
```

---

## Quick Start

### 1. Install Dependencies

```bash
cd ~/Desktop/manus\ chf/chf
pip3 install -r requirements.txt
```

### 2. Configure API Keys (Optional)

```bash
cp .env.example .env
# Edit .env to add your API keys:
# COINGECKO_API_KEY=your_key
# COINMETRICS_API_KEY=your_key
```

### 3. Run the Smoke Test (Synthetic Data)

```bash
python3 tests/smoke_test.py
```

### 4. Launch the Dashboard

```bash
./run_dashboard.sh
# or:
streamlit run app/dashboard.py
```

Open: **http://localhost:8501**

### 5. Run the Full Pipeline

```bash
# Full pipeline (requires API keys for live data)
python3 pipelines/pipeline_runner.py --full

# Individual stages
python3 pipelines/pipeline_runner.py --stage universe
python3 pipelines/pipeline_runner.py --stage market_data
python3 pipelines/pipeline_runner.py --stage features
python3 pipelines/pipeline_runner.py --stage models
python3 pipelines/pipeline_runner.py --stage backtest
```

---

## Dashboard Views

| View | Description |
|------|-------------|
| 🌐 Universe Explorer | Market cap, volume, category filters for all tracked assets |
| 📡 Signal Monitor | Latest model signals, Rank IC over time, feature heatmaps |
| ⚖️ Portfolio Weights | Current allocations, weight history, transaction log |
| 📊 Backtest Analytics | Equity curves, drawdowns, cost sweeps, K sweeps |
| 🤖 Model Diagnostics | Feature importance, walk-forward IC, fold metrics |
| ⚙️ Pipeline Control | Manual triggers, run history, config viewer |

---

## Feature Engineering

All features are mathematically defined with no look-ahead leakage:

| Feature | Formula | Family |
|---------|---------|--------|
| `ret_{n}d` | `ln(P_t / P_{t-n})` | Market |
| `vol_30d` | `std(daily_ret, 30) × √365` | Market |
| `skew_30d` | `skewness(daily_ret, 30)` | Market |
| `beta_btc_60d` | `Cov(R_i, R_BTC) / Var(R_BTC)` | Market |
| `vol_ratio_30d` | `MA(Volume, 30) / mean(Volume)` | Market |
| `reversal_3_30` | `ret_3d - ret_30d` | Market |
| `nvt_ratio` | `Market_Cap / TxTfrValAdjUSD` | On-Chain |
| `mvrv_proxy` | `Market_Cap / CapRealUSD` | On-Chain |
| `adr_growth_30d` | `ln(AdrActCnt_t / AdrActCnt_{t-30})` | On-Chain |
| `tvl_ratio` | `TVL_USD / Market_Cap` | On-Chain |

---

## ML Pipeline

- **Models**: RandomForest (baseline), LightGBM (advanced)
- **Validation**: Purged + embargoed expanding walk-forward CV
- **Metrics**: Rank IC, Hit Rate, R², IC t-stat
- **Tracking**: Local MLflow experiment tracking (`mlruns/`)
- **Explainability**: SHAP values for tree-based models

---

## Portfolio Strategies

| Strategy | Description |
|----------|-------------|
| `top_k_equal_weight` | Top-K assets by signal, equal weights |
| `score_proportional` | Weights proportional to predicted return |

**Constraints**: Long-only, max weight 10%, positive signal filter, weekly rebalancing.

---

## Backtest Engine

- **Vectorized**: NumPy/Pandas-based, no loops over dates
- **Transaction costs**: Configurable BPS (default: 20bps)
- **Metrics**: CAGR, Sharpe, Sortino, Calmar, Max Drawdown, Turnover
- **Sweeps**: Cost sweep, K sweep, subperiod analysis
- **Benchmarks**: BTC, ETH, equal-weight top-100
- **Alpha report**: benchmark-relative regression alpha, beta, information ratio, verdict

---

## Configuration

All parameters in `configs/run_config.yaml`. Key sections:

```yaml
universe:
  top_n: 100
  min_daily_volume_usd: 1_000_000

features:
  return_windows: [3, 7, 14, 30, 90]
  volatility_window: 30
  beta_window: 60

modeling:
  models: [random_forest, lightgbm]
  walk_forward:
    initial_train_months: 12
    step_months: 1
    embargo_days: 7

portfolio:
  strategies: [top_k_equal_weight, score_proportional]
  default_top_k: 10
  max_weight: 0.10

backtesting:
  initial_capital: 100_000
  transaction_cost_bps: 20
```

---

## Scheduler (APScheduler)

```bash
python3 jobs/scheduler.py
```

Default schedule:
- Universe update: 1st of month, 02:00 UTC
- Market data: daily, 06:00 UTC
- On-chain data: daily, 07:00 UTC
- Features: daily, 08:00 UTC
- Models: 1st of month, 10:00 UTC
- Portfolio: every Monday, 12:00 UTC
- Backtest: 1st of month, 14:00 UTC

---

## Data Sources

| Source | Data | API |
|--------|------|-----|
| CoinGecko | Universe, prices, market cap | Free + Pro |
| CCXT/Binance | OHLCV, order book | Free |
| CoinMetrics Community | NVT, MVRV, active addresses | Free |
| DeFiLlama | TVL, protocol metrics | Free |

---

## License

MIT License. For research and educational purposes.
