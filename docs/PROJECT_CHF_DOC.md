# Project CHF Doc

The single, complete reference for Project CHF: what it is, what was built and delivered, how it
works, what the results are, and where its limits lie. Every number in this document is taken
from the project's own verified outputs and source-of-truth docs — nothing here is estimated or
invented. The headline result is a **deliberate negative**, and preserving the integrity of that
negative is the central design constraint of the whole system.

- **Status:** frozen research release (`v1.0-research-release`)
- **Headline result:** `alpha_verified = false` — no verified alpha found under tested configurations
- **Benchmark window:** `2022-12-15` → `2026-03-24` (candidate-aligned)
- **Classification:** research and education only. Not financial advice. No live trading.

---

## 1. What this project is about

Project CHF is a reproducible, leakage-safe quantitative cryptocurrency research pipeline. It
exists to answer one question honestly:

> **Can market and on-chain features be used to construct crypto portfolios that outperform BTC,
> ETH, BTC/ETH 50-50, and an equal-weight crypto universe *after costs and realistic validation*?**

Its purpose is **not** to manufacture a flattering backtest. It is to test whether a candidate
signal survives every layer a credible alpha pipeline must impose:

- real market and on-chain data,
- honest universe construction,
- leakage-safe feature and label generation,
- purged + embargoed walk-forward signal screening,
- deterministic, prediction-only portfolio allocation,
- transaction-cost-aware backtesting,
- benchmark sanity checks,
- comparison against BTC, ETH, BTC/ETH 50-50, and an equal-weight universe.

A system designed to find alpha is easy to fool. A system designed to *reject* unsupported alpha
claims is the harder and more valuable thing to build. CHF is the second kind.

---

## 2. Headline result

CHF found statistically promising candidate signals, but after deterministic portfolio
construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting,
**no strategy achieved verified alpha** against the four benchmarks under the tested
configurations.

- `alpha_verified = false`
- No verified alpha found under tested configurations.

This is reported as a *successful* research outcome: the pipeline did exactly what a trustworthy
alpha-research process should do — it surfaced candidates, isolated them into portfolio-safe
files, refused to let the research stage claim alpha, forced every candidate through deterministic
allocation and cost-aware backtesting, and then **rejected the claims that did not clear the
benchmark bar.** The result is not flattering, but it is trustworthy.

---

## 3. Results (verified numbers)

All figures below are measured over each candidate's own backtest window (not a trailing public
chart window) and are reproduced from the project's backtest outputs and
`docs/RESEARCH_RESULTS_SUMMARY.md` / `docs/BENCHMARK_VERIFICATION.md`.

### 3.1 Benchmark returns (aligned window)

| Benchmark | Total Return |
|---|---:|
| BTC | 305.50% |
| ETH | 69.85% |
| BTC/ETH 50-50 | 178.04% |
| Equal-weight universe | 30.39% |

The BTC figure was independently sanity-checked from raw market closes
(≈17,359 → ≈70,532 over the window) and matches the BacktestAgent's benchmark return after its
20 bps initial cost convention.

### 3.2 Strongest signal-screen candidate (signal only — not alpha)

| Field | Value |
|---|---|
| Model | `lightgbm` |
| Feature set | `market_only` |
| Label target | `raw_forward_return` |
| Horizon | `14d` |
| Mean Rank IC | `0.0275` |
| Rank IC t-stat | `7.1034` |
| Top-bottom spread | `0.0034` |

A positive, statistically significant Rank IC at the signal-screen stage — but **not** treated as
alpha until the BacktestAgent evaluated it after costs and benchmarks.

### 3.3 Candidate-by-candidate backtest results

| Candidate | Best Strategy | Total Return | Sharpe | Beat BTC | Beat ETH | Beat 50-50 | Beat EW | Alpha Verified |
|---|---|---:|---:|:--:|:--:|:--:|:--:|:--:|
| `lightgbm / market_only / raw_forward_return / 14d` | `top_20_vol_scaled` | 45.39% | 0.5030 | false | false | false | true | **false** |
| `linear_ridge / market_only / raw_forward_return / 30d` | `top_5_equal_weight` | 147.36% | 0.7521 | false | true | false | true | **false** |
| `random_forest / market_only / raw_forward_return / 14d` | `top_5_equal_weight` | -30.40% | 0.2288 | false | false | false | false | **false** |

