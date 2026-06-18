# CHF — Project Overview (Master Reference)

> Single-source reference for the CHF crypto-quant research pipeline. Every claim here is
> grounded in the repository (`main.py`, `agents/base.py`, `configs/`, `pipelines/`,
> `docs/`). Where a section summarizes a stage, it cross-links the authoritative per-agent
> doc. **No performance numbers in this document are invented** — the only headline result
> is the deliberate negative one: `alpha_verified=false`.

---

## 1. What This Is

CHF is a **reproducible quantitative crypto research pipeline** that tests one question:
*can market and on-chain features be used to construct crypto portfolios that outperform
BTC, ETH, BTC/ETH 50-50, and an equal-weight crypto universe after costs and realistic
validation?* It runs an end-to-end chain — universe construction, OHLCV ingestion, on-chain
ingestion, leakage-safe feature engineering, exact forward-return labeling, purged +
embargoed walk-forward model screening, deterministic prediction-only portfolio
construction, and transaction-cost-aware out-of-sample backtesting against multiple
benchmarks.

**The headline result is negative.** After deterministic portfolio construction,
transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting,
`alpha_verified=false` for **all** tested candidates. No verified alpha was found under
the tested configurations (see `docs/RESEARCH_RESULTS_SUMMARY.md`). The strongest
individually-tested backtest candidate was `linear_ridge / market_only /
raw_forward_return / 30d`, which beat ETH and the equal-weight universe but did **not**
beat BTC or BTC/ETH 50-50 — so it failed verification. **Preserving the integrity of that
negative result is the central design constraint of the entire codebase**: the system is
engineered so that no leakage, no overclaiming, and no single weak link can manufacture a
flattering result. A trustworthy "no" is the deliverable.

---

## 2. The End-to-End DAG

Canonical stage order (`pipelines/pipeline_runner.py::run_full_pipeline`, `run_all.sh`):

```
universe → market → onchain → (clean) → features → labels → model(s) → portfolio → backtest
```
followed by `ablation` (run separately). Every stage is an agent in `agents/` and has a
matching verifier `scripts/verify_<stage>_run.py`. The CLI entrypoint is `main.py`
(subcommands), and `pipelines/pipeline_runner.py` runs the same DAG programmatically with
per-stage validation gates between every step.

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
| Alpha research | `alpha_research` | `AlphaResearchAgent` | — | (signal-only; cannot claim alpha) |
| Ablation | `ablation` | `models/ablation.py::run_ablation` | — | — |

### Stage descriptions

**Universe** — Builds the eligible crypto universe as monthly point-in-time snapshots.
Resolves a source (`source: auto` prefers the survivorship-free keyless `cmc_web_pit`
deep-PIT dataset, else a local historical dataset, else live providers). Produces
`data/raw/universe/universe_monthly.parquet`, `universe_membership.parquet` (unique
`cmc_id`s incl. since-delisted coins), `exclusions_monthly.parquet`, and
`universe_manifest.json`. `main.py membership` (and `PipelineRunner.build_membership_daily`)
expands the monthly file into a daily PIT membership mask
(`universe_membership_daily.parquet`). Gates on maturity (≥365d), positive market cap,
liquidity (≥$1M/day), on-chain coverage, and category exclusions (stablecoins, wrapped,
bridged, LST, synthetic). See [UNIVERSE_AGENT.md](UNIVERSE_AGENT.md).

**Market** — Consumes the universe symbol list and fetches daily OHLCV via CCXT
(exchange-first: Coinbase → Kraken → KuCoin → Gemini) with a keyless fallback waterfall
(CryptoCompare → CoinGecko → CoinCap → CoinPaprika). **Binance is forbidden**
(`fail_on_binance_usage: true`). Produces the canonical flat panel
`data/raw/market/market_ohlcv.parquet` (the file every downstream stage reads), plus
`by_symbol/`, optional Hive partitions, and `market_manifest.json`. Flags forward-filled
synthetic bars (`is_synthetic_ohlc`), price anomalies (`is_price_anomaly`), and stale feeds
(`is_stale_price`); enforces unit-correct `dollar_volume_usd`. See
[MARKET_DATA_AGENT.md](MARKET_DATA_AGENT.md).

