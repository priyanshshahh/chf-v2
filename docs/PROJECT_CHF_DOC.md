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
(`main.py schedule`) runs the stages on cron cadences.

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
  `PORTFOLIO_AGENT.md`, `BACKTEST_AGENT.md`), `PROJECT_OVERVIEW.md`,
  `RESEARCH_RESULTS_SUMMARY.md`, `BENCHMARK_VERIFICATION.md`, `data_dictionary.md` (LaTeX feature
  formulas), `REPRODUCIBILITY_COMMANDS.md`, `LIMITATIONS_AND_NEXT_STEPS.md`, and `NEXT_STEPS.md`.

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
