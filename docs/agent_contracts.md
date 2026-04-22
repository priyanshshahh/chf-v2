# CHF Agent Contracts

This repo implements deterministic pipeline workers, not autonomous AI agents. Each stage follows the `AgentBase` lifecycle:

`prepare -> run -> persist`

The public pipeline order is:

`universe -> market -> onchain -> clean -> features -> labels -> models -> portfolio -> backtest`

## UniverseAgent

- Purpose: Build the eligible monthly crypto universe from CoinGecko.
- Upstream inputs: `configs/run_config.yaml`
- Output artifacts:
  - `data/raw/universe/universe_YYYYMM.parquet`
  - `data/raw/universe/exclusions_YYYYMM.parquet`
  - `data/raw/universe/snapshot_meta_YYYYMM.json`
- Core logic: fetch top-N assets, filter stablecoins/wrapped assets, persist eligibility snapshot.
- Failure modes: upstream API failure, invalid config, empty universe snapshot.
- Done when: latest universe parquet and metadata JSON exist and contain eligible assets.

## MarketDataAgent

- Purpose: Fetch daily OHLCV history for the eligible universe plus benchmarks.
- Upstream inputs:
  - latest universe snapshot, or fallback benchmark list
  - `market_data` config
- Output artifacts:
  - `data/raw/market/{SYMBOL}_ohlcv.parquet`
  - `data/raw/market/year=YYYY/month=MM/{SYMBOL}.parquet`
  - `data/raw/market/qa_report.parquet`
  - `data/raw/market/manifest.json`
- Core logic: fetch backfill or incremental OHLCV, persist both flat and hive layouts, emit QA report.
- Failure modes: exchange/API failure, no symbols, empty fetches, corrupted parquet writes.
- Done when: at least one symbol parquet exists and QA report/manifest are written.

## OnChainAgent

- Purpose: Collect daily on-chain features from CoinMetrics and DeFiLlama.
- Upstream inputs:
  - latest universe snapshot
  - `on_chain` config
- Output artifacts:
  - `data/raw/onchain/{SYMBOL}_onchain.parquet`
  - `data/raw/onchain/coverage_report.parquet`
  - `data/raw/onchain/manifest.json`
- Core logic: fetch source-specific metrics, outer-merge by `date_ts`/`symbol`, persist symbol files and coverage summary.
- Failure modes: missing provider coverage, API failures, empty merged frames.
- Done when: symbol parquet files or an explicit empty-onchain fallback path exist and coverage report is updated.

## Clean Stage

- Purpose: Normalize raw market and on-chain inputs before feature engineering.
- Upstream inputs:
  - `data/raw/market/*`
  - `data/raw/onchain/*`
- Output artifacts:
  - `data/cleaned/*_ohlcv_clean.parquet`
  - `data/cleaned/*_onchain_clean.parquet`
- Core logic: enforce timestamp normalization, deduplicate, repair small gaps where supported, and stage cleaned files for downstream use.
- Failure modes: missing raw inputs, malformed date columns, invalid OHLCV rows.
- Done when: cleaned parquet files exist or the stage explicitly falls back to raw files downstream.

## FeatureAgentV1

- Purpose: Build market-derived features from OHLCV.
- Upstream inputs:
  - cleaned OHLCV if available, otherwise raw OHLCV
- Output artifacts:
  - `data/features/market_features.parquet`
  - `data/features/feature_dictionary.json`
- Core logic: compute returns, volatility, skew, beta, volume ratios, reversal, ATR proxy, then winsorize and cross-sectionally z-score.
- Failure modes: missing OHLCV files, missing benchmark BTC history, empty concatenation.
- Done when: `market_features.parquet` exists with one row per `symbol/date_ts` and numeric feature columns.

## FeatureAgentV2

- Purpose: Merge on-chain features into the market feature store and emit final feature selection metadata.
- Upstream inputs:
  - `data/features/market_features.parquet`
  - raw on-chain symbol files
- Output artifacts:
  - `data/features/full_features.parquet`
  - `data/features/feature_keep_list.json`
- Core logic: compute on-chain transforms, merge onto market features, run correlation-based redundancy pruning, persist final feature store.
- Failure modes: missing market feature store, malformed on-chain files, empty merged features.
- Done when: `full_features.parquet` exists even if the run falls back to market-only features.

## LabelAgent

- Purpose: Generate leakage-safe forward return targets.
- Upstream inputs:
  - cleaned OHLCV if available, otherwise raw OHLCV
- Output artifacts:
  - `data/labels/labels_{horizon}d.parquet`
  - `data/labels/label_metadata.json`
- Core logic: compute `ln(P[t+h] / P[t])` per symbol for configured horizons and drop incomplete tails.
- Failure modes: missing OHLCV, malformed timestamps, empty symbol histories.
- Done when: horizon parquet files exist and contain `label_value`.

## ModelAgent

- Purpose: Train tabular models with walk-forward validation and persist predictions/metrics.
- Upstream inputs:
  - `data/features/full_features.parquet` or `market_features.parquet`
  - `data/labels/labels_{horizon}d.parquet`
- Output artifacts:
  - `data/predictions/predictions_{model}_h{horizon}d.parquet`
  - `data/predictions/metrics_{model}_h{horizon}d.json`
  - `artifacts/models/{model}_h{horizon}d.pkl`
  - MLflow artifacts under `artifacts/` and `mlruns/`
- Core logic: join features/labels, pick numeric feature columns, run purged walk-forward validation, save predictions and metrics.
- Failure modes: missing features/labels, no valid splits, model dependency missing, MLflow logging failure.
- Done when: at least one prediction parquet and metrics JSON are written for the requested model/horizon.

## PortfolioAgent

- Purpose: Turn predictions into rebalance weights and transaction logs.
- Upstream inputs:
  - prediction parquet for the chosen model/horizon
  - raw market data for liquidity filtering
- Output artifacts:
  - `data/allocations/allocations_{strategy}.parquet`
  - `data/allocations/allocations_top_{k}_equal_weight.parquet`
  - `data/allocations/allocations_transaction_log.parquet`
  - `data/allocations/latest_allocation.parquet`
- Core logic: filter to latest available predictions at each rebalance date, apply liquidity and positive-signal filters, compute weights, and track turnover.
- Failure modes: missing predictions, no positive signals, empty rebalance dates, missing market data for liquidity checks.
- Done when: at least one allocation parquet exists and `latest_allocation.parquet` is updated.

## BacktestAgent

- Purpose: Evaluate the allocation strategy against benchmarks with transaction costs.
- Upstream inputs:
  - allocation parquet files with `symbol/date_ts/weight`
  - raw market OHLCV
- Output artifacts:
  - `data/backtests/equity_curves.parquet`
  - `data/backtests/backtest_summary.parquet`
  - `data/backtests/backtest_summary.json`
  - `data/backtests/vbt_stats.json`
- Core logic: run the main strategy, BTC benchmark, equal-weight benchmark, cost sweeps, K sweeps, and subperiod tests.
- Failure modes: no allocation files, no market prices, vectorbt unavailable, malformed allocation artifacts.
- Done when: `backtest_summary.parquet` exists and includes populated risk/return metrics.

## Demo Mode

- Purpose: Emit canonical synthetic artifacts for dashboard loading and offline acceptance checks.
- Entry point: `python main.py demo`
- Output artifacts:
  - canonical raw/features/labels/predictions/allocations/backtests files matching the live pipeline naming conventions
- Done when: dashboard/API loaders can read the generated files without needing special-case paths.
