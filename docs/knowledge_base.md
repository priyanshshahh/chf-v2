# CHF Knowledge Base

This document is the single, code-accurate reference for how CHF works today: what runs what, where outputs land, and where to change things safely.

If you are new, start here, then jump into the linked deep-dive docs.

## What CHF Is

CHF is a local-first, config-driven crypto research and portfolio automation system:

- Deterministic ingestion and feature engineering (no LLMs in the prediction core)
- Walk-forward ML training with leakage controls
- Portfolio construction + vectorized backtesting
- A Streamlit dashboard and optional FastAPI read-only API
- A simple scheduler for cron-style automation

The “agents” in this repo are pipeline workers: `prepare -> run -> persist`. See [docs/agent_contracts.md](./agent_contracts.md).

## Primary Entry Points

- `python main.py <command>`: single CLI for all stages and helpers (including `demo`, `serve`, `schedule`)
- `pipelines/pipeline_runner.py`: orchestration helper for running the full DAG (and a `--stage` CLI)
- `Makefile`: convenient wrappers (`make demo`, `make full`, `make dashboard`, `make smoke`, etc.)

## Pipeline DAG (Actual Run Order)

Full pipeline order (as implemented in `pipelines/pipeline_runner.py`):

`universe -> market_data -> onchain -> clean -> features -> labels -> models -> portfolio -> backtest`

Notes:

- `features` is two agents in sequence: `FeatureAgentV1` (market) then `FeatureAgentV2` (merge on-chain + prune).
- `models` loops over configured horizons and model names.

Architecture diagram source: [docs/architecture.mmd](./architecture.mmd) (PNG is a rendered snapshot).

## Storage Layout (Source of Truth)

All paths are defined in `configs/run_config.yaml` under `paths:` and resolved relative to the project root by `configs/config.py::resolve_path`.

Canonical directories:

- `data/raw/`
- `data/cleaned/`
- `data/features/`
- `data/labels/`
- `data/predictions/`
- `data/allocations/`
- `data/backtests/`
- `data/reports/`
- `artifacts/` (model binaries, explainability artifacts)
- `mlruns/` (MLflow tracking store)
- `metadata/agent_registry.db` (SQLite run registry written by `AgentBase`)

## Agent Responsibilities (Where To Look)

Deterministic agent contracts and artifact names are documented here:

- [docs/agent_contracts.md](./agent_contracts.md)

Code locations (high-signal modules):

- `agents/base.py`: lifecycle, retries, snapshot IDs, run registry
- `agents/universe_agent.py`: CoinGecko universe construction
- `agents/market_data_agent.py`: research-mode market ingestion from UniverseAgent-approved assets using CCXT exchange priority (`coinbase -> kraken -> kucoin -> gemini`) before aggregate fallbacks (`cryptocompare -> coingecko -> coincap -> coinpaprika`), with cache-first behavior, sticky per-run rate-limit failover, canonical market outputs, and explicit distinction between full OHLCV and partial close-only data
- `scripts/verify_market_run.py`: strict market artifact validator; it must fail gracefully on missing schema columns and only count `is_full_ohlcv=true` assets toward research coverage thresholds
- `configs/run_config.yaml`: `market_data_smoke` is the safe live proving mode for MarketDataAgent (`max_assets=3`, `backfill_days=60`, `minimum_assets_required=1`) before scaling to the full 100-asset research run
- `agents/onchain_agent.py`: research-mode on-chain ingestion that intersects latest eligible universe assets with market-approved assets (`passed_qa=true` and `is_full_ohlcv=true`), ingests cache-first CoinMetrics Community + DeFiLlama free API data, writes canonical long/wide on-chain artifacts plus coverage/manifest files, and fails loudly when research coverage thresholds are not met
- `scripts/verify_onchain_run.py`: strict on-chain artifact validator for schema, UTC-midnight dates, non-negative metric QA, duplicate detection, and honest coverage thresholds
- `pipelines/data_cleaner.py`: normalization for raw market/on-chain prior to features/labels
- `agents/feature_agent.py`: FeatureAgentV1/V2 + redundancy pruning hooks
- `agents/label_agent.py`: forward-return label generation
- `models/walk_forward.py`: purged + embargoed expanding walk-forward CV
- `agents/model_agent.py`: model training + MLflow + prediction/metric persistence
- `agents/portfolio_agent.py`: strategies and allocation artifact writing
- `agents/backtest_agent.py`: vectorbt backtests + sweeps + benchmark comparisons
- `reports/alpha_analysis.py`: risk-adjusted alpha evaluation + markdown report rendering

## Data Contracts (Schema Cheatsheet)

These are the practical contracts other parts of the system assume. For full definitions, see [docs/agent_contracts.md](./agent_contracts.md) and the code.

Required columns by artifact:

