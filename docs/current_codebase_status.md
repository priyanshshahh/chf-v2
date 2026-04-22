# CHF Current Codebase Status

## Purpose

This document captures what the current CHF codebase already implements and how it maps to the three requirement PDFs:

- `Crypto Quant System Build Plan.pdf`
- `Project CHF overview (1).pdf`
- `Project Roadmap CHF (1).pdf`

The current repo is best described as a deterministic, local-first crypto quant research and portfolio automation system. It is not yet a true LLM-driven agentic AI system. The existing "agents" are pipeline workers implemented as Python classes with a shared lifecycle.

## What The Codebase Is Today

Core design:

- Local-first Python project
- Deterministic ETL, feature engineering, modeling, allocation, and backtesting
- Parquet-centered data lake
- Optional DuckDB analytics
- MLflow experiment tracking
- Streamlit dashboard and FastAPI output layer
- APScheduler for cron-style automation

The main execution flow is:

`Universe -> Market Data -> On-Chain -> Clean -> Features -> Labels -> Models -> Portfolio -> Backtest`

Primary entry points:

- `main.py`: CLI entrypoint for individual stages and full pipeline runs
- `pipelines/pipeline_runner.py`: stage orchestration
- `jobs/scheduler.py`: cron-based automation
- `app/dashboard.py`: Streamlit interface
- `app/api.py`: FastAPI endpoints over generated artifacts

## What "Agents" Mean In This Repo

The project already uses an agent-style interface, but not in the modern LLM-autonomous sense.

Implemented pattern:

- `agents/base.py` defines `prepare()`, `run()`, `persist()`, plus retries, logging, snapshot IDs, and SQLite run history
- Concrete classes like `UniverseAgent`, `MarketDataAgent`, `FeatureAgentV1`, `FeatureAgentV2`, `ModelAgent`, and `BacktestAgent` inherit from this base
- Each class executes a predetermined job

So these are deterministic software agents or pipeline workers, which matches the build-plan PDF's preference for programmatic, reproducible agents.

## What Is Already Implemented

### Project and Infrastructure

Implemented:

- `README.md` with architecture and run instructions
- `configs/run_config.yaml` as the central config surface
- structured logging in `configs/logging_config.py`
- run registry in `metadata/agent_registry.db`
- local MLflow setup in `mlruns/`
- shell helpers like `run_all.sh`, `run_dashboard.sh`, and `Makefile`
- smoke/bootstrap scripts in `scripts/`

Partially aligned with roadmap:

- folder layout is in good shape
- schema modeling exists in `schemas/schemas.py`
- roadmap asked for schema markdown docs in `/schemas/*.md`, but the repo currently uses Python schema definitions instead of schema documentation files

### Phase 1: Data Layer

Implemented:

- `agents/universe_agent.py`
  - monthly eligible universe construction
  - stablecoin and wrapped-asset exclusion
  - snapshot metadata written to JSON
- `agents/market_data_agent.py`
  - CCXT/Binance OHLCV ingestion
  - incremental updates
  - QA report output
  - flat and hive-style Parquet output
- `agents/onchain_agent.py`
  - CoinMetrics + DeFiLlama ingestion
  - coverage report output
- snapshot IDs across agents via `AgentBase.generate_snapshot_id()`

Partially implemented / caveats:

- DuckDB exists in `pipelines/duckdb_engine.py`, but it is more of an analytics helper than a full view-management layer
- the roadmap references Glassnode, while the implemented stack uses CoinMetrics Community + DeFiLlama

### Phase 2: Feature Store

Implemented:

- `agents/feature_agent.py`
  - `FeatureAgentV1` for market features
  - `FeatureAgentV2` for on-chain feature merge
- leakage-aware feature/label separation
- winsorization
- cross-sectional z-scoring
- correlation-cluster-based redundancy pruning
- saved `feature_keep_list.json`
- `agents/label_agent.py` for 7d/14d/30d forward return labels

Implemented feature families include:

- returns over multiple windows
- rolling volatility
- rolling skewness
- beta to BTC
- volume ratios
- reversal
- ATR proxy
- active address growth
- transaction growth
- NVT ratio / NVT signal
- MVRV proxy
- realized cap change
- fee intensity
- TVL ratio / TVL growth

Partially implemented / caveats:

- the roadmap calls for a formal redundancy pruning report including VIF; the repo has correlation clustering and VIF utilities in `features/feature_engineering.py`, but the exported reporting layer is still lightweight

### Phase 3: Baseline Modeling and Backtesting

Implemented:

- `agents/model_agent.py`
  - walk-forward model training
  - RandomForest
  - LightGBM
  - optional XGBoost path
  - MLflow metric/artifact logging
  - persisted predictions and model binaries
- `models/walk_forward.py`
  - purged/embargoed expanding-window validation
- `agents/portfolio_agent.py`
  - top-K equal weight
  - score-proportional weighting
  - weekly/biweekly/monthly rebalancing
  - liquidity filter
  - transaction log
- `agents/backtest_agent.py`
  - vectorbt primary engine
  - NumPy fallback
  - cost sweeps
  - K sweeps
  - subperiod analysis
  - BTC and equal-weight benchmark comparisons

Strong alignment with the PDFs:

- tree-based ML rather than deep sequence models
- leakage-aware evaluation
- cost-aware backtesting
- professional quant-research workflow

### Phase 4: Advanced Models and Robustness

Implemented:

- LightGBM support
- XGBoost support path
- SHAP computation helper
- ablation module in `models/ablation.py`
- cost sweeps
- K sweeps
- subperiod tests

Not fully implemented:

- Optuna tuning is declared in config and dependencies, but not actually integrated into `ModelAgent`
- a proper decay monitor for rolling feature rank-IC and auto down-weighting is not present
- social/sentiment data is not implemented

### Phase 5: Paper Draft and Freeze

Not meaningfully represented in code yet:

- no paper draft structure in repo
- no pinned experiment manifest for final paper release
- no formal results tables package for the paper

### Phase 6: Product Seed

Implemented:

- Streamlit dashboard in `app/dashboard.py`
- FastAPI endpoints in `app/api.py`
- reproducibility-oriented scripts
- smoke tests in `scripts/smoke_test.py` and `tests/`

Partially implemented:

- "ServeAgent" is represented functionally by dashboard/API, but not as a dedicated agent class
- tests exist, but the suite is still more smoke/integration oriented than a broad unit-test matrix

### Phase 7: Polish, Submit, Present

Mostly not implemented:

- no executive summary one-pager
- no slide deck in repo
- no final release tagging/archive flow inside the project

## Current Strengths

The repo already does a lot well:

- architecture matches the local-first design in the PDFs
- deterministic execution is appropriate for academic finance work
- config-driven structure is good for reproducibility
- artifact layout is practical and easy to inspect
- validation and backtesting design show sound quant instincts
- dashboard and API make the project feel product-oriented, not just notebook-based

## Current Gaps

The biggest gaps between the current repo and the full PDF vision are:

1. The system is not yet genuinely agentic in the AI sense.
2. The pipeline orchestration is fixed and procedural, not goal-driven or adaptive.
3. Optuna and a formal model-experiment loop are not wired into training.
4. Feature decay monitoring and automatic feature governance are missing.
5. Social/sentiment signals are absent.
6. Final research deliverables are not yet codified in-repo.
7. Some roadmap outputs exist in spirit but not in the exact requested form, such as schema docs and formal reporting artifacts.

## Bottom Line

This codebase already satisfies a large portion of the deterministic quant-system requirements from the PDFs. It is a credible quant research platform with modular "agents," but those agents are execution workers, not autonomous AI planners.

If the goal is now to evolve CHF into a true agentic AI system, the right next step is not replacing this quant core. The right next step is to build an agentic orchestration layer on top of this deterministic foundation.
