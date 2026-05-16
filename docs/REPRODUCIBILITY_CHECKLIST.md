# Reproducibility Checklist

Use this checklist before sharing or reviewing CHF results. It is designed to prevent accidental regeneration of research artifacts and to make clear which commands validate the repository without changing results.

## 1. Confirm Repository State

```bash
git status -sb
git log --oneline -3
```

Expected release-prep state before committing these docs:

- branch: `main`
- latest baseline commit before Phase 11: `9894d7b6 Add benchmark verification and final reviewer packet`
- local branch synced with `origin/main`

## 2. Python Compile Check

Run:

```bash
python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py
```

This checks syntax only. It does not rerun the research pipeline or overwrite artifacts.

## 3. Targeted Tests

Run the targeted research-integrity subset:

```bash
python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q
```

These tests validate critical signal and backtest behavior without rerunning the full pipeline.

## 4. Markdown And Link Sanity

Use a local Markdown sanity check that:

- verifies newly added Markdown files exist,
- verifies README links point to existing local files,
- verifies local `docs/*.md` links point to existing files where practical,
- checks that no new placeholder-marker text was introduced.

The Phase 11 audit used a local Python script for this check rather than a pipeline command.

## 5. Benchmark Verification Review

Read:

```text
docs/BENCHMARK_VERIFICATION.md
```

Key benchmark facts:

- Candidate benchmark window: `2022-12-15` through `2026-03-24`.
- BTC benchmark return: `305.50%`.
- ETH benchmark return: `69.85%`.
- BTC/ETH 50-50 benchmark return: `178.04%`.
- Equal-weight universe benchmark return: `30.39%`.

The BTC value is tied to CHF's exact candidate backtest window and BacktestAgent benchmark cost convention. It is not intended to match arbitrary public trailing "last 5 years" charts.

## 6. Locate Stored Backtest Outputs

If generated local artifacts are present, candidate backtest outputs are expected under:

```text
data/backtests_candidate_lightgbm_14d/
data/backtests_candidate_linear_ridge_30d/
data/backtests_candidate_random_forest_14d/
data/backtests_alpha_candidates/
```

Key files inside candidate folders:

```text
benchmark_summary.parquet
backtest_summary.parquet
strategy_comparison.parquet
alpha_report.json
alpha_report.md
benchmark_sanity_report.parquet
```

Generated `data/` artifacts are ignored by Git and may not be present in a fresh clone.

## 7. Do Not Regenerate Accidentally

Do not run these unless intentionally rebuilding the research outputs:

```bash
./run_all.sh
python3 main.py universe --config configs/run_config.yaml
python3 main.py market --config configs/run_config.yaml
python3 main.py onchain --config configs/run_config.yaml
python3 main.py features --config configs/run_config.yaml
python3 main.py labels --config configs/run_config.yaml
python3 main.py model --config configs/run_config.yaml
python3 main.py portfolio --config configs/run_config.yaml
python3 main.py backtest --config configs/run_config.yaml
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
```

These commands can create or overwrite generated artifacts under `data/`.

## 8. Research Result Freeze

Final result:

```text
No verified alpha found under tested configurations.
```

Do not change benchmark returns, strategy returns, candidate metrics, dates, or final research claims unless a verified arithmetic or data error is found and documented separately.