- Market OHLCV: `symbol`, `date_ts`, `open`, `high`, `low`, `close`, `volume`
- Feature store: `symbol`, `date_ts`, plus numeric feature columns
- Labels: `symbol`, `date_ts`, `horizon_days`, `label_value`
- Predictions: `symbol`, `date_ts`, `predicted_return`, `model_name`, `horizon_days`, `fold_id`
- Allocations: `symbol`, `date_ts`, `weight` (plus strategy metadata)
- Equity curves: `date_ts`, `portfolio_value`, `daily_return`, `backtest_name`

Feature/label definitions and formulas:

- [docs/data_dictionary.md](./data_dictionary.md)

## Configuration Surface

Primary configuration file:

- `configs/run_config.yaml`

Loader behavior:

- `configs/config.py::load_config()` loads YAML, then applies a small set of env overrides (`MLFLOW_TRACKING_URI`, `CHF_SEED`, `LOG_LEVEL`).
- Paths are created on-demand when resolved via `resolve_path(cfg, key)`.

## Dashboard + API

Dashboard:

- `app/dashboard.py` reads from disk artifacts under `data/` and is tolerant to missing files (shows empty states with runnable commands).
- Run it with `make dashboard` or `streamlit run app/dashboard.py`.

Optional REST API:

- `app/api.py` exposes read-only endpoints over on-disk artifacts (`/health`, `/signals`, `/weights`, `/metrics`, `/runs`, `/latest_snapshot`).
- Run it with `python main.py serve`.

## Tests and “Known Good” Validation

Fast offline acceptance path (no API keys):

1. `python main.py demo`
2. `python scripts/smoke_test.py`
3. `pytest`

Checklist doc:

- [docs/clean_run_checklist.md](./clean_run_checklist.md)

## How To Extend CHF (Safe Edit Guide)

Common extension points and where to change them:

1. Add a new data provider: implement under `providers/` and call it from the relevant agent (usually `MarketDataAgent` or `OnChainAgent`).
2. Add a new feature: implement math in `features/feature_engineering.py`, then wire it into `agents/feature_agent.py`.
3. Add a new model: implement training/inference in `agents/model_agent.py` and ensure outputs conform to the prediction contract.
4. Add a new strategy: implement in `agents/portfolio_agent.py` and ensure outputs contain `symbol/date_ts/weight`.
5. Add a new dashboard view: implement in `app/dashboard.py` using the existing cached loaders.

When you change artifact schemas, update:

- the agent that writes it
- any downstream readers (often: `app/dashboard.py`, `app/api.py`, and later agents)
- [docs/agent_contracts.md](./agent_contracts.md) and [docs/data_dictionary.md](./data_dictionary.md)

## What This Knowledge Base Does Not Do

- It does not try to “freeze” experiment outputs (`mlruns/`, `artifacts/`, `data/`) that are generated at runtime.
- It does not claim LLM autonomy in the financial core; see [docs/agentic_ai_suggestions.md](./agentic_ai_suggestions.md) for the recommended way to add an agentic control plane above the deterministic pipeline.

## Research Pipeline Notes (April 29, 2026)

Recent pipeline upgrades that now matter for downstream work:

- `UniverseAgent` is the strict gatekeeper for the research asset universe and writes canonical monthly snapshot artifacts under `data/raw/universe/`.
- `MarketDataAgent` now runs Binance-free in research mode, uses Coinbase/Kraken/KuCoin/Gemini plus aggregate fallbacks, and writes canonical outputs under `data/raw/market/`.
- `OnChainAgent` now builds its asset set as:
  `latest eligible universe` intersect `market assets with passed_qa=true and is_full_ohlcv=true`.

Current `OnChainAgent` provider matrix:

- `coinmetrics`: primary network metrics path, cache-first, real persisted observations.
- `defillama`: real chain/protocol fundamentals path, now persisting `chain_tvl_usd` when mapping exists.
- `etherscan`: optional, only active when `ETHERSCAN_API_KEY` is present; otherwise recorded as unavailable.
- `thegraph`: optional, only active when key/subgraph config exists; otherwise recorded as unavailable.
- `blockchair`: optional and disabled by default.
- `dune`: optional and disabled by default unless key/query config exists.

Important behavior:

- Optional providers do not fabricate rows and do not become hard dependencies when keys/config are missing.
- Coverage and manifest files explicitly record provider availability, provider failure reasons, and which providers actually produced rows.
- DeFiLlama protocol mapping is intentionally conservative: curated protocol aliases for protocol tokens, controlled chain aliases for chain TVL, and no blind fuzzy matching.

## Feature Pipeline Notes (April 29, 2026)

`FeatureAgent` has been rebuilt around the canonical research outputs rather than the old per-symbol parquet layout.

Research-mode inputs:

- `data/raw/market/market_ohlcv.parquet`
- `data/raw/onchain/onchain_wide.parquet`
- `data/raw/onchain/onchain_observations.parquet`
- `data/raw/universe/universe_monthly.parquet`
- `data/raw/market/market_manifest.json`
- `data/raw/onchain/onchain_manifest.json`

