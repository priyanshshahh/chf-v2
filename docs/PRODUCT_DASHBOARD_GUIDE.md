# Product Dashboard Guide

The React product MVP dashboard presents Project CHF as a polished fintech-style product interface: **CHF Alpha Research OS**, an agentic crypto quant research platform for signal discovery, portfolio construction, benchmark-aware validation, and reproducible research operations.

It is separate from the Streamlit research/control dashboard.

## Purpose

Use the React dashboard to show CHF as:

- an alpha research operating system,
- an agentic crypto research pipeline,
- a benchmark-aware portfolio research engine,
- a reproducibility and risk-control platform,
- a productized analytics interface.

It does not rerun research, change outputs, or claim verified alpha.

## How To Run

Install frontend dependencies:

```bash
cd frontend
npm install
```

Run the dashboard:

```bash
npm run dev
```

Or from the repository root after dependencies are installed:

```bash
./run_product_dashboard.sh
```

Open:

```text
http://127.0.0.1:5173
```

## Difference From Streamlit Dashboard

| Dashboard | Purpose |
|---|---|
| Streamlit research dashboard | Local control center for reviewer operations, pipeline status, safe command execution, logs, and file inspection. |
| React product MVP dashboard | Product-style presentation layer for demos, investors, professors, and portfolio/research platform storytelling. |

The React dashboard is more visual and product-oriented. The Streamlit dashboard is more operational.

## Pages

### Landing / Hero

Shows CHF as a startup/product MVP with a strong hero, release status, product facts, and a mock control-plane panel. It uses real project facts:

- 9-agent pipeline
- release tag `v1.0-research-release`
- benchmark window `2022-12-15` to `2026-03-24`
- benchmark set: BTC, ETH, BTC/ETH 50-50, and equal-weight universe

### Why CHF Exists

Explains the real problem CHF solves: fragmented crypto data, unstable universes, look-ahead bias, cost-blind backtests, weak benchmarks, hard-to-reproduce notebooks, and the gap between ML forecasts and portfolio alpha. The page pairs each research failure mode with CHF's product answer.

### Product Console

Shows the dashboard as a control-center product:

- Research Pipeline
- Scheduler
- Benchmark Engine
- Reproducibility Pack
- Product UI
- Research UI

It also shows a pipeline status timeline from UniverseAgent through report generation.

The Generated Outputs panel summarizes local artifacts discovered during the Phase 12D inventory:

- graph artifacts,
- portfolio allocation artifacts,
- backtest/benchmark/report artifacts,
- reviewer documentation.

The dashboard does not create those files. It only indexes what already exists locally.

### Agent Pipeline

Shows clickable interactive cards for:

- UniverseAgent
- MarketDataAgent
- OnChainAgent
- FeatureAgent
- LabelAgent
- ModelAgent
- AlphaResearchAgent
- PortfolioAgent
- BacktestAgent

Each selected agent opens a detail panel with what it does, why it matters, its input, output, product value, and example artifact.

### Verified Outputs

Shows release proof rather than promotional strategy performance:

- BTC: `305.50%`
- ETH: `69.85%`
- BTC/ETH 50-50: `178.04%`
- Equal-weight universe: `30.39%`
- Python compile: PASS
- targeted tests: PASS
- markdown/link sanity: PASS
- final reviewer packet: complete

These are benchmark context values, not CHF strategy alpha claims.

Verified Outputs also links into:

- Visualization Gallery,
- Portfolio Viewer,
- Benchmark Intelligence,
- Reproducibility / Trust.

### Visualization Gallery

Shows indexed generated artifacts from the local project checkout. It supports:

- image previews when a safe image is available,
- report cards for Markdown/JSON/CSV/Parquet artifacts,
- category filters for Benchmark, Backtest, Portfolio, Model, Feature, QA, and Report,
- a search box for artifact names, sources, and paths.

Current local visual artifact preview:

- `docs/architecture.png`

The gallery includes a clear policy:

```text
Only existing generated artifacts are shown. No charts are fabricated.
```

Parquet artifacts are shown as artifact cards instead of parsed in the browser. This avoids adding heavy browser-side Parquet dependencies and avoids copying large generated data into the frontend bundle.

### Portfolio Viewer

Shows real generated PortfolioAgent artifacts when they exist locally. The current indexed allocation artifacts include:

- `data/allocations_candidate_lightgbm_14d/allocations_from_predictions.parquet`
- `data/allocations_candidate_linear_ridge_30d/allocations_from_predictions.parquet`
- `data/allocations_candidate_random_forest_14d/allocations_from_predictions.parquet`
- `data/allocations_alpha_candidates/allocation_manifest.json`

The viewer shows metadata such as:

- candidate signal,
- artifact path,
- allocation row count,
- unique symbol count,
- rebalance frequency,
- allocation mode,
- `alpha_verified=false`.

It does not show live holdings, orders, or current portfolio advice.

If portfolio artifacts are missing in another checkout, regenerate them through the normal pipeline path rather than adding fake files:

```bash
python3 main.py portfolio --config configs/run_config.yaml --section <portfolio_section>
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section <portfolio_section>
```

Then run BacktestAgent only through the verified workflow if alpha evaluation is required.

### Portfolio Intelligence

Shows product modules such as signal ranking, Top-K construction, risk-aware allocation, turnover/cost monitoring, benchmark comparison, experiment registry, and future live monitoring.

The mock product panel is explicitly labeled:

```text
Product interface mockup — not live trading
```

### Benchmark Intelligence

Turns the benchmark numbers into a product story:

- BTC was the strongest passive benchmark in the tested window.
- Any promoted strategy must beat a demanding crypto beta baseline after costs.
- Exact backtest windows matter more than public trailing chart windows.
- CHF uses benchmarks to prevent weak alpha claims.

### Reproducibility / Trust

Shows the release as an audit product:

- frozen release tag
- benchmark verification
- reviewer packet
- artifact manifest
- validation commands
- no hidden recomputation

### Roadmap

Shows MVP Now, Next, and Future product tracks, including authenticated command execution, experiment registry, strategy comparison, cloud deployment, model monitoring, and institutional research portal concepts.

### Demo / Investor View

Shows how to explain CHF to a professor/reviewer, quant recruiter, startup/product viewer, or future user.

## How To Present It

Suggested flow:

1. Start on Landing and explain CHF as an alpha research operating system.
2. Open Why CHF Exists and explain why crypto alpha research needs infrastructure.
3. Open Product Console and show the pipeline as an operating layer.
4. Open Agent Pipeline and click through the nine agents.
5. Open Verified Outputs and explain that benchmark values are verified context.
6. Open Visualization Gallery to show existing report/visual artifacts.
7. Open Portfolio Viewer to show real allocation artifacts without pretending they are live holdings.
8. Open Portfolio Intelligence to describe portfolio-construction capabilities.
9. Open Benchmark Intelligence to explain why BTC was a demanding baseline.
10. Open Reproducibility / Trust to show the reviewer package and validation commands.
11. End with Roadmap or Demo / Investor View, depending on the audience.

## What Not To Claim

Do not claim:

- verified alpha was found,
- CHF currently beats BTC,
- the system is a live trading product,
- portfolio artifacts are current holdings or advice,
- the dashboard is financial advice,
- roadmap modules are already proven returns.

Correct product positioning:

```text
CHF is designed to identify and validate crypto alpha candidates using agentic research automation, walk-forward testing, deterministic portfolio construction, and benchmark-aware backtesting.
```

Correct research result:

```text
No verified alpha found under tested configurations.
```
