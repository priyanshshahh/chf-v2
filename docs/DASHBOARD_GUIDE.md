# Dashboard Guide

CHF includes a Streamlit dashboard for inspecting locally generated research artifacts. It functions as a production-style local control center for the frozen research release and is intended for reviewer, professor, or demo walkthroughs. The dashboard is a viewer for local outputs; it does not create verified alpha and it is not an execution system. It remains a research/demo layer: it is not a trading product and it does not provide financial advice.

## Purpose

The dashboard helps a reviewer understand:

- what Project CHF is,
- how the pipeline is structured,
- which local artifacts are available or missing,
- what the final research result was,
- how benchmarks were verified,
- how to inspect final reports,
- how safe local pipeline commands are triggered,
- how local scheduling works,
- what limitations remain.

## Before Running The Dashboard

Run at least the relevant pipeline stages first so local `data/` artifacts exist.

For a full local run:

```bash
./run_all.sh
```

For a quick API/data status check:

```bash
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

The dashboard expects generated local files under paths such as:

```text
data/raw/
data/features/
data/labels/
data/predictions/
data/allocations/
data/backtests/
data/research/
```

`data/` is ignored by Git, so a fresh clone will not include these outputs.

## Run The Dashboard

Use Streamlit directly:

```bash
streamlit run app/dashboard.py
```

or use the helper script:

```bash
./run_dashboard.sh
```

Then open:

```text
http://localhost:8501
```

## What You Should See

Depending on which artifacts exist locally, the dashboard may show:

- universe and market coverage,
- feature and label summaries,
- model signal diagnostics,
- portfolio allocation outputs,
- backtest equity curves and summaries,
- final research reports.

If a panel is empty, first check whether the corresponding pipeline stage has been run and verified.

## Dashboard Sections

The dashboard is organized into the following sections.

### Home / Executive Summary

Shows the final no-verified-alpha result, the candidate signal table, the release tag, the current branch, and the research-integrity warning.

### Pipeline Control Center

Shows the full pipeline:

```text
UniverseAgent → MarketDataAgent → OnChainAgent → FeatureAgent → LabelAgent → ModelAgent → PortfolioAgent → BacktestAgent
```

For each stage it shows purpose, expected inputs/outputs, output availability, and last modified time where available.

It also includes guarded local command buttons. A user must check the confirmation box before any command can run. Buttons call existing CLI commands only.

### Scheduler / Automation

Shows the local schedule plan:

- UniverseAgent: monthly
- MarketDataAgent: daily
- OnChainAgent: daily
- FeatureAgent: daily after data
- LabelAgent: daily after features
- ModelAgent: weekly or manual
- PortfolioAgent: weekly
- BacktestAgent: manual/research validation only by default

Start the scheduler locally:

```bash
./run_scheduler.sh
```

The scheduler stops when the terminal/server stops unless deployed separately as a service.

### Results Explorer

Shows candidate results, benchmark results, report content, and real equity curves if local `data/` artifacts are present. Missing files produce warnings instead of fake charts.

### Project Explorer

Provides a safe file browser limited to whitelisted directories and safe extensions. It blocks hidden files, `.env`, `.git`, `.venv`, caches, and unrestricted paths.

### Architecture / Methodology

Explains walk-forward validation, benchmark comparison, the transaction-cost convention, and why a no-verified-alpha outcome is still a valid research result.

### Reproducibility

Shows validation commands, reviewer packet links, the artifact manifest, the release audit, and the reproducibility checklist.

### Logs / Run History

Shows logs from dashboard-triggered commands under:

```text
logs/dashboard_runs/
```

## Troubleshooting

If the dashboard does not start:

```bash
python3 -m pip install -r requirements.txt
python3 -m py_compile app/dashboard.py
```

If the dashboard starts but data is missing:

```bash
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

If a pipeline stage failed, run its verifier directly and fix that stage before relying on dashboard views.

Important research rule: dashboard displays are not alpha verification. BacktestAgent is the only stage allowed to verify alpha.

## Demo Walkthrough

Recommended demo flow:

1. Open Home / Executive Summary.
2. Show the Pipeline Control Center and explain stage status.
3. Open the Results Explorer and explain the candidate results.
4. Open Benchmark Verification to explain BTC `305.50%`.
5. Open the Reproducibility Package or Project Explorer to show reviewer documents.
6. Show Scheduler / Automation as a local automation option.
7. End with Limitations.

## Demonstrating Safe Command Execution

Use a verifier button first, not a full pipeline button.

1. Open the Pipeline Control Center.
2. Read the warning.
3. Check the confirmation checkbox.
4. Click a verifier button.
5. Show stdout/stderr and the generated dashboard log.

Avoid running full pipeline commands during a short demo unless the reviewer explicitly wants to see local output regeneration.

## What Not To Claim

Do not claim:

- verified alpha was found,
- CHF beat BTC overall,
- the system is a trading product,
- results are live or executable,
- the historical CMC point-in-time universe was completed,
- dashboard buttons are safe for public unauthenticated deployment.

Correct claim:

```text
CHF found statistically promising candidate signals, but no strategy achieved verified alpha under the tested configurations.
```

## Public Deployment Warning

If deploying the dashboard publicly, disable pipeline command buttons or protect them behind authentication. The current dashboard is designed for local use by the repository owner or reviewer.