Important behavior:

- The research path does not read legacy `data/raw/market/*_ohlcv.parquet` or `data/raw/onchain/*_onchain.parquet` files as source-of-truth inputs.
- `market_features.parquet` is the backbone; `full_features.parquet` is built by left-joining lagged on-chain features onto the market calendar.
- On-chain features are lagged by `onchain_lag_days` before join to avoid same-day leakage from slower data publication paths.
- Winsorization is cross-sectional by date, not global across the full sample.
- Cross-sectional z-scores are also computed by date only.
- Raw unpruned outputs are saved, and pruning diagnostics plus the final keep list are written separately.

Canonical feature outputs:

- `data/features/market_features.parquet`
- `data/features/onchain_features.parquet`
- `data/features/full_features.parquet`
- `data/features/full_features_pruned.parquet` when pruning is enabled
- `data/features/feature_coverage_report.parquet`
- `data/features/feature_manifest.json`
- `data/features/feature_dictionary.json`
- `data/features/feature_keep_list.json`
- `data/features/data_quality_features.md`

## Label Pipeline Notes (April 30, 2026)

`LabelAgent` has been rebuilt around the canonical market + feature outputs and no longer uses legacy cleaned/per-symbol OHLCV paths in research mode.

Research-mode inputs:

- `data/raw/market/market_ohlcv.parquet`
- `data/features/full_features.parquet`
- `data/features/full_features_pruned.parquet` when configured and present
- `data/raw/market/market_manifest.json`
- `data/features/feature_manifest.json`

Important behavior:

- Labels are generated as forward log returns aligned back to feature date `t`:
  `ln(close(t+h) / close(t))`.
- The final incomplete horizon rows per symbol are dropped; labels are never forward-filled or clipped.
- Feature inputs are checked for prohibited leakage-style columns before any join.
- `label_matrix.parquet` is the all-horizons-common target table.
- `modeling_dataset.parquet` is built by inner-joining canonical features with `label_matrix` on `symbol + date_ts`.
- Pruned features are used for the default modeling dataset when configured, while an unpruned modeling dataset can also be written for ablation/debugging.
- Manifest metadata includes recommended embargo days and purged walk-forward overlap guidance for downstream modeling.

Canonical label outputs:

- `data/labels/labels_7d.parquet`
- `data/labels/labels_14d.parquet`
- `data/labels/labels_30d.parquet`
- `data/labels/label_matrix.parquet`
- `data/labels/modeling_dataset.parquet`
- `data/labels/modeling_dataset_unpruned.parquet` when enabled
- `data/labels/label_coverage_report.parquet`
- `data/labels/label_manifest.json`
- `data/labels/data_quality_labels.md`

## Modeling And Backtest Notes (April 30, 2026)

The research pipeline now treats `data/labels/modeling_dataset.parquet` as the canonical model input, `data/predictions/model_predictions.parquet` as the canonical portfolio input, and `data/allocations/allocations_from_predictions.parquet` as the canonical backtest input.

Important behavior:

- `ModelAgent` no longer builds its primary training panel by manually merging old feature and label files; it reads the canonical modeling dataset produced by `LabelAgent`.
- Walk-forward validation is expanding-window, purged, and embargoed. No random K-fold or shuffled split is used in the research path.
- Prediction outputs are strictly out-of-sample and ranked cross-sectionally by test date.
- `BacktestAgent` now builds allocations directly from out-of-sample predictions and executes on the next available market date rather than the same signal date.
- `PortfolioAgent` is now the default bridge between `ModelAgent` and `BacktestAgent`. It selects the configured/leaderboard model, turns out-of-sample predictions into executable allocations, records skipped rebalance dates, and enforces next-day execution timing plus weight constraints before backtesting.
- `BacktestAgent` now consumes canonical portfolio allocations as its primary input instead of generating strategy weights internally. Internal weight generation is no longer the default research path.

## CoinMarketCap Historical Universe Notes (May 6, 2026)

CoinMarketCap access has been probed live, and the professor-grade three-year point-in-time universe path is currently blocked by the API plan. Do not run `universe_cmc_3y` as a real research universe until access is upgraded or another verified point-in-time historical listings source is added.

Current CMC readiness facts:

- A CMC key is visible to the Python runtime.
- Recent-window `/v1/cryptocurrency/listings/historical` works inside the current plan's short access window.
- Three-year `/v1/cryptocurrency/listings/historical` is blocked; CMC reports only `1 month` of historical listings access.
- `/v2/cryptocurrency/quotes/historical` is limited to `12 months` of access under the current plan.
- `/v2/cryptocurrency/ohlcv/historical` is unsupported under the current plan and returned CMC error code `1006`.
- Therefore `professor_historical_universe_ready=false`.
- Recommended mode is `latest_survivor_baseline_until_cmc_upgrade`.

Research interpretation:

- Historical quotes or daily market data are not enough to construct a point-in-time Top-N universe.
- CHF needs `/v1/cryptocurrency/listings/historical` over the full research window because universe membership must come from historical active + inactive listings, not today's survivor list.
- Do not fake point-in-time membership by stamping current rankings onto old dates.
- Continue with the latest-survivor/free-provider baseline only with explicit survivorship-bias disclosure.
- The required limitation remains:
  `Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.`

Reference docs:

- [API data readiness audit](./API_DATA_READINESS_AUDIT.md)
- [CMC historical access limitation](./CMC_HISTORICAL_ACCESS_LIMITATION.md)

## Portfolio And Backtest Notes (May 1, 2026)

`PortfolioAgent` now converts canonical out-of-sample model predictions into deterministic research allocations rather than generating placeholder or ad hoc weights.

Important portfolio behavior:

- Canonical portfolio input is `data/predictions/model_predictions.parquet`, with optional model selection from `data/predictions/model_leaderboard.parquet`.
- Canonical portfolio outputs are:
  - `data/allocations/allocations_from_predictions.parquet`
  - `data/allocations/allocation_coverage_report.parquet`
  - `data/allocations/allocation_manifest.json`
  - `data/allocations/data_quality_allocations.md`
- Strategy-specific allocation files are also written for each generated strategy.
- Allocations are deterministic and forecast-driven: prediction ranking, cross-sectional prediction z-scores, inverse-volatility scaling, max-weight caps, execution lag, and turnover control.
- `date_ts` in allocation outputs is the execution date, not the signal date.
- `signal_date` must be strictly earlier than `execution_date` when execution lag is enabled.
- `alpha_gate_passed=false` does not stop allocation creation, but it forces `allocation_mode=diagnostic_not_live_trading`.

Important backtest behavior:

- `BacktestAgent` now consumes canonical portfolio allocations as the primary input and backtests every `strategy_name` present in `allocations_from_predictions.parquet`.
- Canonical backtest inputs are:
  - `data/allocations/allocations_from_predictions.parquet`
  - `data/allocations/allocation_manifest.json`
  - `data/raw/market/market_ohlcv.parquet`
- Canonical backtest outputs are:
  - `data/backtests/equity_curves.parquet`
  - `data/backtests/backtest_summary.parquet`
  - `data/backtests/benchmark_summary.parquet`
  - `data/backtests/strategy_comparison.parquet`
  - `data/backtests/cost_sweep.parquet`
  - `data/backtests/drawdown_series.parquet`
  - `data/backtests/turnover_report.parquet`
  - `data/backtests/alpha_report.json`
  - `data/backtests/alpha_report.md`
  - `data/backtests/backtest_manifest.json`
  - `data/backtests/data_quality_backtest.md`
- Backtests apply target weights from allocation `execution_date` forward and use close-to-close returns with transaction costs charged from realized turnover.
- Missing prices are treated conservatively by zeroing the affected asset weight into cash rather than fabricating returns.
- Benchmarks are built from canonical market data: `BTC`, `ETH`, `BTC_ETH_50_50`, `equal_weight_universe`, and `cash`.
- Benchmark curves are clipped to the canonical allocation window defined by the actual portfolio execution dates; benchmark math is no longer allowed to drift across a wider market history window than the strategy being tested.
- `BTC`, `ETH`, and `BTC_ETH_50_50` must share the same backtest window unless an explicit failure reason is recorded.
- `equal_weight_universe` now excludes assets until they have enough prior history at a rebalance date and zeroes clearly absurd daily price jumps into cash rather than compounding them as if they were tradable returns.
- `data/backtests/benchmark_sanity_report.parquet` is now a required output and records benchmark window, return extremes, valid price coverage, and sanity pass/fail status.
- `alpha_status` in strategy comparison is only `passed` if the strategy beats the equal-weight benchmark on both Sharpe and total return, stays within the allowed drawdown limit, avoids ruin, and also beats or improves on the BTC/ETH 50-50 benchmark on return or Sharpe.
- If the source allocation manifest says the model failed the alpha gate, the backtest still runs but is explicitly marked diagnostic in the outputs.

## Phase 2-Fallback Run Notes (May 7, 2026)

The corrected latest-survivor/free-provider baseline pipeline was run through ModelAgent. A verifier-contract mismatch around the honest no-candidate state was repaired, and `verify_model_run.py` now passes without force-selecting a model.

Stages completed:

- `UniverseAgent`: PASS, verifier PASS.
- `MarketDataAgent`: PASS, verifier PASS.
- `OnChainAgent`: PASS, verifier PASS.
- `FeatureAgent`: PASS, verifier PASS.
- `LabelAgent`: PASS, verifier PASS.
- `ModelAgent`: agent PASS, verifier PASS.
- `PortfolioAgent`: not run.
- `BacktestAgent`: not run.

Important run outputs:

