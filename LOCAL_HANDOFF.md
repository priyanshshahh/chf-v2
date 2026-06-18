# CHF — Local Handoff Guide

This document tells you exactly what to run, in what order, and what to expect.
Everything here has been tested on macOS with Python 3.11 (Anaconda).

---

## 1. Prerequisites

| Requirement | Version | Check |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| pip | latest | `pip3 --version` |
| Internet access | — | For live API calls |

---

## 2. First-Time Setup

```bash
cd /path/to/chf

# Option A (recommended): create a local venv + install deps
make setup

# Option B: install into your current Python environment
# pip install -r requirements.txt

# Run bootstrap (creates directories, copies .env)
python scripts/bootstrap.py
```

Expected output from bootstrap:
```
[1/4] Creating data directories...   [OK]
[2/4] Checking .env file...          [OK] Copied .env.example -> .env
[3/4] Checking Python packages...    [OK] all packages
[4/4] Checking project modules...    [OK] all modules
Bootstrap complete. Ready to run!
```

---

## 3. Run the Smoke Test (No API Keys Needed)

```bash
cd /path/to/chf
python scripts/smoke_test.py
```

Expected output:
```
[1] Testing imports...          [PASS] all 15 modules
[2] Testing feature engineering [PASS] log returns, vol, beta, skewness, turnover
[3] Testing walk-forward CV     [PASS] 3 folds, no overlap
[4] Testing ablation study      [PASS] market_only + market_plus_onchain
[5] Testing hive partitioning   [PASS] year=YYYY/month=MM layout
[6] Testing VectorBT            [PASS] portfolio creation, total_return
[7] Testing DuckDB engine       [PASS] query execution
[8] Testing BacktestAgent       [PASS] summary + equity curves

PASSED: 30+  |  FAILED: 0
ALL TESTS PASSED ✅
```

---

## 4. Demo Mode (No API Keys — Synthetic Data)

```bash
cd /path/to/chf
python main.py demo
streamlit run app/dashboard.py
```

Opens the dashboard at http://localhost:8501 with synthetic data for all 6 views.

---

## 5. Live Pipeline (Requires API Keys)

### 5.1 Configure API Keys

```bash
cp .env.example .env
# Edit .env and add:
COINGECKO_API_KEY is optional; free tier works without a key.
COINMETRICS_API_KEY is optional; community tier works without a key.
```

### 5.2 Run Full Pipeline

```bash
# Option A: Single command
python main.py full

# Option B: Stage by stage
python main.py universe    # ~2 min — fetches top-100 from CoinGecko
python main.py market      # ~15 min — fetches 2yr OHLCV from Binance
python main.py onchain     # ~10 min — fetches on-chain from CoinMetrics + DeFiLlama
python main.py features    # ~2 min — builds feature store
python main.py labels      # ~1 min — generates forward-return labels
python main.py models      # ~5 min — trains RF + LightGBM, logs to MLflow
python main.py portfolio   # ~1 min — builds portfolio allocations
python main.py backtest    # ~2 min — runs vectorbt backtest

# Option C: Shell script
./run_all.sh
```

---

## 6. Ablation Study

```bash
# Must run features + labels first
python main.py ablation
```

Output: `data/reports/ablation_results.json`

```json
{
  "market_only": {"mean_rank_ic": 0.028, "n_features": 8},
  "market_plus_onchain": {"mean_rank_ic": 0.041, "n_features": 12},
  "onchain_marginal_ic_lift": 0.013,
  "onchain_features_help": true
}
```

---

## 7. Dashboard

```bash
streamlit run app/dashboard.py
```

| Tab | What it shows | Data required |
|---|---|---|
| Universe Explorer | Top-100 assets, market cap, filters | `data/raw/universe/` |
| Signal Monitor | Feature heatmap, cross-sectional z-scores | `data/features/` |
| Portfolio Weights | Current allocation pie + history | `data/allocations/` |
| Backtest Analytics | Equity curves, Sharpe, Calmar vs benchmarks | `data/backtests/` |
| Model Diagnostics | Rank IC per fold, feature importance, SHAP | `data/predictions/` + MLflow |
| Pipeline Control | One-click stage triggers, logs | — |

---

## 8. MLflow UI

```bash
mlflow ui --backend-store-uri mlruns --port 5000
```

Opens at http://localhost:5000. Shows all model runs, hyperparameters, Rank IC, Hit Rate.

---

## 9. Architecture

```
data/raw/market/year=YYYY/month=MM/SYMBOL.parquet   ← hive-partitioned OHLCV
data/raw/universe/universe_YYYYMMDD.parquet
data/raw/onchain/SYMBOL_onchain.parquet
data/features/feature_store_*.parquet
data/labels/labels_*.parquet
data/predictions/predictions_*.parquet
data/allocations/allocations_*.parquet
data/backtests/equity_curves.parquet
data/backtests/backtest_summary.parquet
data/backtests/vbt_stats.json
data/reports/ablation_results.json
mlruns/                                              ← MLflow experiments
docs/architecture.png                                ← DAG diagram
docs/data_dictionary.md                              ← Feature formulas
```

---

## 10. Key Files

| File | Purpose |
|---|---|
| `main.py` | Single CLI entrypoint for all stages |
| `Makefile` | `make demo`, `make full`, `make test`, etc. |
| `run_all.sh` | Full pipeline shell script |
| `scripts/bootstrap.py` | First-time environment setup |
| `scripts/smoke_test.py` | Offline end-to-end validation |
| `configs/run_config.yaml` | All tunable parameters |
| `docs/architecture.png` | System DAG diagram |
| `docs/data_dictionary.md` | All feature formulas |
| `models/ablation.py` | Ablation study (on-chain marginal value) |

---

## 11. Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: vectorbt` | `pip install vectorbt` |
| `ModuleNotFoundError: numba` | `pip install numba` |
| `ModuleNotFoundError: lightgbm` | `pip install lightgbm` |
| `ModuleNotFoundError: duckdb` | `pip install duckdb` |
| Dashboard shows empty state | Run `python main.py demo` first |
| CoinGecko rate limit | Add `COINGECKO_API_KEY` to `.env` |
| MLflow not tracking | Check `mlruns/` directory exists |
| Numba JIT slow first run | Normal — kernel is compiled on first call, cached after |

---

*CHF v1.0 — Built for macOS/Linux, Python 3.11+*
