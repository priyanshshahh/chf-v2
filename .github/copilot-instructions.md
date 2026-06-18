# CHF Copilot Instructions

- **Project shape:** `CHF` is an agent-based crypto portfolio system with a fixed pipeline: `Universe → Market Data → On-chain → Features → Labels → Models → Portfolio → Backtest`.
- **Primary entrypoint:** Use `main.py` for user-facing commands (`universe`, `market`, `onchain`, `features`, `labels`, `models`, `portfolio`, `backtest`, `ablation`, `full`, `serve`, `schedule`, `demo`).
- **Orchestration layer:** `pipelines/pipeline_runner.py` runs the same stages programmatically and is the right place for cross-stage control flow changes.
- **Agent contract:** All pipeline agents inherit from `agents/base.py` and implement `prepare()`, `run()`, and `persist()`; `execute()` handles retries, status, logging, and registry updates.
- **State & provenance:** Agents write run metadata to `metadata/agent_registry.db` with `run_id`, `config_hash`, `snapshot_id`, `output_paths`, and metrics.
- **Config source of truth:** `configs/run_config.yaml` drives behavior; `configs/config.py` loads it, resolves paths under the repo root, and honors `.env` overrides for `MLFLOW_TRACKING_URI`, `CHF_SEED`, and `LOG_LEVEL`.
- **Output conventions:** Persist artifacts as Parquet/JSON under `data/` subfolders (`raw/`, `cleaned/`, `features/`, `labels/`, `predictions/`, `allocations/`, `backtests/`) plus `artifacts/` and `mlruns/`.
- **Time handling:** Use UTC-aware `date_ts` timestamps; many loaders normalize date columns with `pd.to_datetime(..., utc=True)`.
- **Feature pipeline:** `FeatureAgentV1` builds market features from OHLCV; `FeatureAgentV2` adds on-chain features. The feature store is leakage-aware and may use `feature_keep_list.json`.
- **Modeling pattern:** `agents/model_agent.py` uses walk-forward validation, with Rank IC as the main quality metric, and logs local MLflow runs. Default models are `random_forest` and `lightgbm`.
- **Portfolio pattern:** `agents/portfolio_agent.py` consumes the latest available predictions as-of each rebalance date, applies liquidity and positive-signal filters, and produces long-only allocations.
- **Backtesting pattern:** `agents/backtest_agent.py` prefers `vectorbt` and falls back gracefully if unavailable; outputs must include `data/backtests/backtest_summary.parquet` and `equity_curves.parquet`.
- **Dashboard/API behavior:** `app/dashboard.py` and `app/api.py` are read-only consumers of pipeline outputs; avoid duplicating business logic there.
- **Demo mode is real:** `python main.py demo` generates the canonical synthetic artifacts used by tests, the dashboard, and the API when live data is absent.
- **Common workflows:** `make setup`, `make smoke`, `make test`, `python main.py full`, `streamlit run app/dashboard.py`, `python main.py serve`, and `python main.py schedule` are the main local commands.
- **Optional dependencies:** FastAPI, Streamlit, APScheduler, and VectorBT are optional at runtime; code should fail softly when they are missing.
- **Imports & structure:** The repo adds the project root to `sys.path` in entrypoints; keep imports package-absolute (`agents...`, `configs...`) and preserve the existing module layout.
- **When editing code:** Prefer minimal, stage-local changes that preserve file-based contracts consumed by the dashboard, API, and smoke tests.