- Universe mode was `latest_snapshot_only`.
- `survivor_only_universe=true`.
- `survivorship_bias_disclosed=true`.
- Market output contained `100,293` rows and `90` persisted/full-OHLCV symbols.
- On-chain output contained `311,666` observations across `43` symbols.
- Feature output contained `100,293` full rows and `57` kept pruned features.
- Label/modeling output contained `97,593` all-horizon rows across `90` symbols.
- Model output contained `470,406` out-of-sample prediction rows across `47` folds.
- No model passed the signal gate.
- Baseline model output remained diagnostic only with `candidate_for_backtest=false`.
- Model manifest now explicitly records:
  - `alpha_status=not_evaluated_by_backtest`
  - `research_status=no_candidate_signal_passed`
  - `backtest_ready=false`
  - `any_signal_gate_passed=false`
  - `any_candidate_for_backtest=false`

Model verifier result after repair:

```text
Model validation: PASS
```

Research interpretation:

- No verified alpha was found under tested configurations.
- BacktestAgent was not run, so `alpha_verified` was not evaluated.
- Do not run PortfolioAgent or BacktestAgent from diagnostic/no-candidate outputs unless a later alpha-search phase exports a valid candidate.
- The diagnostic baseline was not promoted to candidate status, and signal thresholds were not loosened.

Detailed report:

- [Pipeline run report](./PIPELINE_RUN_REPORT.md)

## Phase 3 Alpha-Search Expansion Notes (May 7, 2026)

AlphaResearchAgent was run in signal-only mode. It did not mutate canonical LabelAgent, FeatureAgent, ModelAgent, PortfolioAgent, or BacktestAgent outputs. `export_candidate_to_predictions=false`, so canonical `data/predictions/model_predictions.parquet` was not overwritten.

Commands passed:

```text
python3 -m py_compile agents/alpha_research_agent.py scripts/verify_alpha_research_run.py
python3 -m pytest tests/test_alpha_research_agent.py -q
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
python3 scripts/verify_alpha_research_run.py --config configs/run_config.yaml --section alpha_research
```

Alpha research verifier result:

```text
Alpha research validation: PASS
```

Run outputs:

- `data/research/research_leaderboard.parquet`
- `data/research/best_experiments.parquet`
- `data/research/research_manifest.json`
- `data/research/alpha_research_report.md`
- `data/predictions/alpha_research_predictions.parquet`
- `data/predictions/alpha_model_leaderboard.parquet`
- `data/predictions/alpha_fold_metrics.parquet`

Run counts:

- Experiments run: `80`.
- Experiments skipped by configured budget: `1,090`.
- Prediction rows: `16,491,600`.
- Fold metric rows: `3,040`.
- Candidate signals found: `3`.
- Final alpha passed: `0`.

Candidate signals:

- `lightgbm`, `market_only`, `raw_forward_return`, horizon `14d`: mean Rank IC `0.0275`, Rank IC t-stat `7.1034`, top-bottom spread `0.0034`.
- `linear_ridge`, `market_only`, `raw_forward_return`, horizon `30d`: mean Rank IC `0.0142`, Rank IC t-stat `6.3192`, top-bottom spread `0.0041`.
- `random_forest`, `market_only`, `raw_forward_return`, horizon `14d`: mean Rank IC `0.0170`, Rank IC t-stat `4.3800`, top-bottom spread `0.0029`.

Research interpretation:

- Candidate signals found, but alpha is not verified until PortfolioAgent and BacktestAgent evaluate them.
- AlphaResearchAgent remains signal-only and cannot set `alpha_verified` to true.
- BacktestAgent has not evaluated these candidates yet.
- Latest-survivor universe limitation still applies.
- CMC three-year point-in-time universe remains blocked by the current API plan.

Detailed report:

- [Alpha signal search report](./ALPHA_SIGNAL_SEARCH_REPORT.md)

## Phase 4A Candidate Backtest Verification Notes (May 7, 2026)

AlphaResearchAgent candidate signals were exported to separate candidate prediction files and then evaluated through PortfolioAgent and BacktestAgent.

Canonical `data/predictions/model_predictions.parquet` was not overwritten.

Candidate export outputs:

- `data/predictions/candidate_model_predictions.parquet`
- `data/predictions/candidate_model_leaderboard.parquet`
- `data/predictions/candidate_model_manifest.json`

Candidate export result:

- Candidate combos exported: `3`.
- Portfolio-safe prediction rows after de-duplication: `217,215`.
- Overlapping out-of-sample duplicate rows dropped deterministically: `401,220`.
- Forbidden realized/label/future/target columns in candidate prediction file: `0`.
- `alpha_verified=false`.
- `backtest_required=true`.

PortfolioAgent result:

- Config section: `portfolio_alpha_candidates`.
- Selected model: `lightgbm`.
- Selected feature set: `market_only`.
- Selected horizon: `14`.
- Allocation mode: `signal_candidate_for_backtest`.
- Allocation rows: `22,253`.
- Rebalance count: `172`.
- Unique symbols allocated: `90`.
- Portfolio verifier: PASS.

