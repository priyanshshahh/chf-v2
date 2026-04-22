# CHF Clean-Run Checklist

Use this after a clean checkout or after major pipeline refactors.

## Setup

- `pip install -r requirements.txt`
- `cp .env.example .env`
- Optional: add `COINGECKO_API_KEY` or `COINMETRICS_API_KEY` if you need higher rate limits.

## Offline Acceptance Path

1. Run `python3 main.py demo`
   Expected artifacts:
   - `data/raw/market/BTC_ohlcv.parquet`
   - `data/features/full_features.parquet`
   - `data/labels/labels_7d.parquet`
   - `data/predictions/predictions_lightgbm_h7d.parquet`
   - `data/allocations/latest_allocation.parquet`
   - `data/backtests/backtest_summary.parquet`

2. Run `python3 scripts/smoke_test.py`
   Expected:
   - all checks pass
   - exit code `0`

3. Run `pytest -q`
   Expected:
   - tests are collected
   - demo artifact contract test passes
   - pipeline integration test passes

## Stage-by-Stage Research Pipeline

1. `python3 main.py universe`
   Expected:
   - `data/raw/universe/universe_*.parquet`
   - `data/raw/universe/snapshot_meta_*.json`

2. `python3 main.py market`
   Expected:
   - `data/raw/market/*_ohlcv.parquet`
   - `data/raw/market/qa_report.parquet`

3. `python3 main.py onchain`
   Expected:
   - `data/raw/onchain/*_onchain.parquet` or a logged no-data fallback
   - `data/raw/onchain/coverage_report.parquet`

4. `python3 main.py features`
   Expected:
   - `data/features/market_features.parquet`
   - `data/features/full_features.parquet`
   - `data/features/feature_keep_list.json`

5. `python3 main.py labels`
   Expected:
   - `data/labels/labels_7d.parquet`
   - `data/labels/labels_14d.parquet`
   - `data/labels/labels_30d.parquet`

6. `python3 main.py models`
   Expected:
   - `data/predictions/predictions_random_forest_h7d.parquet`
   - `data/predictions/predictions_lightgbm_h7d.parquet`
   - metrics JSON files in `data/predictions/`

7. `python3 main.py portfolio`
   Expected:
   - `data/allocations/allocations_top_k_equal_weight.parquet`
   - `data/allocations/latest_allocation.parquet`
   - `data/allocations/allocations_transaction_log.parquet`

8. `python3 main.py backtest`
   Expected:
   - `data/backtests/equity_curves.parquet`
   - `data/backtests/backtest_summary.parquet`
   - `data/backtests/backtest_summary.json`

## Full Pipeline Shortcut

- `./run_all.sh`
  Expected:
  - commands complete in stage order
  - backtest summary exists at the end

## Dashboard and API Verification

- Dashboard: `streamlit run app/dashboard.py`
  Expected:
  - universe, signal, portfolio, and backtest views render without file-not-found issues

- API: `python3 main.py serve`
  Expected:
  - `/health` returns `200`
  - `/weights` returns current allocation data when `latest_allocation.parquet` exists
  - `/signals` returns latest predictions when prediction parquet exists
  - `/metrics` returns backtest summary rows when summary parquet exists