**On-chain** — Consumes market symbols and fetches metrics from CoinMetrics (10 base-layer
metrics), DeFiLlama (TVL/volume/fees), and optionally Etherscan / The Graph (key-gated).
Produces `data/raw/onchain/onchain_observations.parquet` (long) and `onchain_wide.parquet`
(wide pivot), plus `onchain_coverage_report.parquet` and `onchain_manifest.json`. Never
forward-fills fabricated values, restricts observations to the symbol's market-calendar and
as-of date, refuses ambiguous reused tickers, and quarantines look-ahead-unsafe DeFiLlama
pool metrics (`pool_tvl_usd`, `pool_apy`). See [ONCHAIN_AGENT.md](ONCHAIN_AGENT.md).

**Clean** (optional, in `PipelineRunner.run_clean`) — Normalizes raw market and on-chain
data into `data/cleaned/` via `pipelines/data_cleaner.py`. Not a separate `main.py`
subcommand.

**Features** — Two-tier. `FeatureAgentV1` builds market features from OHLCV
(`market_features.parquet`); `FeatureAgentV2` adds on-chain features
(`onchain_features.parquet`), joins to `full_features.parquet`, and writes the
correlation/VIF-pruned `full_features_pruned.parquet` plus `feature_keep_list.json` /
`feature_manifest.json`. On-chain features are lagged (`onchain_lag_days: 1`) and
winsorization / cross-sectional z-scoring are computed strictly within-date. Leakage-token
columns (`target`, `label`, `forward`, `future`, `lead`) are rejected. See
[FEATURE_AGENT.md](FEATURE_AGENT.md) and `docs/data_dictionary.md` for formulas.

