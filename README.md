# Project CHF — Crypto Alpha Research Pipeline

Project CHF is a reproducible quantitative crypto research **and simulated fund-operations** platform. It tests whether market and on-chain features contain tradable cross-sectional alpha after leakage-safe modeling, deterministic portfolio construction, transaction costs, benchmark sanity checks, and out-of-sample backtesting — and then runs the full institutional apparatus around that research: a purged/embargoed walk-forward with a **single alpha authority**, an advanced ML layer (XGBoost, CatBoost, walk-forward stacking, triple-barrier + meta-labeling), a **short-side-capable paper-trading engine** across **13 virtual books** (incl. delta-neutral carry and stat-arb sleeves), dual-book accounting that reconciles at 0 bps, a 10-module monitoring suite, and institutional reporting. It spans ~76 first-class modules and a 628-case pytest suite across 40 test files (measured 2026-07-06: 625 passed, 2 skipped, 1 known failure — a `test_pipeline_integration.py` end-to-end case whose fixture predates the research-mode canonical-input contract in `configs/run_config.yaml`).

The headline result is deliberately, honestly negative: **`alpha_verified = false`.** CHF's value as a portfolio piece is the *process rigor and intellectual honesty* that produced that result — not a fabricated Sharpe. See the **[recruiter-facing case study →](docs/CASE_STUDY.md)** and the **[honest scaling roadmap →](docs/SCALING_ROADMAP.md)**.

Start with the final research package in [docs/RESEARCH_RESULTS_SUMMARY.md](docs/RESEARCH_RESULTS_SUMMARY.md), then use [docs/REPRODUCIBILITY_COMMANDS.md](docs/REPRODUCIBILITY_COMMANDS.md) for reproduction notes. To see the whole system run end-to-end on shipped local data with no API keys, run **`./scripts/demo.sh`**.