BacktestAgent result:

- Config section: `backtesting_alpha_candidates`.
- Backtest verifier: PASS.
- Benchmark sanity: PASS.
- Best strategy by Sharpe: `top_20_vol_scaled`.
- Best strategy by total return: `top_20_vol_scaled`.
- Best strategy total return: `45.39%`.
- Best strategy CAGR: `12.10%`.
- Best strategy Sharpe: `0.5030`.
- Best strategy max drawdown: `-71.45%`.
- Transaction cost for main backtest: `20 bps`.

Benchmark results over the aligned backtest window:

- BTC total return: `305.50%`.
- ETH total return: `69.85%`.
- BTC/ETH 50-50 total return: `178.04%`.
- Equal-weight universe total return: `30.39%`.

Alpha result:

- Any strategy passed alpha status: `false`.
- `alpha_verified=false`.
- No verified alpha found under tested candidate signals.

Research interpretation:

- The best candidate portfolio beat equal-weight universe but did not beat BTC, ETH, or BTC/ETH 50-50.
- BacktestAgent correctly rejected alpha verification.
- Latest-survivor universe limitation still applies.

Detailed report:

- [Alpha backtest verification report](./ALPHA_BACKTEST_VERIFICATION_REPORT.md)

## Phase 4B Candidate-By-Candidate Verification Notes (May 8, 2026)

Each Phase 3 AlphaResearch candidate was exported into a separate portfolio-safe prediction file and tested independently through PortfolioAgent and BacktestAgent.

Candidate-specific prediction files:

- `data/predictions/candidates_by_signal/lightgbm_market_only_raw_forward_return_14d_predictions.parquet`
- `data/predictions/candidates_by_signal/linear_ridge_market_only_raw_forward_return_30d_predictions.parquet`
- `data/predictions/candidates_by_signal/random_forest_market_only_raw_forward_return_14d_predictions.parquet`

All candidate-specific prediction files passed safety checks:

- No actual/realized/label/future/target/y columns.
- Finite predictions.
- No duplicate `date_ts + symbol` rows per candidate.
- `candidate_for_backtest=true`.
- `alpha_status=not_evaluated_by_backtest`.
- `alpha_verified=false` before BacktestAgent.

Candidate-by-candidate verifier results:

- `lightgbm_market_only_raw_forward_return_14d`: Portfolio verifier PASS, Backtest verifier PASS.
- `linear_ridge_market_only_raw_forward_return_30d`: Portfolio verifier PASS, Backtest verifier PASS.
- `random_forest_market_only_raw_forward_return_14d`: Portfolio verifier PASS, Backtest verifier PASS.

Candidate results:

- `lightgbm_market_only_raw_forward_return_14d`: best strategy `top_20_vol_scaled`, total return `45.39%`, CAGR `12.10%`, Sharpe `0.5030`, max drawdown `-71.45%`, `alpha_verified=false`.
- `linear_ridge_market_only_raw_forward_return_30d`: best strategy `top_5_equal_weight`, total return `147.36%`, CAGR `31.84%`, Sharpe `0.7521`, max drawdown `-59.40%`, `alpha_verified=false`.
- `random_forest_market_only_raw_forward_return_14d`: best strategy `top_5_equal_weight`, total return `-30.40%`, CAGR `-10.47%`, Sharpe `0.2288`, max drawdown `-86.86%`, `alpha_verified=false`.

Benchmark returns over the aligned backtest window:

- BTC: `305.50%`.
- ETH: `69.85%`.
- BTC/ETH 50-50: `178.04%`.
- Equal-weight universe: `30.39%`.

Research interpretation:

- No verified alpha found across individually tested candidates.
- The strongest candidate was `linear_ridge_market_only_raw_forward_return_30d`; it beat ETH and equal-weight universe but did not beat BTC or BTC/ETH 50-50.
- BacktestAgent correctly kept `alpha_verified=false` for all candidates.
- Latest-survivor universe limitation still applies.

## Phase 5 Final Research Package Notes (May 9, 2026)

Final package documents were created:

- `docs/RESEARCH_RESULTS_SUMMARY.md`
- `docs/ALPHA_FINDINGS_REPORT.md`
- `docs/LIMITATIONS_AND_NEXT_STEPS.md`
- `docs/REPRODUCIBILITY_COMMANDS.md`
- `docs/PIPELINE_RUN_REPORT.md`
- `docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md`
- `docs/API_DATA_READINESS_AUDIT.md`
- `docs/knowledge_base.md`

Final research conclusion:

CHF found statistically promising candidate signals, but after deterministic portfolio construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting, no strategy achieved verified alpha against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks under the tested configurations.

Final alpha state:

- `alpha_verified=false` for every individually tested candidate.
- No verified alpha found under tested configurations.

Strongest candidate details:

- Candidate: `linear_ridge_market_only_raw_forward_return_30d`.
- Best strategy: `top_5_equal_weight`.
- Total return: `147.36%`.
- CAGR: `31.84%`.
- Sharpe: `0.7521`.
- Max drawdown: `-59.40%`.
- It beat ETH and equal-weight universe.
- It did not beat BTC or BTC/ETH 50-50.
- BacktestAgent kept `alpha_verified=false`.

Persistent limitations:

- CMC three-year historical listings remain blocked by the current plan.
- Current production universe is latest-survivor baseline, not point-in-time historical membership.
- On-chain coverage is sparse relative to market coverage.
- Only `80` alpha experiments were run from the larger configured search grid.

Safe next step:

- Submit or share the final research package as an honest no-verified-alpha result.
- If continuing research, first obtain point-in-time historical universe data or expand the alpha-search budget without loosening verification criteria.

## Phase 6 Repository Cleanup And Submission Plan Notes (May 9, 2026)

Repository cleanup was handled as a documentation and `.gitignore` phase only. No files were deleted, staged, committed, reset, or cleaned.

Files created or updated:

- `.gitignore`
- `docs/REPO_CLEANUP_AND_SUBMISSION_PLAN.md`
- `docs/REPRODUCIBILITY_COMMANDS.md`
- `docs/ALPHA_FINDINGS_REPORT.md`
- `docs/ALPHA_SIGNAL_SEARCH_REPORT.md`
- `docs/knowledge_base.md`

Cleanup plan summary:

- Commit-worthy source code includes agents, providers, scripts, configs, tests, pipeline wiring, and schema/model utilities after manual diff review.
- Commit-worthy docs include the final research package and cleanup plan.
- Generated research outputs should be archived separately rather than committed directly.
- Local-only files include `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `logs/`, `data/cache/`, `data/*_smoke/`, `data/backups/`, and local metadata databases.
- Tracked data deletions and generated data modifications should be reviewed manually before any future commit.

Important submission guidance:

- Do not use `git add .`.
- Do not commit API keys, `.env`, virtual environments, cache files, bytecode, or generated data unless intentionally archiving outside git.
- The final research package remains safe to submit/share as an honest no-verified-alpha result.

## Phase 7 Final Repository Cleanup And Commit-Readiness Notes (May 12, 2026)

Phase 7 completed commit-readiness cleanup without changing research results, rerunning the full pipeline, deleting local data, committing, pushing, staging all files, resetting, cleaning, editing `.env`, or printing secrets.

Cleanup status:

- `data/` is ignored in `.gitignore`.
- Local `data/` files still exist on disk.
- `git rm --cached -r data` returned `fatal: pathspec 'data' did not match any files`, meaning no tracked `data` pathspec remained for removal at that point.
- Generated data files appear as `D` entries in `git status --short`, which is expected for files being removed from Git tracking.
- Junk/cache/local metadata files appear as `D` entries only, not `M`, for the checked patterns:
  - `.DS_Store`
  - `__pycache__/`
  - `*.pyc`
  - `metadata/*.db`

`.gitignore` now includes:

- `.env`
- `.env.*`
- `.venv/`
- `__pycache__/`
- `*.py[cod]`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `.ipynb_checkpoints/`
- `.DS_Store`
- `logs/`
- `metadata/*.db`
- `data/`

Validation passed:

```text
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_portfolio_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q
```

Targeted tests:

- `74 passed`

Secret/claim scan:

- No real keys found.
- No improper claim that CHF found verified alpha.
- Remaining grep matches are expected no-alpha statements.

Commit-readiness report:

- `docs/REPOSITORY_COMMIT_READINESS_REPORT.md`

Safe next step:

- Perform manual diff review.
- Prepare an explicit-path commit later.
- Do not use `git add .`.

## Phase 8 Final Source-Only Commit Preparation Audit (May 12, 2026)

Phase 8 completed the final source-only repository audit. No agents or research results were changed, no generated backtest outputs were modified, no full pipeline was rerun, no local data was deleted, no `.env` file was edited, and no commit, push, reset, clean, or broad staging command was run.

Repository cleanup status:

- `.gitignore` contains the required local/generated patterns:
  - `.env`
  - `.venv/`
  - `__pycache__/`
  - `*.py[cod]`
  - `*.pyc`
  - `.pytest_cache/`
  - `.DS_Store`
  - `logs/`
  - `metadata/*.db`
  - `data/`
  - `.mypy_cache/`
  - `.ruff_cache/`
  - `.ipynb_checkpoints/`
- `data/` is ignored and appears only as tracked deletions for previously tracked generated artifacts.
- Junk/cache/local metadata files appear only as tracked deletions for removal from Git tracking.
- Local generated data remains on disk and should be archived separately, not committed.

Validation passed:

```text
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_portfolio_agent_research_mode.py tests/test_backtest_agent_research_mode.py tests/test_universe_agent_research_mode.py tests/test_market_data_agent_research_mode.py tests/test_onchain_agent_research_mode.py tests/test_feature_agent_research_mode.py tests/test_label_agent_research_mode.py -q
```

Targeted test result:

- `230 passed`

Secret/claim scan:

- No real keys found.
- No improper claim that CHF found verified alpha.
- Expected matches are the intentionally honest no-alpha statements.

Commit preparation guidance:

- Nothing was staged automatically in Phase 8.
- Do not use `git add .`.
- Review diffs manually, then stage explicit source/docs/tests/config paths only.
- Generated research data and local cache artifacts should remain out of Git.

## Phase 9 GitHub README Rewrite And Submission Polish (May 12, 2026)

Phase 9 rewrote the GitHub README as a concise research-oriented landing page. No agents, providers, models, features, configs, tests, generated outputs, `.env` values, local data, or research result numbers were changed.

README now emphasizes:

- CHF is a reproducible quantitative crypto alpha research pipeline.
- The research question is whether market and on-chain features can outperform BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks after costs and realistic validation.
- `alpha_verified=false` for every tested candidate.
- No verified alpha found under tested configurations.
- The strongest candidate was `linear_ridge / market_only / raw_forward_return / 30d`, which beat ETH and equal-weight universe but did not beat BTC or BTC/ETH 50-50.
- BacktestAgent is the final alpha authority.
- AlphaResearchAgent is signal-only and cannot claim alpha.
- `data/` outputs are generated locally and ignored by Git.
- Latest-survivor and CMC historical listings access limitations remain.

Submission checklist created:

- `docs/GITHUB_SUBMISSION_CHECKLIST.md`

Phase 9 validation passed:

- README overclaim scan.
- README docs link check.
- Secret scan against `HEAD`.
- Working-tree README/docs secret scan.
- Syntax validation.
- Targeted tests: `54 passed`.

Phase 9 redo status:

- README rewrite is present in the local working tree.
- README says `No verified alpha found under tested configurations.`
- README preserves `alpha_verified=false` for every tested candidate.
- README identifies `linear_ridge / market_only / raw_forward_return / 30d` as the strongest candidate and says it did not beat BTC or BTC/ETH 50-50.
- No commit, push, source-logic edit, data deletion, `.env` edit, or full pipeline rerun was performed.

Manual note:

- The README includes the requested `.env.example` setup command. Final repository review should confirm `.env.example` exists or adjust setup wording before committing README polish.

## Beginner Setup Documentation Update (May 12, 2026)

The practical setup/runbook was added to repository documentation for non-technical reviewers.

Files added:

- `docs/USER_GUIDE.md`
- `docs/API_KEYS_AND_DATA_SOURCES.md`
- `docs/DASHBOARD_GUIDE.md`

README update:

- Added a concise `How To Run CHF` section.
- Linked the new beginner guides.
- Kept the final research conclusion unchanged: no verified alpha found under tested configurations.

Guide coverage:

- installation and virtual environment setup,
- local `.env` usage,
- API key meanings and required/optional status,
- API readiness checks,
- full pipeline command,
- individual stage commands and verifiers,
- AlphaResearchAgent signal-only command,
- dashboard launch steps,
- expected generated output locations,
- troubleshooting and verifier-first failure handling.

No source code, research numbers, generated data, `.env` values, commits, or pushes were changed.

## Phase 10 Benchmark Verification And Reviewer Packet (May 13, 2026)

Phase 10 verified the candidate benchmark numbers from stored backtest artifacts and raw market prices without changing source logic, model/backtest calculations, generated outputs, `.env`, or research numbers.

Benchmark verification:

- Candidate benchmark folders inspected:
  - `data/backtests_candidate_lightgbm_14d`
  - `data/backtests_candidate_linear_ridge_30d`
  - `data/backtests_candidate_random_forest_14d`
- All three candidate backtests used the same benchmark window:
  - start: `2022-12-15 00:00:00 UTC`
  - end: `2026-03-24 00:00:00 UTC`
- Benchmark returns:
  - BTC: `305.50%`
  - ETH: `69.85%`
  - BTC/ETH 50-50: `178.04%`
  - equal-weight universe: `30.39%`

Manual BTC check from `data/raw/market/market_ohlcv.parquet`:

- BTC start close on `2022-12-15`: `17,359.21`
- BTC end close on `2026-03-24`: `70,532.10`
- raw close-to-close return: `306.31%`
- return after BacktestAgent's 20 bps initial benchmark cost: `305.50%`
- BacktestAgent BTC benchmark return: `305.50%`
- difference after cost convention: `0.000000%`

Documentation added:

- `docs/BENCHMARK_VERIFICATION.md`
- `docs/FINAL_REVIEWER_PACKET.md`

Clarification added:

- Benchmark returns are measured over each candidate's backtest window, not over a trailing five-year public chart window.

Final conclusion unchanged:

- No verified alpha found under tested configurations.