**Labels** — Consumes market OHLCV and feature panels. Produces per-horizon
`labels_{h}d.parquet` (exact forward log return `ln(close_{t+h}/close_t)` for
`horizons: [7, 14, 30]`), `label_matrix.parquet` (wide), and `modeling_dataset.parquet`
(features ⨝ labels — the model's input), plus `label_manifest.json` stamping
`recommended_embargo_days = max(horizons)`. Verifies `future_date_ts == date_ts + h days`
exactly and drops rows that fail; rejects non-positive/non-finite prices. See
[LABEL_AGENT.md](LABEL_AGENT.md).

**Model** — Trains a baseline (cross-sectional / historical mean), RandomForest, and
LightGBM across `horizons: [7, 14, 30]` and feature sets `{market_only,
market_plus_onchain}` under **purged + embargoed walk-forward CV**. Produces
`data/predictions/model_predictions.parquet` (out-of-sample only), `model_leaderboard.parquet`
(Rank IC / t-stat / hit-rate per config), `fold_metrics`, and `model_manifest.json`
stamping `alpha_status="not_evaluated_by_backtest"`. Rank IC is the primary quality metric;
a signal gate marks candidates but the model **cannot** claim alpha. See
[MODEL_AGENT.md](MODEL_AGENT.md).

**Portfolio** — Consumes the **latest available predictions** as-of each rebalance date and
market OHLCV. Produces `data/allocations/allocations_from_predictions.parquet` plus
per-strategy copies and `allocation_manifest.json` (which always stamps
`alpha_verified=False`). Long-only, gross ≤ 1.0, liquidity and positive-signal filters,
execution strictly after the signal date (`execution_lag_days > 0`). Realized returns and
label columns are **rejected** as allocation inputs. See [PORTFOLIO_AGENT.md](PORTFOLIO_AGENT.md).

**Backtest** — The **sole alpha authority**. Consumes allocations + market OHLCV, applies
weights with a one-day shift (no same-day look-ahead), charges transaction costs on
per-day turnover, and compares against benchmarks (BTC, ETH, BTC/ETH 50-50, equal-weight
universe) with sanity checks. Produces `equity_curves.parquet`, `backtest_summary.parquet`,
`strategy_comparison.parquet`, `benchmark_sanity_report.parquet`, `cost_sweep.parquet`,
`alpha_report.json/md`, and `backtest_manifest.json` stamping the boolean `alpha_verified`.
See [BACKTEST_AGENT.md](BACKTEST_AGENT.md).

**Alpha research** (`alpha_research`) — Signal-only research expansion across many
model × feature-set × label-target × horizon combinations (`alpha_research` config). It
screens candidates but `signal_only: true` and it **cannot** report verified alpha — only
`BacktestAgent` can.

**Ablation** — `models/ablation.py::run_ablation` compares `market_only` vs.
`market_plus_onchain` to isolate the marginal IC lift of on-chain features. Outputs
`data/reports/ablation_results.json`.

---

## 3. Architecture

### Agent pattern (`agents/base.py`)
Every stage subclasses `AgentBase` (ABC) and implements three methods:
- `prepare()` — validate inputs and preconditions,
- `run()` — execute core logic and return output data,
- `persist(result)` — write outputs to disk.

The base `execute(max_retries, retry_backoff)` wraps these with the full lifecycle:
status tracking (`PENDING → RUNNING → SUCCESS/FAILED`), structured logging, **retries with
exponential backoff** (`retry_backoff ** attempt`), and registry upserts on every state
change. Subclasses must **not** reimplement this lifecycle. A QA hook (`qa_check`) logs
shape/null counts into `self.metrics`.

### Run provenance (SQLite registry)
On construction each agent gets a `run_id` (`uuid4()[:8]`) and computes
`config_hash = get_config_hash(cfg)` (first 16 hex chars of a SHA-256 over the
sort-keyed JSON-serialized config). Each run is upserted into a SQLite registry at
**`metadata/agent_registry.db`**, table `agent_runs`, keyed by `run_id`, storing
`agent_name`, `status`, timestamps, `config_hash`, `snapshot_id`, `error_message`,
`output_paths` (JSON), and `metrics` (JSON). History is queryable via
`AgentBase.get_run_history(registry_path)`.

### Deterministic snapshot IDs
`AgentBase.generate_snapshot_id(data_repr)` produces a **deterministic** 12-char ID:
```
snapshot_id = sha256(f"{config_hash}:{data_repr}:{run_id}").hexdigest()[:12]
```
Identical config + data + run_id reproduce the same snapshot ID, which is stamped into
artifacts for provenance.

### File-based contracts (`data/`)
Stages communicate **only** through artifacts under `data/` (Parquet/JSON), never via
in-memory handoffs. Paths are config-driven (`configs/run_config.yaml::paths`) and resolved
under the repo root by `configs/config.py::resolve_path`. Market data may be written
Hive-partitioned (`partitioned/symbol=.../year=.../`) in addition to the canonical flat
file. `data/` and `metadata/` are gitignored — generated locally. When editing a stage,
preserve the output schema that downstream stages, the dashboard, the API, and smoke tests
consume.

### Providers layer (`providers/`)
One adapter per data source over a shared `providers/http_client.py`
(`coingecko.py`, `ccxt_market.py` / `ccxt_binance.py`, `coinmetrics.py`, `defillama.py`,
`coinmarketcap.py`, `coinpaprika.py`, `cryptocompare.py`, `coincap.py`, `etherscan.py`,
`thegraph.py`, `blockchair.py`, `dune.py`, `exchange_tradability.py`), with
`providers/market_fallbacks.py` for provider failover. Most providers have a free/community
tier and work **without keys**.

### Read-only consumers
`app/dashboard.py` (Streamlit, :8501) and `app/api.py` (FastAPI, :8000) **only read**
pipeline outputs — no business logic lives there. `jobs/scheduler.py` (APScheduler) runs the
pipeline on cron schedules (`scheduler` config section). FastAPI, Streamlit, APScheduler,
and vectorbt are **optional at runtime** — code degrades gracefully when absent (e.g.
`BacktestAgent` falls back if vectorbt is missing; `main.py cmd_backtest` reports
`_VBT_AVAILABLE`). A separate React product dashboard lives under `frontend/` and likewise
only indexes existing generated outputs.

---

## 4. Research-Integrity Contracts (the non-negotiables)

These are the rules that protect the negative result. Violating any of them is a
correctness failure, not a style issue. The `tests/*_research_mode.py` suite guards them.

1. **Leakage-safe by construction.** Labels are exact forward calendar returns. Leakage
   guards exist across feature, label, model, portfolio, and backtest stages. Feature/label
   stages reject leakage-token columns and verify forward-date exactness.
2. **Purged + embargoed walk-forward CV.** Model validation uses expanding-window
   walk-forward with both purge and embargo ≥ horizon, so no training label's forward
   window reaches a test date. Imputation uses train-only statistics. **Rank IC is the
   primary quality metric.**
3. **Prediction-only portfolio inputs.** `PortfolioAgent` consumes the latest available
   *predictions* as-of each rebalance date with liquidity + positive-signal filters.
   Realized returns and labels are **rejected** as allocation inputs.
4. **`BacktestAgent` is the sole alpha authority.** It verifies or rejects alpha after
   transaction costs and benchmark sanity checks (BTC, ETH, BTC/ETH 50-50, equal-weight
   universe). `AlphaResearchAgent` does signal-only expansion and **cannot** claim alpha.
   No code path reports verified alpha except `BacktestAgent`; allocation/model manifests
   stamp `alpha_verified=False` / `alpha_status="not_evaluated_by_backtest"`.
5. **Two-tier features.** `FeatureAgentV1` builds market features from OHLCV;
   `FeatureAgentV2` adds on-chain features. The feature store is leakage-aware and may
   honor `feature_keep_list.json`.
6. **`config_hash` / snapshot reproducibility.** Deterministic `config_hash` and
   `snapshot_id` (SHA-256 derived) stamp every run for provenance and reproducibility.
7. **Gated MLflow.** MLflow logging is optional and gated per stage in config
   (`mlflow.log_market_run`, `log_onchain_run`, `log_feature_run`, `log_model_run`,
   `log_backtest_run`), non-fatal if MLflow is absent; tracking URI is `./mlruns` by
   default (overridable via `MLFLOW_TRACKING_URI`).

---

## 5. How To Run It

Setup:
```bash
make setup                  # create .venv and install requirements
python scripts/bootstrap.py # create data dirs, copy .env.example -> .env, verify imports
```

Run the pipeline (single CLI `main.py`, or `make <stage>`):
```bash
python main.py full          # entire pipeline end-to-end (via PipelineRunner)
./run_all.sh                 # full pipeline + per-stage verifier after each stage
python main.py demo          # synthetic artifacts (no API keys) for dashboard/tests/API
./run_all.sh --demo          # demo data only
```
Stages run individually in order, each with its verifier:
```bash
python main.py universe --config configs/run_config.yaml
python scripts/verify_universe_run.py --config configs/run_config.yaml
# replace 'universe' with: market | onchain | features | labels | model | portfolio | backtest
```
> Note the stage-alias drift: the model stage is `models` in the Makefile but `model`
> (singular) in `run_all.sh`; `main.py` accepts both (`cmd_model` delegates to `cmd_models`).

Serving & UIs:
```bash
make serve        # FastAPI (app/api.py) on :8000
make dashboard    # Streamlit (app/dashboard.py) on :8501  (run `main.py demo` first if no data)
make mlflow       # MLflow UI on :5000, backed by ./mlruns
python main.py schedule   # APScheduler daemon
```

Testing & diagnostics:
```bash
make smoke        # offline end-to-end validation (scripts/smoke_test.py)
make test         # full pytest suite (-v --tb=short)
python -m pytest tests/test_<name>.py -q   # single test file
python scripts/probe_api_readiness.py --config configs/run_config.yaml   # which providers/keys are live
python scripts/audit_pipeline_inputs.py --config configs/run_config.yaml # validate local inputs
```
The `*_research_mode.py` tests are the research-integrity suite — treat failures there as
correctness failures, not flakiness. Formatting: black + isort, line-length 100, py311
(`pyproject.toml`).

---

## 6. Data Layout

All paths are config-driven (`configs/run_config.yaml::paths`) and resolved under the repo
root. `data/` and `metadata/` are **gitignored**.

| Path | Contents |
|---|---|
| `data/raw/` | Raw provider pulls: `universe/` (`universe_monthly.parquet`, membership masks), `market/` (`market_ohlcv.parquet`, `by_symbol/`, partitions), `onchain/` (`onchain_observations.parquet`, `onchain_wide.parquet`) |
| `data/staged/` | Intermediate staged data |
| `data/cleaned/` | Normalized market/on-chain (`*_clean.parquet`) from the clean stage |
| `data/features/` | `market_features.parquet`, `full_features.parquet`, `full_features_pruned.parquet`, `feature_keep_list.json`, `feature_manifest.json` |
| `data/labels/` | `labels_{7,14,30}d.parquet`, `label_matrix.parquet`, `modeling_dataset.parquet`, `label_manifest.json` |
| `data/predictions/` | `model_predictions.parquet`, `model_leaderboard.parquet`, candidate prediction files |
| `data/allocations/` | `allocations_from_predictions.parquet`, per-strategy allocations, `allocation_manifest.json` |
| `data/backtests/` | `equity_curves.parquet`, `backtest_summary.parquet`, `strategy_comparison.parquet`, `benchmark_sanity_report.parquet`, `cost_sweep.parquet`, `vbt_stats.json`, `backtest_manifest.json` |
| `data/reports/` | `alpha_report.json/md`, `ablation_results.json` |
| `artifacts/` | Model pickles, feature-importance CSVs, fold-metrics JSON |
| `mlruns/` | MLflow experiment store (`mlflow.tracking_uri`) |
| `metadata/` | Runtime metadata incl. `agent_registry.db` |

`configs/run_config.yaml` also defines many **config sections** (e.g. `market_data_pit`,
`features_pit`, `onchain_smoke`, `portfolio_candidate_*`, `backtesting_candidate_*`) that
`main.py --section <name>` merges over their base section for variant / candidate runs.

---

## 7. Known Limitations

- **Survivorship / point-in-time universe caveat.** The CoinMarketCap Pro
  `/v1/cryptocurrency/listings/historical` endpoint was blocked on the current
  (Hobbyist) plan during the study (`docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`). The
  production universe was historically a latest-survivor baseline. *Note:* that doc records
  a survivorship-free path now exists via the **keyless** public `cmc_web_pit` data-API
  (ingested by `scripts/build_cmc_web_history.py`, source `cmc_web_pit`). Results that
  predate or do not use that source are conditional on the latest-survivor universe and may
  overstate historical tradability. The frozen research result and README still disclose
  the survivorship caveat.
- **Sparse on-chain coverage.** On-chain data is sparse relative to market coverage; dead
  coins legitimately lack on-chain data, so PIT runs use lower on-chain coverage floors.
- **No real-time execution.** There is no live execution engine; backtesting is research
  validation only. The scheduler is local, not a managed cloud service.
- **Research / education only.** Not financial advice. No verified alpha was found under
  the tested configurations.

---

## 8. Reproducibility & Provenance

- **Config is the source of truth.** `configs/run_config.yaml` drives all tunable behavior;
  `configs/config.py::load_config` loads it, resolves paths under the repo root, and applies
  `.env` overrides (`MLFLOW_TRACKING_URI`, `CHF_SEED`, `LOG_LEVEL`). Universe exclusions live
  in `configs/universe_exclusions.yaml`. `main.py` merges named config sections over their
  base section via `--section`.
- **Deterministic seeds.** `project.seed: 42` (overridable via `CHF_SEED`); model configs
  pin `random_seed: 42`. Synthetic demo data uses a fixed RNG seed (`default_rng(42)`).
- **Config hashing.** `get_config_hash(cfg)` = SHA-256 over the sort-keyed JSON config,
  truncated to 16 hex chars; stamped into every registry record.
- **Snapshot hashing.** `generate_snapshot_id(data_repr)` = SHA-256 of
  `config_hash:data_repr:run_id`, truncated to 12 hex chars; deterministic for identical
  inputs and stamped into artifacts.
- **Run registry.** Every agent run is recorded in `metadata/agent_registry.db`
  (`run_id`, `config_hash`, `snapshot_id`, `status`, `output_paths`, `metrics`), enabling
  full lineage reconstruction.
- **Manifests.** Each stage writes a `*_manifest.json` capturing inputs, coverage, gate
  flags, and (for model/portfolio/backtest) the alpha status.

### Reference docs
Start with `docs/RESEARCH_RESULTS_SUMMARY.md` and `docs/REPRODUCIBILITY_COMMANDS.md`.
Feature/label/metric formulas are in `docs/data_dictionary.md`; the DAG diagram is
`docs/architecture.mmd` / `docs/architecture.png`. Per-agent docs are linked in §2.
The frozen result set: `docs/ALPHA_FINDINGS_REPORT.md`,
`docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md`, `docs/BENCHMARK_VERIFICATION.md`,
`docs/FINAL_REVIEWER_PACKET.md`.

---

*CHF is a research and education project. Not financial advice. Headline result:
`alpha_verified=false` — no verified alpha under the tested configurations.*
