# Project CHF User Guide

This guide explains how to set up and run CHF from a clean local checkout. CHF is a research pipeline, not a trading product. The current final result is: no verified alpha found under tested configurations.

## 1. Install

Clone the repository and enter it:

```bash
git clone https://github.com/priyanshshahh/chf.git
cd chf
```

Create and activate a Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run a syntax check:

```bash
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
```

## 2. Add API Keys

CHF reads local environment variables and also loads a project-root `.env` file if present.

Create a local `.env` file:

```bash
touch .env
```

Add only the keys you actually have:

```text
CMC_API_KEY=your_local_key
COINGECKO_API_KEY=your_local_key
ETHERSCAN_API_KEY=your_local_key
GRAPH_API_KEY=your_local_key
THEGRAPH_API_KEY=your_local_key
DUNE_API_KEY=your_local_key
```

Do not commit `.env`. Do not paste real API keys into code, docs, screenshots, or chat logs.

See [API Keys And Data Sources](API_KEYS_AND_DATA_SOURCES.md) for key-by-key details.

## 3. Run API Readiness Checks

Before running data collection, check provider access:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
```

This writes:

```text
docs/API_DATA_READINESS_AUDIT.md
data/readiness/api_probe_results.json
```

Then audit local pipeline inputs:

```bash
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

This writes:

```text
data/readiness/pipeline_input_audit.json
```

## 4. Run The Full Pipeline

The easiest way to run the normal pipeline is:

```bash
./run_all.sh
```

The runner executes each stage and then runs its verifier. If a verifier fails, stop and fix that stage before continuing.

Pipeline order:

```text
UniverseAgent
→ MarketDataAgent
→ OnChainAgent
→ FeatureAgent
→ LabelAgent
→ ModelAgent
→ PortfolioAgent
→ BacktestAgent
```

## 5. Run Individual Stages

Each stage can be run through `main.py`.

Universe:

```bash
python3 main.py universe --config configs/run_config.yaml
python3 scripts/verify_universe_run.py --config configs/run_config.yaml
```

Market data:

```bash
python3 main.py market --config configs/run_config.yaml
python3 scripts/verify_market_run.py --config configs/run_config.yaml
```

On-chain data:

```bash
python3 main.py onchain --config configs/run_config.yaml
python3 scripts/verify_onchain_run.py --config configs/run_config.yaml
```

Features:

```bash
python3 main.py features --config configs/run_config.yaml
python3 scripts/verify_feature_run.py --config configs/run_config.yaml
```

Labels:

```bash
python3 main.py labels --config configs/run_config.yaml
python3 scripts/verify_label_run.py --config configs/run_config.yaml
```

Model screening:

```bash
python3 main.py model --config configs/run_config.yaml
python3 scripts/verify_model_run.py --config configs/run_config.yaml
```

Portfolio construction:

```bash
python3 main.py portfolio --config configs/run_config.yaml
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml
```

Backtest:

```bash
python3 main.py backtest --config configs/run_config.yaml
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml
```

Some research sections use `--section`, for example:

```bash
python3 main.py portfolio --config configs/run_config.yaml --section portfolio_alpha_candidates
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_alpha_candidates
```

## 6. Run Alpha Research

AlphaResearchAgent searches for signal candidates. It is signal-only and cannot claim alpha.

```bash
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
python3 scripts/verify_alpha_research_run.py --config configs/run_config.yaml --section alpha_research
```

Only BacktestAgent can verify alpha.

## 7. Run The Dashboard

Generate pipeline outputs first. Then run:

```bash
streamlit run app/dashboard.py
```

or:

```bash
./run_dashboard.sh
```

Open:

```text
http://localhost:8501
```

See [Dashboard Guide](DASHBOARD_GUIDE.md).

## 8. Find Outputs

Generated outputs are local and ignored by Git.

Common output locations:

```text
data/raw/universe/
data/raw/market/
data/raw/onchain/
data/features/
data/labels/
data/predictions/
data/allocations/
data/backtests/
data/research/
```

Important final reports:

```text
docs/RESEARCH_RESULTS_SUMMARY.md
docs/ALPHA_FINDINGS_REPORT.md
docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md
docs/ALPHA_SIGNAL_SEARCH_REPORT.md
docs/LIMITATIONS_AND_NEXT_STEPS.md
docs/REPRODUCIBILITY_COMMANDS.md
docs/API_DATA_READINESS_AUDIT.md
docs/CMC_HISTORICAL_ACCESS_LIMITATION.md
docs/PIPELINE_RUN_REPORT.md
```

## 9. Troubleshoot Failures

If something fails:

1. Stop the pipeline.
2. Read the terminal error.
3. Run the failed stage verifier directly.
4. Inspect that stage's manifest and data-quality report under `data/`.
5. Do not continue downstream until the failed stage passes.

Useful commands:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
python3 scripts/verify_market_run.py --config configs/run_config.yaml
python3 scripts/verify_label_run.py --config configs/run_config.yaml
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml
```

Research rule: no verifier pass, no result claim. If BacktestAgent does not set `alpha_verified=true`, the honest conclusion remains no verified alpha.
