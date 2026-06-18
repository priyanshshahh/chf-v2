# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CHF is a reproducible quantitative crypto research pipeline that tests whether market and on-chain features contain tradable cross-sectional alpha after leakage-safe modeling, deterministic portfolio construction, transaction costs, benchmark sanity checks, and out-of-sample backtesting. The headline result is **negative**: `alpha_verified=false` for all tested candidates. Preserving the integrity of that negative result (no leakage, no alpha overclaiming) is the central design constraint — see "Research-integrity contracts" below.

## Commands

Setup and environment:
```bash
make setup                  # create .venv and install requirements
python scripts/bootstrap.py # create data dirs, copy .env.example -> .env, verify imports
```

Running the pipeline (single CLI entrypoint `main.py`, or `make <stage>`):
```bash
python main.py full         # entire pipeline end-to-end
python main.py demo         # generate synthetic artifacts (no API keys; feeds tests/dashboard/API)
./run_all.sh                # full pipeline + per-stage verifier after each stage
./run_all.sh --demo         # demo data only
```
Stages run in order: `universe → market → onchain → features → labels → model(s) → portfolio → backtest`, then `ablation`. Each has a matching verifier in `scripts/verify_<stage>_run.py`.

> Note: the model stage is `models` in the Makefile but `model` (singular) in `run_all.sh`/README; `main.py` accepts the documented forms. When in doubt, check the argparse subcommands in `main.py`.

Serving and UIs:
```bash
make serve        # FastAPI (app/api.py) on :8000
make dashboard    # Streamlit (app/dashboard.py) on :8501  (run `main.py demo` first if no data)
make mlflow       # MLflow UI on :5000, backed by ./mlruns
python main.py schedule   # APScheduler daemon
```

Testing:
```bash
make smoke                                  # offline end-to-end validation (scripts/smoke_test.py)
make test                                   # full pytest suite (-v --tb=short)
python -m pytest tests/test_<name>.py -q    # single test file
python -m pytest tests/test_x.py::TestClass::test_y   # single test
python -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py  # syntax check
```
The `*_research_mode.py` tests are the research-integrity suite — they guard leakage and alpha-claim rules. Treat failures there as correctness failures, not flakiness.

Diagnostics:
```bash
python scripts/probe_api_readiness.py --config configs/run_config.yaml   # which providers/keys are live
python scripts/audit_pipeline_inputs.py --config configs/run_config.yaml # validate local pipeline inputs
```

Formatting: black + isort, line-length 100, target py311 (`pyproject.toml`).

## Architecture

**Agent pipeline.** Every stage is an agent in `agents/` subclassing `AgentBase` (`agents/base.py`). Subclasses implement `prepare()` (validate inputs/preconditions), `run()` (core logic), and `persist()` (write outputs). The base `execute()` wraps these with retries (exponential backoff), status tracking, logging, and registry updates — do not reimplement that lifecycle in subclasses.

**Run provenance.** Each agent run writes a record to the SQLite registry at `metadata/agent_registry.db` keyed by `run_id`, with `config_hash`, `snapshot_id`, `output_paths`, and `metrics`. Snapshot IDs are deterministic from `config_hash + data_repr + run_id`.

**Orchestration.** `main.py` is the user-facing CLI; `pipelines/pipeline_runner.py` runs the same stages programmatically — put cross-stage control-flow changes there, not in `main.py`.

**Config is the source of truth.** `configs/run_config.yaml` drives all tunable behavior; `configs/config.py` loads it, resolves paths under the repo root, and applies `.env` overrides (`MLFLOW_TRACKING_URI`, `CHF_SEED`, `LOG_LEVEL`). `main.py` supports config *sections* (e.g. `market_data_*`) that merge over their base section. Universe exclusions live in `configs/universe_exclusions.yaml`.

**File-based contracts.** Stages communicate only through artifacts under `data/` (Parquet/JSON), partitioned hive-style where relevant (e.g. `data/raw/market/year=YYYY/month=MM/SYMBOL.parquet`). Subfolders: `raw/ cleaned/ features/ labels/ predictions/ allocations/ backtests/ reports/`, plus `artifacts/` and `mlruns/`. `data/` and `metadata/` are gitignored — generated locally. When editing a stage, preserve the output schema that downstream stages, the dashboard, the API, and smoke tests consume.

**Providers.** `providers/` holds one adapter per data source (CoinGecko, Binance/CCXT, CoinMetrics, DeFiLlama, CoinMarketCap, etc.) over a shared `http_client.py`, with `market_fallbacks.py` for provider failover. Most providers have a free/community tier and work without keys.

**Read-only consumers.** `app/dashboard.py` (Streamlit) and `app/api.py` (FastAPI) only read pipeline outputs — do not put business logic there. FastAPI, Streamlit, APScheduler, and vectorbt are optional at runtime; code should degrade gracefully (e.g. `BacktestAgent` falls back if vectorbt is missing) when they are absent.

## Research-integrity contracts (do not violate)

- **Leakage-safe by construction.** Labels are exact forward calendar returns. Leakage guards exist across feature, label, model, portfolio, and backtest stages. Model validation is purged + embargoed walk-forward CV; Rank IC is the primary quality metric.
- **Prediction-only portfolio inputs.** `PortfolioAgent` consumes the latest available *predictions* as-of each rebalance date and applies liquidity + positive-signal filters to produce long-only allocations. Realized returns and labels are rejected as allocation inputs — never feed them in.
- **`BacktestAgent` is the sole alpha authority.** It verifies or rejects alpha after transaction costs and benchmark sanity checks (BTC, ETH, BTC/ETH 50-50, equal-weight universe). `AlphaResearchAgent` does signal-only expansion and **cannot** claim alpha. No code path should report verified alpha except via `BacktestAgent`.
- **Two-tier features.** `FeatureAgentV1` builds market features from OHLCV; `FeatureAgentV2` adds on-chain features. The feature store is leakage-aware and may honor `feature_keep_list.json`.

## Conventions

- Timestamps are UTC-aware (`date_ts`); loaders normalize with `pd.to_datetime(..., utc=True)`.
- Entrypoints add the project root to `sys.path`; keep imports package-absolute (`agents...`, `configs...`) and preserve the existing module layout.
- Prefer minimal, stage-local changes that keep the file-based contracts intact.

## Known limitations (reflected in code/data)

- Production universe is a latest-survivor baseline, not full point-in-time — CoinMarketCap 3-year historical listings were blocked by the API plan during this study (`docs/COINMARKETCAP.md`).
- On-chain coverage is sparse relative to market coverage. No real-time execution engine. Research/education only.

## Reference docs

Start with `docs/RESEARCH_RESULTS_SUMMARY.md` and `docs/REPRODUCIBILITY_COMMANDS.md`. Feature formulas are in `docs/data_dictionary.md`; the DAG diagram is `docs/architecture.png`.