The strongest individually tested backtest was
`linear_ridge / market_only / raw_forward_return / 30d` (147.36% total return, 31.84% CAGR,
0.7521 Sharpe, -59.40% max drawdown). It beat ETH and the equal-weight universe but **did not
beat BTC or BTC/ETH 50-50**, so it failed alpha verification. Every candidate that passed the
signal screen was **market-only**; on-chain candidates did not produce a verified result in this
bounded run.

---

## 4. Architecture — the 9-agent pipeline

Every stage is an agent subclassing `AgentBase` (`agents/base.py`) implementing
`prepare() → run() → persist()`. The base `execute()` wraps each with retries, status tracking,
logging, and a run-provenance record in the SQLite registry (`metadata/agent_registry.db`).
Stages communicate **only** through file artifacts under `data/` (Parquet/JSON, hive-partitioned
where relevant) — never in-memory.

```
UniverseAgent → MarketDataAgent → OnChainAgent → FeatureAgent → LabelAgent
              → ModelAgent → AlphaResearchAgent → PortfolioAgent → BacktestAgent
```

| # | Agent | Role | Primary output |
|---|---|---|---|
| 1 | **UniverseAgent** | Builds the eligible, exclusion-filtered crypto universe (stablecoins/wrapped removed) | `data/raw/universe/universe_monthly.parquet` |
| 2 | **MarketDataAgent** | Ingests + validates daily OHLCV | `data/raw/market/market_ohlcv.parquet` |
| 3 | **OnChainAgent** | CoinMetrics + DeFiLlama network/protocol metrics | `data/raw/onchain/onchain_observations.parquet` |
| 4 | **FeatureAgent** | Leakage-safe market (v1) + on-chain (v2) features, pruned store | `data/features/full_features_pruned.parquet` |
| 5 | **LabelAgent** | Exact forward calendar log-return labels (7/14/30d) | `data/labels/label_matrix.parquet`, `modeling_dataset.parquet` |
| 6 | **ModelAgent** | Purged + embargoed walk-forward signal screening, Rank IC, SHAP | `data/predictions/model_leaderboard.parquet` |
| 7 | **AlphaResearchAgent** | Signal-only research grid (cannot claim alpha) | `data/research/research_leaderboard.parquet` |
| 8 | **PortfolioAgent** | Deterministic, prediction-only long-only allocations | `data/allocations/allocations_from_predictions.parquet` |
| 9 | **BacktestAgent** | **Sole alpha authority** — costs, benchmarks, verdict | `data/backtests/alpha_report.json`, `backtest_summary.parquet` |

Orchestration: `main.py` (CLI) and `pipelines/pipeline_runner.py` (programmatic); `run_all.sh`
runs the full pipeline plus a per-stage verifier. An optional APScheduler daemon
(`main.py schedule`, backed by `jobs/scheduler.py`) runs the stages on cron cadences.

### 4.1 Stage map — CLI subcommand, agent class, verifier, per-agent doc

Canonical stage order (`pipelines/pipeline_runner.py::run_full_pipeline`, `run_all.sh`):

```
universe → market → onchain → (clean) → features → labels → model(s) → portfolio → backtest
```
followed by `ablation` (run separately). The CLI entrypoint is `main.py` (subcommands);
`pipelines/pipeline_runner.py` runs the same DAG programmatically with per-stage validation
gates between every step.

| Stage | CLI subcommand | Agent class | Verifier | Per-agent doc |
|---|---|---|---|---|
| Universe | `universe` (+ `membership`) | `UniverseAgent` | `scripts/verify_universe_run.py` | [UNIVERSE_AGENT.md](UNIVERSE_AGENT.md) |
| Market | `market` | `MarketDataAgent` | `scripts/verify_market_run.py` | [MARKET_DATA_AGENT.md](MARKET_DATA_AGENT.md) |
| On-chain | `onchain` | `OnChainAgent` | `scripts/verify_onchain_run.py` | [ONCHAIN_AGENT.md](ONCHAIN_AGENT.md) |
| Features | `features` | `FeatureAgent` (V1+V2) | `scripts/verify_feature_run.py` | [FEATURE_AGENT.md](FEATURE_AGENT.md) |
| Labels | `labels` | `LabelAgent` | `scripts/verify_label_run.py` | [LABEL_AGENT.md](LABEL_AGENT.md) |
| Model | `models` / `model` | `ModelAgent` | `scripts/verify_model_run.py` | [MODEL_AGENT.md](MODEL_AGENT.md) |
| Portfolio | `portfolio` | `PortfolioAgent` | `scripts/verify_portfolio_run.py` | [PORTFOLIO_AGENT.md](PORTFOLIO_AGENT.md) |
| Backtest | `backtest` | `BacktestAgent` | `scripts/verify_backtest_run.py` | [BACKTEST_AGENT.md](BACKTEST_AGENT.md) |
| Alpha research | `alpha_research` | `AlphaResearchAgent` | `scripts/verify_alpha_research_run.py` | (signal-only; cannot claim alpha) |
| Ablation | `ablation` | `models/ablation.py::run_ablation` | — | — |