**Repository:** [github.com/priyanshshahh/chf-v2](https://github.com/priyanshshahh/chf-v2) — clone and track the `main` branch.

> **Reviewers / recruiters, start here:** [docs/CASE_STUDY.md](docs/CASE_STUDY.md) — CHF as a portfolio piece. The headline is an *honest negative result* (`alpha_verified=false`), and the rigor that produces it — survivorship-free universe, purged+embargoed walk-forward, a single alpha authority, triple-barrier/meta-labeling, dual-book accounting reconciling at 0 bps — is the point. See the [scaling roadmap](docs/SCALING_ROADMAP.md) for the honest path to a verified edge, and run the whole thing in one command: `./scripts/demo.sh`.

**What CHF is now:** ~76 deterministic Python modules (no LLM in any decision path) spanning a 9-stage research pipeline, an advanced ML layer (ridge / RF / LightGBM / XGBoost / CatBoost / walk-forward stacking, optional regime-conditional + meta-labeling), a multi-strategy paper-trading fund (13 virtual books incl. trend, cross-sectional momentum, technical ensemble, **delta-neutral carry**, **stat-arb**, **DeFi-yield**), 21 data-provider adapters (derivatives, DeFiLlama, macro), and an institutional ops stack (dual-book accounting, 10 monitors, risk governance, orchestration with human approval gates, monthly investor letter, Docker/systemd deploy).

## Research Question

Can market and on-chain features be used to construct crypto portfolios that outperform BTC, ETH, BTC/ETH 50-50, and an equal-weight crypto universe after costs and realistic validation?

## Current Research Result

- Signal search found statistically promising candidates.
- BacktestAgent tested candidate portfolios after transaction costs and benchmark sanity checks.
- `alpha_verified=false` for all tested candidates.
- No verified alpha found under tested configurations.
- **2026-07-05 canonical refresh:** the canonical model stage now runs real ML models (`linear_ridge`, `random_forest`, `lightgbm`) alongside the baseline (600,888 predictions, best Rank IC 0.064). PortfolioAgent auto-selected `linear_ridge / market_plus_onchain / 14d`; the refreshed canonical backtest (2025-03-25 → 2026-05-12) peaked at `top_5_vol_scaled` with -5.53% total return (Sharpe 0.171) — still `alpha_verified=false`. Benchmarks over the same window: BTC -8.13%, ETH +9.83%, BTC/ETH 50-50 +2.96%, equal-weight universe -46.43% — the best strategy beats BTC and equal-weight but loses to ETH and the 50-50 mix. The canonical market dataset is now survivorship-free full-history (268,592 rows; BTC/ETH history back to 2021) after a ccxt pagination bugfix that had silently truncated exchange history. All candidate strategies now also run in daily **paper trading** (see [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md)) as ongoing out-of-sample validation.
- Strongest candidate: `linear_ridge / market_only / raw_forward_return / 30d`.
- That candidate beat ETH and the equal-weight universe, but did not beat BTC or BTC/ETH 50-50.
- The latest-survivor universe limitation remains because CMC 3-year historical listings were blocked by current API plan access.

| Candidate | Best Strategy | Return | CAGR | Sharpe | Max DD | Alpha Verified |
|---|---|---:|---:|---:|---:|---:|
| lightgbm / market_only / raw_forward_return / 14d | top_20_vol_scaled | 45.39% | 12.10% | 0.5030 | -71.45% | false |
| linear_ridge / market_only / raw_forward_return / 30d | top_5_equal_weight | 147.36% | 31.84% | 0.7521 | -59.40% | false |
| random_forest / market_only / raw_forward_return / 14d | top_5_equal_weight | -30.40% | -10.47% | 0.2288 | -86.86% | false |

| Benchmark | Total Return |
|---|---:|
| BTC | 305.50% |
| ETH | 69.85% |
| BTC/ETH 50-50 | 178.04% |
| Equal-weight universe | 30.39% |

## Interpreting the Null Result (2026-07 update)

A statistically overwhelming signal and a null economic result are not a contradiction — that separation *is* the finding.

- **The cross-sectional ranking signal is real.** In the walk-forward signal screen (`data/predictions/alpha_model_leaderboard.parquet`), the strongest configuration — `elastic_net / market_only / 30d` — reaches a mean Rank IC of **0.1271 at a t-statistic of 9.60** across folds (`signal_gate_passed = true`, `alpha_backtest_status = not_run`), with the leading candidates clustered at t ≈ 4.4–9.6. A t-stat near 9.6 is p ≈ 10⁻²¹: the signal's correlation with forward cross-sectional returns is not in doubt.
- **No configuration converts that signal into verified alpha.** Every candidate that clears the *signal* gate still returns `alpha_verified = false` at the *alpha* gate, after portfolio construction, transaction costs and benchmark comparison. Statistical significance and economic significance are independent quantities, and this pipeline is built to keep them independent — hence the headline is one honest boolean, not a Sharpe.

Two **structural constraints** — not signal absence — are the leading explanations, and each is a concrete, falsifiable next experiment:

1. **The long-only constraint is binding.** The edge is a cross-sectional *ranking*; its information lives in the market-neutral subspace `{w : Σwᵢ = 0}`. A long-only book cannot occupy that subspace — it is forced to β ≈ 1 and inherits market direction. In the canonical window the equal-weight universe fell **−46.4%**, so even a good ranking loses money when it can only be expressed long. Prediction: a **long/short** variant should recover the ranking's value.
2. **The out-of-sample window is a single regime.** The canonical backtest (2025-03-25 → 2026-05-12) is ~14 months of a predominantly **bear** tape — exactly where cross-sectional momentum is known to underperform. The 2021 bull run, the 2022 crash and the 2023–24 recovery all sit inside the *training* split, never tested out-of-sample. Widening the walk-forward (≈14 → ≈50 folds) walks the OOS start back toward 2022, spanning bull + crash + recovery.

The defensible one-line summary: **under a long-only book, evaluated over a single bear-market regime at 20 bps costs, a signal significant at p ≈ 10⁻²¹ produced no tradable alpha.** That is a result about the constraint set — not a verdict on the signal. See [docs/RESEARCH_RESULTS_SUMMARY.md](docs/RESEARCH_RESULTS_SUMMARY.md) and the [scaling roadmap](docs/SCALING_ROADMAP.md) for the experiments this motivates.

## Pipeline Architecture

```text
UniverseAgent
→ MarketDataAgent
→ OnChainAgent
→ FeatureAgent
→ LabelAgent
→ ModelAgent
→ AlphaResearchAgent
→ PortfolioAgent
→ BacktestAgent
```

- UniverseAgent: builds the eligible crypto universe.
- MarketDataAgent: ingests and validates OHLCV data.
- OnChainAgent: ingests CoinMetrics, DeFiLlama, and other provider metrics where available.
- FeatureAgent: builds leakage-safe market/on-chain features.
- LabelAgent: creates exact forward calendar labels.
- ModelAgent: performs purged walk-forward signal screening.
- AlphaResearchAgent: signal-only research expansion; cannot claim alpha.
- PortfolioAgent: creates deterministic allocations from prediction-only files.
- BacktestAgent: final alpha authority; verifies or rejects alpha after costs and benchmarks.

## Methodology Highlights

- Exact forward calendar labels.
- Leakage guards throughout feature, label, model, portfolio, and backtest stages.
- Purged and embargoed walk-forward validation.
- Cross-sectional rank metrics, including Rank IC and top/bottom spreads.
- Prediction-only portfolio inputs; realized returns and labels are rejected from allocation inputs.
- Transaction costs included in backtests.
- BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks.
- Benchmark sanity checks for date alignment and impossible benchmark behavior.
- No alpha claim unless BacktestAgent verifies it.

## Repository Layout

```text
agents/       deterministic pipeline agents
providers/    API/provider adapters
features/     feature engineering utilities
models/       walk-forward validation utilities
pipelines/    orchestration helpers
scripts/      readiness probes, verifiers, candidate export
configs/      run configuration and exclusions
tests/        unit/research-integrity tests and fixtures
docs/         final reports and reproducibility notes
schemas/      schema definitions
data/         generated locally; ignored by Git
metadata/     local runtime metadata; ignored by Git
```

## Quick Start

```bash
git clone https://github.com/priyanshshahh/chf-v2.git
cd chf-v2

python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install -r requirements.txt

cp .env.example .env
# edit locally if needed; never commit real keys
```

## How To Run CHF

1. Install dependencies and create a local `.env` file for any provider keys you want to use.
2. Check API readiness:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

3. Run the full pipeline:

```bash
./run_all.sh
```

4. Or run one stage at a time:

```bash
python3 main.py universe --config configs/run_config.yaml
python3 scripts/verify_universe_run.py --config configs/run_config.yaml
```

Replace `universe` with `market`, `onchain`, `features`, `labels`, `model`, `portfolio`, or `backtest` and run the matching verifier.

Run the paper-trading books (virtual, per-strategy forward validation):

```bash
python3 main.py papertrade --config configs/run_config.yaml
```

See [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md).

## Institutional Fund Operations

On top of paper trading, CHF runs five institutional-grade fund-operations layers (all virtual — simulated cash and simulated fees, results still `alpha_verified=false`):

- **Accounting (`accounting/`)** — dual-book NAV reconciliation (engine vs independent shadow ledger), gross and net-of-fees NAV per book with a simulated 2%/20% fee schedule, and a hash-chained audit log under `data/accounting/`.
- **Monitoring (`monitoring/`)** — daily monitors for data quality, signal health, execution quality, model decay, risk, shadow NAV, pipeline staleness and champion/challenger, writing `data/reports/monitoring/*.json` and the readiness verdict `data/readiness/daily_quality_verdict.json`.
- **Portfolio risk layer (`portfolio/`)** — vol targeting, liquidity/concentration limits and a drawdown de-risking state machine producing `data/risk/risk_adjusted_weights_*.parquet`, `risk_audit_*.json` and `drawdown_state_*.json` before any paper order.
- **Strategy sleeves (`strategies/`)** — seven deterministic sleeves (trend, cross-sectional momentum, carry, technical ensemble, plus a true **delta-neutral carry** sleeve, a **stat-arb** pairs sleeve, and a **DeFi-yield** sleeve) with their own paper books, a market-regime classifier (`data/strategies/regime_daily.parquet`) and a sleeve capital allocator whose output is a **proposal only**, gated by the human approval queue.
- **Monthly letter (`reports/monthly_letter.py`)** — a deterministic institutional letter (fund summary, benchmarks, risk metrics, regime, sleeves, open alerts, disclaimers) written to `artifacts/letters/YYYY-MM_letter.md` + `.json`.

New CLI commands: `python3 main.py nav` (reconcile + accounting NAV), `python3 main.py monitor` (all monitors; exit 1 on any alert) and `python3 main.py letter` (monthly letter). The scheduler (`python3 main.py schedule`) also runs the monitoring suite as daily/weekly jobs alongside the existing pipeline and paper-trading jobs. Background and design notes: [docs/FUND_PRACTICES_RESEARCH.md](docs/FUND_PRACTICES_RESEARCH.md) and [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md).

5. Launch the dashboard after local data has been generated:

```bash
streamlit run app/dashboard.py
```

Beginner guides:

- [User Guide](docs/USER_GUIDE.md)
- [API Keys And Data Sources](docs/API_KEYS_AND_DATA_SOURCES.md)
- [Dashboard Guide](docs/DASHBOARD_GUIDE.md)

## Dashboard and Automation

CHF includes a Streamlit control-center dashboard for local reviewer demos. It presents the frozen research release, benchmark verification, final result, limitations, reproducibility links, pipeline status, safe file browsing, and optional local run controls.

Run the dashboard locally:

```bash
./run_dashboard.sh
# or
streamlit run app/dashboard.py
```

Pipeline run buttons are guarded by a confirmation checkbox and execute only existing local CLI commands. They may update local generated outputs under `data/`; they do not run automatically when the dashboard opens.

Start the local scheduler:

```bash
./run_scheduler.sh
```

The scheduler runs locally until stopped. It is not a managed cloud service, and BacktestAgent remains manual/research-validation only by default.

Public deployments should disable pipeline run buttons or protect them behind authentication. The dashboard is for research and education only, not financial advice.

### Live demo (Streamlit Community Cloud)

The dashboard is staged for a free, read-only Streamlit Community Cloud deployment — no API keys or secrets required. `streamlit_app.py` at the repo root boots the dashboard in read-only demo mode (`CHF_DASHBOARD_READ_ONLY=1`, pipeline run buttons disabled) and generates synthetic demo artifacts on first start, since real `data/` outputs are gitignored. The demo shows the same honest headline: `alpha_verified=false`.

```bash
# local equivalent of the cloud deployment
streamlit run streamlit_app.py
```

Owner deploy steps: see [DEPLOY.md](DEPLOY.md).

See [Dashboard Guide](docs/DASHBOARD_GUIDE.md).

## Production MVP Dashboard

CHF also includes a separate React product/MVP dashboard under `frontend/`. It presents **CHF Alpha Research OS** as a fintech-style product interface for agentic crypto research automation, portfolio intelligence, benchmark monitoring, reproducible review, and generated-artifact exploration. It is separate from the Streamlit research dashboard and does not recompute research or touch generated outputs.

**Live demo:** **[chf-dashboard.vercel.app](https://chf-dashboard.vercel.app)** — a static, prebuilt deployment on Vercel (`npm run bake` bakes the committed `data/` artifacts into `frontend/public/data/*.json`, then `vite build`). It is read-only and recomputes nothing.

Run it directly:

```bash
cd frontend
npm install
npm run dev
```

Or after dependencies are installed:

```bash
./run_product_dashboard.sh
```

The React dashboard uses verified benchmark values from the frozen research docs and frames them as benchmark context, not as CHF strategy alpha. It also includes a Visualization Gallery and Portfolio Viewer that index existing local generated outputs only. Graphs and portfolio artifacts are not fabricated, and portfolio views are historical research artifacts rather than live holdings or financial advice. Public deployments should protect or disable any future command-execution features.

See [Product Dashboard Guide](docs/PRODUCT_DASHBOARD_GUIDE.md).

Run syntax validation:

```bash
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
```

Run targeted research-integrity tests:

```bash
python3 -m pytest \
  tests/test_alpha_research_agent.py \
  tests/test_model_agent_research_mode.py \
  tests/test_portfolio_agent_research_mode.py \
  tests/test_backtest_agent_research_mode.py \
  tests/test_universe_agent_research_mode.py \
  tests/test_market_data_agent_research_mode.py \
  tests/test_onchain_agent_research_mode.py \
  tests/test_feature_agent_research_mode.py \
  tests/test_label_agent_research_mode.py \
  -q
```

Probe API readiness:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

Audit local pipeline inputs:

```bash
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

See [docs/REPRODUCIBILITY_COMMANDS.md](docs/REPRODUCIBILITY_COMMANDS.md) for complete reproduction notes.

## Data And API Notes

**GitHub carries source only.** After cloning, generated outputs stay on your machine:

| Local only (gitignored) | Regenerate with |
|---|---|
| `data/` — pipeline Parquet/JSON | `python main.py demo` or `python main.py full` |
| `artifacts/` — model pickles, FI CSVs | same |
| `mlruns/` — MLflow experiment store | same |
| `cmc_complete/data/` — CMC parquet/JSON/CSV bulk (single canonical folder) | `python -m src.cmc.maintain` / `cmc_complete/code/keyless_extractor/extract_cmc_daily_history.py` |
| `.env` — API keys | copy from `.env.example` |

- CoinMetrics Community and DeFiLlama may work without keys.
- CoinMarketCap historical listings for a 3-year point-in-time universe were blocked by current plan access during this study.
- `.env` must never be committed; use `.env.example` for non-secret local setup guidance only.

## Security

Before pushing to GitHub, install the local secret-scan hook and read [docs/SECURITY.md](docs/SECURITY.md):

```bash
make hooks          # blocks commits that contain .env or API keys
```

GitHub Actions runs the same checks on every push/PR (`.github/workflows/secret-scan.yml`). Use a **private repository** if you do not want the source code copied publicly.

## Documentation

**Research results**
- [Research Results Summary](docs/RESEARCH_RESULTS_SUMMARY.md) — headline negative result
- [Alpha Backtest Verification Report](docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md)
- [Alpha Signal Search Report](docs/ALPHA_SIGNAL_SEARCH_REPORT.md)
- [Benchmark Verification](docs/BENCHMARK_VERIFICATION.md)

**Project & methodology**
- [Project Reference](docs/PROJECT_CHF_DOC.md)
- [Agent Contracts](docs/agent_contracts.md)
- [Data Dictionary](docs/data_dictionary.md)
- Per-agent technical docs: [Universe](docs/UNIVERSE_AGENT.md) · [Market Data](docs/MARKET_DATA_AGENT.md) · [On-Chain](docs/ONCHAIN_AGENT.md) · [Feature](docs/FEATURE_AGENT.md) · [Label](docs/LABEL_AGENT.md) · [Model](docs/MODEL_AGENT.md) · [Portfolio](docs/PORTFOLIO_AGENT.md) · [Backtest](docs/BACKTEST_AGENT.md)

**Run & reproduce**
- [Reproducibility Commands](docs/REPRODUCIBILITY_COMMANDS.md)
- [User Guide](docs/USER_GUIDE.md)
- [API Keys And Data Sources](docs/API_KEYS_AND_DATA_SOURCES.md)
- [CoinMarketCap Reference](docs/COINMARKETCAP.md)
- [Dashboard Guide](docs/DASHBOARD_GUIDE.md) · [Product Dashboard Guide](docs/PRODUCT_DASHBOARD_GUIDE.md)

**Roadmap & security**
- [Roadmap & Known Limitations](docs/NEXT_STEPS.md)
- [Security](docs/SECURITY.md)

## Limitations

- The current production universe is a latest-survivor baseline, not a full point-in-time historical universe.
- CoinMarketCap 3-year historical listings access was blocked by the current API plan.
- On-chain coverage is sparse relative to market coverage.
- There is no real-money execution engine; execution is virtual paper trading only (internal simulator, optional Alpaca paper API).
- No verified alpha found under tested configurations.
- Research and education only; not financial advice.

## License

MIT License.

For research and educational purposes only. Not financial advice.
