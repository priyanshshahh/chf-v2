# Dashboard Demo Guide

The Phase 12 dashboard is a production-style local control center for Project CHF. It is intended for reviewer, professor, or demo walkthroughs of the frozen research release.

The dashboard is still a research/demo layer. It is not a trading product and it does not provide financial advice.

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

## How To Run Locally

Install dependencies first:

```bash
python3 -m pip install -r requirements.txt
```

Launch with:

```bash
./run_dashboard.sh
```

or:

```bash
streamlit run app/dashboard.py
```

Then open:

```text
http://localhost:8501
```

## What The Dashboard Shows

### Home / Executive Summary

Shows the final no-verified-alpha result, candidate signal table, release tag, current branch, and research-integrity warning.

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

Start locally:

```bash
./run_scheduler.sh
```

The scheduler stops when the terminal/server stops unless deployed separately as a service.

### Results Explorer

Shows candidate results, benchmark results, report content, and real equity curves if local `data/` artifacts are present. Missing files produce warnings instead of fake charts.

### Project Explorer

Provides a safe file browser limited to whitelisted directories and safe extensions. It blocks hidden files, `.env`, `.git`, `.venv`, caches, and unrestricted paths.

### Architecture / Methodology

Explains walk-forward validation, benchmark comparison, transaction-cost convention, and why no verified alpha is still a valid research result.

### Reproducibility

Shows validation commands, reviewer packet links, artifact manifest, release audit, and reproducibility checklist.

### Logs / Run History

Shows logs from dashboard-triggered commands under:

```text
logs/dashboard_runs/
```

## How To Present It

Recommended demo flow:

1. Open Home / Executive Summary.
2. Show Pipeline Control Center and explain stage status.
3. Open Results Explorer and explain the candidate results.
4. Open Benchmark Verification to explain BTC `305.50%`.
5. Open Reproducibility Package or Project Explorer to show reviewer documents.
6. Show Scheduler / Automation as a local automation option.
7. End with Limitations.

## How To Demo Safe Command Execution

Use a verifier button first, not a full pipeline button.

1. Open Pipeline Control Center.
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
- historical CMC point-in-time universe was completed,
- dashboard buttons are safe for public unauthenticated deployment.

Correct claim:

```text
CHF found statistically promising candidate signals, but no strategy achieved verified alpha under the tested configurations.
```

## Public Deployment Warning

If deploying the dashboard publicly, disable pipeline command buttons or protect them behind authentication. The current dashboard is designed for local use by the repository owner or reviewer.