### 4.2 Per-stage responsibilities

**Universe** — Builds the eligible crypto universe as monthly point-in-time snapshots.
Resolves a source (`source: auto` prefers the survivorship-free keyless `cmc_web_pit` deep-PIT
dataset, else a local historical dataset, else live providers). Produces
`universe_monthly.parquet`, `universe_membership.parquet` (unique `cmc_id`s including
since-delisted coins), `exclusions_monthly.parquet`, and `universe_manifest.json`.
`main.py membership` (and `PipelineRunner.build_membership_daily`) expands the monthly file
into a daily PIT membership mask (`universe_membership_daily.parquet`). Gates on maturity
(≥365d), positive market cap, liquidity (≥$1M/day), on-chain coverage, and category exclusions
(stablecoins, wrapped, bridged, LST, synthetic).

**Market** — Consumes the universe symbol list and fetches daily OHLCV via CCXT (exchange-first:
Coinbase → Kraken → KuCoin → Gemini) with a keyless fallback waterfall (CryptoCompare → CoinGecko
→ CoinCap → CoinPaprika). **Binance is forbidden** (`fail_on_binance_usage: true`). Produces the
canonical flat panel `market_ohlcv.parquet` (the file every downstream stage reads), plus
`by_symbol/`, optional Hive partitions, and `market_manifest.json`. Flags forward-filled synthetic
bars (`is_synthetic_ohlc`), price anomalies (`is_price_anomaly`), stale feeds (`is_stale_price`);
enforces unit-correct `dollar_volume_usd`. `market_data_smoke` is the safe live proving mode
(`max_assets=3`, `backfill_days=60`) before scaling to the full research run.

**On-chain** — Consumes market symbols and fetches metrics from CoinMetrics (10 base-layer
metrics), DeFiLlama (TVL/volume/fees), and optionally Etherscan / The Graph (key-gated). The asset
set is `latest eligible universe` intersected with market assets having `passed_qa=true` and
`is_full_ohlcv=true`. Produces `onchain_observations.parquet` (long) and `onchain_wide.parquet`
(wide pivot), plus `onchain_coverage_report.parquet` and `onchain_manifest.json`. Never
forward-fills fabricated values, restricts observations to the symbol's market-calendar and as-of
date, refuses ambiguous reused tickers, and quarantines look-ahead-unsafe DeFiLlama pool metrics.
Optional providers (Etherscan, The Graph, Blockchair, Dune) never fabricate rows and never become
hard dependencies when keys are missing; coverage/manifest files record provider availability and
failure reasons.

**Clean** (optional, `PipelineRunner.run_clean`) — Normalizes raw market and on-chain data into
`data/cleaned/` via `pipelines/data_cleaner.py`. Not a separate `main.py` subcommand.

**Features** — Two-tier. `FeatureAgentV1` builds market features from OHLCV
(`market_features.parquet`, the backbone); `FeatureAgentV2` adds on-chain features
(`onchain_features.parquet`), left-joins lagged on-chain features onto the market calendar to form
`full_features.parquet`, and writes the correlation/VIF-pruned `full_features_pruned.parquet` plus
`feature_keep_list.json` / `feature_manifest.json`. On-chain features are lagged
(`onchain_lag_days: 1`) before join to avoid same-day leakage; winsorization and cross-sectional
z-scoring are computed strictly within-date. Leakage-token columns (`target`, `label`, `forward`,
`future`, `lead`) are rejected. See `docs/data_dictionary.md` for formulas.

**Labels** — Consumes market OHLCV and feature panels. Produces per-horizon `labels_{h}d.parquet`
(exact forward log return `ln(close_{t+h}/close_t)` for `horizons: [7, 14, 30]`),
`label_matrix.parquet` (wide, all-horizons-common target table), and `modeling_dataset.parquet`
(canonical features inner-joined with `label_matrix` on `symbol + date_ts` — the model's input),
plus `label_manifest.json` stamping `recommended_embargo_days = max(horizons)`. Verifies
`future_date_ts == date_ts + h days` exactly and drops rows that fail; final incomplete-horizon
rows per symbol are dropped; labels are never forward-filled or clipped.

**Model** — Trains a baseline (cross-sectional / historical mean), RandomForest, and LightGBM
across `horizons: [7, 14, 30]` and feature sets `{market_only, market_plus_onchain}` under
**purged + embargoed walk-forward CV** (`models/walk_forward.py`, expanding-window). Reads the
canonical `modeling_dataset.parquet`. Produces `model_predictions.parquet` (out-of-sample only,
ranked cross-sectionally by test date), `model_leaderboard.parquet` (Rank IC / t-stat / hit-rate
per config), `fold_metrics`, and `model_manifest.json` stamping
`alpha_status="not_evaluated_by_backtest"`. Rank IC is the primary quality metric; a signal gate
marks candidates but the model **cannot** claim alpha.

**Portfolio** — Consumes the **latest available predictions** as-of each rebalance date and market
OHLCV (optional model selection from `model_leaderboard.parquet`). Produces
`allocations_from_predictions.parquet` plus per-strategy copies and `allocation_manifest.json`
(which always stamps `alpha_verified=False`). Allocations are deterministic and forecast-driven:
prediction ranking, cross-sectional z-scores, inverse-volatility scaling, max-weight caps,
execution lag, turnover control. Long-only, gross ≤ 1.0; `date_ts` is the execution date, strictly
later than `signal_date`. Realized returns and label columns are **rejected** as inputs.

**Backtest** — The **sole alpha authority**. Consumes allocations + market OHLCV, applies target
weights from `execution_date` forward with a one-day shift (no same-day look-ahead), charges
transaction costs on realized per-day turnover, and compares against benchmarks (BTC, ETH,
BTC/ETH 50-50, equal-weight universe, cash) with sanity checks. Missing prices are zeroed into cash
rather than fabricated. Benchmark curves are clipped to the actual portfolio execution window.
Produces `equity_curves.parquet`, `backtest_summary.parquet`, `strategy_comparison.parquet`,
`benchmark_sanity_report.parquet`, `cost_sweep.parquet`, `alpha_report.json/md`, and
`backtest_manifest.json` stamping the boolean `alpha_verified`.

**Alpha research** — Signal-only research expansion across many model × feature-set ×
label-target × horizon combinations. `signal_only: true`; it **cannot** report verified alpha —
only `BacktestAgent` can.

**Ablation** — `models/ablation.py::run_ablation` compares `market_only` vs.
`market_plus_onchain` to isolate the marginal IC lift of on-chain features. Outputs
`data/reports/ablation_results.json`.

### 4.3 Agent pattern and run provenance (`agents/base.py`)

Every stage subclasses `AgentBase` (ABC) and implements `prepare()` (validate inputs/preconditions),
`run()` (core logic, returns output data), and `persist(result)` (write outputs). The base
`execute(max_retries, retry_backoff)` wraps these with the full lifecycle: status tracking
(`PENDING → RUNNING → SUCCESS/FAILED`), structured logging, **retries with exponential backoff**
(`retry_backoff ** attempt`), and registry upserts on every state change. Subclasses must **not**
reimplement this lifecycle. A QA hook (`qa_check`) logs shape/null counts into `self.metrics`.

On construction each agent gets a `run_id` (`uuid4()[:8]`) and computes
`config_hash = get_config_hash(cfg)` (first 16 hex chars of SHA-256 over the sort-keyed
JSON-serialized config). Each run is upserted into the SQLite registry at
`metadata/agent_registry.db` (table `agent_runs`, keyed by `run_id`), storing `agent_name`,
`status`, timestamps, `config_hash`, `snapshot_id`, `error_message`, `output_paths` (JSON), and
`metrics` (JSON). History is queryable via `AgentBase.get_run_history(registry_path)`.

Deterministic snapshot IDs are produced by `AgentBase.generate_snapshot_id(data_repr)`:
```
snapshot_id = sha256(f"{config_hash}:{data_repr}:{run_id}").hexdigest()[:12]
```
Identical config + data + run_id reproduce the same snapshot ID, which is stamped into artifacts
for provenance.

### 4.4 Providers layer (`providers/`)

One adapter per data source over a shared `providers/http_client.py` (`coingecko.py`,
`ccxt_market.py` / `ccxt_binance.py`, `coinmetrics.py`, `defillama.py`, `coinmarketcap.py`,
`coinpaprika.py`, `cryptocompare.py`, `coincap.py`, `etherscan.py`, `thegraph.py`, `blockchair.py`,
`dune.py`, `exchange_tradability.py`), with `providers/market_fallbacks.py` for provider failover.
Most providers have a free/community tier and work **without keys**.

### 4.5 Read-only consumers and runtime-optional components

`app/dashboard.py` (Streamlit, :8501) and `app/api.py` (FastAPI, :8000) **only read** pipeline
outputs — no business logic lives there. The dashboard is tolerant to missing files (shows empty
states with runnable commands). `jobs/scheduler.py` (APScheduler) runs the pipeline on cron
schedules. FastAPI, Streamlit, APScheduler, and vectorbt are **optional at runtime** — code
degrades gracefully when absent (e.g. `BacktestAgent` falls back if vectorbt is missing). The React
product dashboard under `frontend/` likewise only indexes existing generated outputs.

---

## 5. Research-integrity contracts (the non-negotiables)

These are the rules that make the negative result credible. They are enforced in code and in the
per-stage verifier scripts (`scripts/verify_<stage>_run.py`).

1. **Leakage-safe by construction.** Labels are *exact* forward calendar returns
   (`ln(close_{t+h}/close_t)`) with an exact-horizon calendar check. Model validation is
   **purged + embargoed walk-forward** CV (López de Prado): `train_end_purged =
   min(train_end_raw, test_start − (purge+1))`, embargo gap enforced, zero train/test overlap.
2. **Rank IC is the primary signal metric** — Spearman correlation of predicted vs realized
   cross-sectional returns — not RMSE, which is near-useless in low-signal financial data.
3. **Prediction-only portfolio inputs.** PortfolioAgent consumes only *predictions* as-of each
   rebalance date and rejects realized returns/labels as inputs (`execution_date > signal_date`).
4. **BacktestAgent is the single alpha authority.** Only it can set `alpha_verified`.
   AlphaResearchAgent is signal-only and *cannot* stamp `final_alpha_status=passed` without
   BacktestAgent-verified metrics — enforced in both the agent and its verifier.
5. **Survivorship honesty.** The universe limitation is written into every relevant manifest, not
   hidden (see §9).
6. **Deterministic reproducibility.** Fixed seeds (`random_seed: 42`, `CHF_SEED`), SHA-256
   `data_content_hash` fingerprints, and MLflow snapshot logging across agents.

---

## 6. Technology stack

Local-first, monolithic, CPU-only — deliberately avoiding distributed/JVM/LLM-in-the-loop
complexity.

| Layer | Technology |
|---|---|
| Orchestration | Python + APScheduler (cron-style DAG) |
| Data ingestion | CCXT / CoinGecko / Binance, CoinMetrics, DeFiLlama, CoinMarketCap |
| Storage | Apache Parquet, hive-partitioned (`year=/month=`) |
| Query engine | DuckDB (in-process, zero-copy over Parquet) |
| Features | pandas / numpy vectorized |
| Modeling | scikit-learn, LightGBM, Random Forest; SHAP for explainability |
| Validation | purged + embargoed walk-forward CV |
| Backtesting | transparent vectorized engine (+ optional VectorBT) |
| Experiment tracking | MLflow (local) — gated per agent |
| UIs | Streamlit dashboard, React product dashboard, FastAPI |

---

## 7. Deliverables — what was built and delivered

### 7.1 Pipeline & agents
- A complete, runnable **9-agent pipeline** with file-based contracts, run provenance, retries,
  and a matching **verifier script per stage**.
- **27 test files** (`tests/`), including the `*_research_mode.py` research-integrity suites that
  guard leakage and alpha-claim rules.

### 7.2 Production-hardening completed across the build (per-agent)
- **ModelAgent** — verified purged/embargoed CV; added **SHAP TreeExplainer** feature importance
  (`mean_abs_shap`, deterministic, non-fatal, tree-models only); `data_content_hash`; gated MLflow.
- **BacktestAgent** — `data_content_hash`; gated MLflow; **subperiod regime-robustness** analysis
  (`subperiod_performance.parquet`); graceful markdown fallback; full benchmark + cost-sweep suite.
- **LabelAgent** — confirmed exact-forward-return + calendar-purge math; `data_content_hash`;
  gated MLflow.
- **AlphaResearchAgent** — signal-only contract verified; `data_content_hash`; gated MLflow;
  graceful markdown fallback.
- **FeatureAgent / OnChainAgent / MarketDataAgent** — leakage-safe feature/label contracts,
  content hashing, and MLflow logging.
- **Gated MLflow + content-hash parity** now covers **7 agents**: market, onchain, feature, model,
  backtest, label, alpha_research. (See §9 for the two that remain.)

### 7.3 Websites (3 surfaces)
- **Streamlit dashboard** (`app/dashboard.py`) — verified running (HTTP 200); all displayed
  numbers cross-checked against real Parquet outputs (exact matches, no fabrication).
- **FastAPI** (`app/api.py`) — 6 read-only endpoints (`/health`, `/weights`, `/signals`,
  `/metrics`, `/runs`, `/latest_snapshot`), all verified 200. Two bugs fixed: `/latest_snapshot`
  (was always 404 — now falls back to `universe_manifest.json`) and `/signals` (now strips
  realized/forward columns for leakage-safety).
- **React product dashboard** (`frontend/`) — "CHF Alpha Research OS" marketing/product UI;
  agents list updated to the full 9-agent pipeline; displayed benchmark numbers verified real.

### 7.4 Documentation
- This master doc, plus per-agent references (`MARKET_DATA_AGENT.md`, `ONCHAIN_AGENT.md`,
  `UNIVERSE_AGENT.md`, `FEATURE_AGENT.md`, `LABEL_AGENT.md`, `MODEL_AGENT.md`,
  `PORTFOLIO_AGENT.md`, `BACKTEST_AGENT.md`),
  `RESEARCH_RESULTS_SUMMARY.md`, `BENCHMARK_VERIFICATION.md`, `data_dictionary.md` (LaTeX feature
  formulas), `REPRODUCIBILITY_COMMANDS.md`, and `NEXT_STEPS.md`.

---

## 8. What is done vs. what is open

### Done
- 9-agent leakage-safe pipeline, end-to-end runnable, with per-stage verifiers.
- Research-integrity contracts implemented and tested (single alpha authority, purged CV,
  prediction-only allocation, exact-forward labels).
- Negative result produced honestly and reproduced with verified numbers.
- Production hardening: SHAP, content hashes, gated MLflow (7 agents), subperiod robustness.
- Three web surfaces; Streamlit + FastAPI verified running with bugs fixed.
- Comprehensive documentation set.

### Open / honest caveats
- **Universe is latest-survivor**, not full point-in-time (CMC historical access blocked — §9).
- **MLflow + content-hash parity** not yet on `universe` and `portfolio`; `portfolio_agent.py`
  still has one raw `.to_markdown()` (works where `tabulate` is installed).
- **2 pre-existing MarketDataAgent test failures** (a USDT verifier-vs-fixture mismatch and the
  end-to-end pipeline integration test) remain in the MarketDataAgent track — independent of the
  hardening above.
- **React production build** requires more free RAM than was available at last attempt; the source
  is valid and builds when the host is not memory-starved (`dist` is a regenerable artifact).
- SHAP **beeswarm/summary plot images** not yet rendered (the numeric attribution is persisted).

---

## 9. Limitations

1. **CoinMarketCap historical access.** Three-year historical listings are blocked by the current
   API plan (historical-listings window observed ≈1 month; quotes ≈12 months; OHLCV historical
   unsupported). This blocks true three-year point-in-time universe construction from CMC.
2. **Latest-survivor universe.** The required, code-embedded limitation:
   > Results are conditional on the latest eligible survivor universe and may overstate historical
   > tradability because full historical membership and delisting data are not yet modeled.
3. **No verified point-in-time universe yet.** The pipeline must not claim point-in-time validity
   until historical active+inactive membership is available and verified.
4. **Sparse on-chain coverage.** On-chain data covered fewer assets than market data; sparse
   observations are kept honest (no forward-filling of unavailable on-chain data).
5. **Bounded alpha search.** The final expansion ran **80 experiments** and **skipped 1,090**
   configured experiments by budget — enough to evaluate current candidates, not to exhaust the
   grid.
6. **No verified alpha yet.** `alpha_verified=false` for every individually tested candidate.
7. **Research/education only.** No real-time execution engine; not investment advice.

---

## 10. Recommended next steps

1. Obtain full historical listings (active + inactive) over the complete research window.
2. Rebuild the universe with **point-in-time monthly membership** to remove survivor bias.
3. Rerun the full canonical pipeline on the corrected universe.
4. Expand the alpha-search budget well beyond the initial 80 experiments.
5. Prioritize excess-return / vol-adjusted / cross-sectional-rank labels and explicit
   bull/bear/high-vol regime filters.
6. Deepen robustness analysis (subperiod stability, cost & turnover sensitivity).
7. Improve on-chain mapping/coverage while keeping sparsity honest.
8. Retest candidates before vs. after survivorship correction.
9. Preserve the alpha-verification contract (only BacktestAgent may verify alpha).

> The right next step is **not** to tune the current result until it passes. It is to reduce
> universe bias, expand the grid, and rerun the same strict verification.

---

## 11. How to run / reproduce

```bash
# Setup
make setup                  # create .venv + install requirements
python scripts/bootstrap.py # data dirs, .env, import checks

# Full pipeline (or per-stage)
python main.py full         # universe → … → backtest, then ablation
./run_all.sh                # full pipeline + per-stage verifier
python main.py demo         # synthetic artifacts, no API keys

# Validate a stage
python scripts/verify_backtest_run.py --config configs/run_config.yaml

# Tests / syntax
make test
python -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py app/*.py

# UIs
make serve        # FastAPI on :8000
make dashboard    # Streamlit on :8501  (run `main.py demo` first if no data)
make mlflow       # MLflow UI on :5000
cd frontend && npm install && npm run build && npm run preview   # React product dashboard
```

Reproducibility rests on: config as the single source of truth
(`configs/run_config.yaml`), fixed seeds, deterministic `data_content_hash` fingerprints, and
MLflow snapshot logging. A reviewer running the same config against the same data state should
obtain the same leaderboard and the same `alpha_verified=false` verdict.

---

## 12. Repository layout (orientation)

```
agents/        # the 9 pipeline agents (+ universe variants) over AgentBase
features/      # feature engineering library (momentum, vol, beta, NVT, MVRV …)
models/        # walk-forward CV engine, ablation
pipelines/     # programmatic pipeline runner
providers/     # one adapter per data source over a shared http client
configs/       # run_config.yaml (source of truth) + loader
scripts/       # per-stage verifiers + diagnostics
app/           # Streamlit dashboard + FastAPI (read-only consumers)
frontend/      # React product dashboard (static, reads indexed artifacts)
data/          # generated artifacts (gitignored): raw/ cleaned/ features/ labels/
               #   predictions/ allocations/ backtests/ research/ reports/
docs/          # this doc + per-agent references + research reports
tests/         # 27 test files incl. research-integrity suites
metadata/      # SQLite run registry  ·  mlruns/  # MLflow tracking
```

### 12.1 Primary entry points

- `python main.py <command>` — single CLI for all stages and helpers (including `demo`, `serve`,
  `schedule`).
- `pipelines/pipeline_runner.py` — orchestration helper for running the full DAG (and a `--stage`
  CLI); put cross-stage control-flow changes here, not in `main.py`.
- `Makefile` — convenient wrappers (`make demo`, `make full`, `make dashboard`, `make smoke`, etc.).

### 12.2 Detailed data layout

All paths are config-driven (`configs/run_config.yaml::paths`) and resolved under the repo root by
`configs/config.py::resolve_path`. `data/` and `metadata/` are **gitignored** — generated locally.

| Path | Contents |
|---|---|
| `data/raw/` | Raw provider pulls: `universe/` (`universe_monthly.parquet`, membership masks), `market/` (`market_ohlcv.parquet`, `by_symbol/`, partitions), `onchain/` (`onchain_observations.parquet`, `onchain_wide.parquet`) |
| `data/staged/` | Intermediate staged data |
| `data/cleaned/` | Normalized market/on-chain (`*_clean.parquet`) from the clean stage |
| `data/features/` | `market_features.parquet`, `full_features.parquet`, `full_features_pruned.parquet`, `feature_keep_list.json`, `feature_manifest.json` |
| `data/labels/` | `labels_{7,14,30}d.parquet`, `label_matrix.parquet`, `modeling_dataset.parquet`, `label_manifest.json` |
| `data/predictions/` | `model_predictions.parquet`, `model_leaderboard.parquet`, candidate prediction files |
| `data/allocations/` | `allocations_from_predictions.parquet`, per-strategy allocations, `allocation_manifest.json` |
| `data/backtests/` | `equity_curves.parquet`, `backtest_summary.parquet`, `strategy_comparison.parquet`, `benchmark_sanity_report.parquet`, `cost_sweep.parquet`, `backtest_manifest.json` |
| `data/reports/` | `alpha_report.json/md`, `ablation_results.json` |
| `artifacts/` | Model pickles, feature-importance CSVs, fold-metrics JSON |
| `mlruns/` | MLflow experiment store (`mlflow.tracking_uri`) |
| `metadata/` | Runtime metadata incl. `agent_registry.db` |

### 12.3 Data contracts (schema cheatsheet)

These are the practical contracts other parts of the system assume. For full definitions, see
`docs/agent_contracts.md` and the code. Required columns by artifact:

| Artifact | Required columns |
|---|---|
| Market OHLCV | `symbol`, `date_ts`, `open`, `high`, `low`, `close`, `volume` |
| Feature store | `symbol`, `date_ts`, plus numeric feature columns |
| Labels | `symbol`, `date_ts`, `horizon_days`, `label_value` |
| Predictions | `symbol`, `date_ts`, `predicted_return`, `model_name`, `horizon_days`, `fold_id` |
| Allocations | `symbol`, `date_ts`, `weight` (plus strategy metadata) |
| Equity curves | `date_ts`, `portfolio_value`, `daily_return`, `backtest_name` |

When you change an artifact schema, update the agent that writes it, any downstream readers (often
`app/dashboard.py`, `app/api.py`, and later agents), and `docs/agent_contracts.md` /
`docs/data_dictionary.md`.

### 12.4 Configuration surface

`configs/run_config.yaml` is the primary configuration file and the source of truth.
`configs/config.py::load_config()` loads the YAML, then applies a small set of `.env` overrides
(`MLFLOW_TRACKING_URI`, `CHF_SEED`, `LOG_LEVEL`); paths are created on-demand when resolved via
`resolve_path(cfg, key)`. Universe exclusions live in `configs/universe_exclusions.yaml`. The file
also defines many **config sections** (e.g. `market_data_pit`, `features_pit`, `onchain_smoke`,
`portfolio_candidate_*`, `backtesting_candidate_*`) that `main.py --section <name>` merges over
their base section for variant / candidate runs. MLflow logging is gated per stage in config
(`mlflow.log_market_run`, `log_onchain_run`, `log_feature_run`, `log_model_run`, `log_backtest_run`)
and is non-fatal if MLflow is absent.

### 12.5 Extending CHF safely

Common extension points and where to change them:

1. **New data provider** — implement under `providers/` and call it from the relevant agent (usually
   `MarketDataAgent` or `OnChainAgent`).
2. **New feature** — implement the math in `features/feature_engineering.py`, then wire it into
   `agents/feature_agent.py`.
3. **New model** — implement training/inference in `agents/model_agent.py`; ensure outputs conform
   to the prediction contract.
4. **New strategy** — implement in `agents/portfolio_agent.py`; ensure outputs contain
   `symbol/date_ts/weight`.
5. **New dashboard view** — implement in `app/dashboard.py` using the existing cached loaders.

Prefer minimal, stage-local changes that keep the file-based contracts intact. This pipeline does
not "freeze" runtime-generated outputs (`mlruns/`, `artifacts/`, `data/`), and it makes no claim of
LLM autonomy in the financial core — the prediction core is deterministic.

---

## 13. One-paragraph summary

Project CHF is a reproducible, leakage-safe, 9-agent crypto quant research pipeline that tests
whether market and on-chain features yield cross-sectional alpha after honest validation, costs,
and benchmark comparison. It found statistically significant candidate *signals* (best signal-
screen Rank IC 0.0275, t-stat 7.10) but, after deterministic portfolio construction and
cost-aware backtesting against BTC, ETH, BTC/ETH 50-50, and an equal-weight universe, **no
candidate achieved verified alpha** (`alpha_verified=false`; strongest backtest
`linear_ridge/market_only/30d` at 147.36% return / 0.7521 Sharpe still lost to BTC and 50-50).
The deliverables are the full pipeline, per-stage verifiers, 27 test files, three web surfaces,
and a complete documentation set. The result is a credible, honestly-bounded negative — produced
by a system built to reject unsupported alpha claims rather than to flatter them.
