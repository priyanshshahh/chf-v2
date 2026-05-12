# Repository Cleanup And Submission Plan

## Purpose

This plan separates commit-worthy Project CHF work from generated artifacts, caches, local environment files, and data outputs. It is documentation only; no files were removed, staged, committed, reset, or cleaned.

## Current Repository Shape

`git status --short` shows several categories mixed together:

- Research source code changes.
- New and updated verification scripts.
- New and updated tests.
- Final research documentation.
- Generated canonical data and research outputs.
- Local-only caches, bytecode, logs, virtual environment files, metadata databases, and `.DS_Store`.
- Tracked legacy/demo data deletions and generated data modifications that need manual review before any commit.

Do not run `git add .`.

## Commit-Worthy Source Code

These source files are generally commit-worthy after manual diff review because they implement the research pipeline and verification logic:

- `agents/*.py`
- `features/feature_engineering.py`
- `models/walk_forward.py`
- `providers/*.py`
- `configs/run_config.yaml`
- `main.py`
- `run_all.sh`
- `pipelines/pipeline_runner.py`
- `schemas/schemas.py`
- `scripts/*.py`, especially:
  - `scripts/probe_api_readiness.py`
  - `scripts/audit_pipeline_inputs.py`
  - `scripts/verify_universe_run.py`
  - `scripts/verify_market_run.py`
  - `scripts/verify_onchain_run.py`
  - `scripts/verify_feature_run.py`
  - `scripts/verify_label_run.py`
  - `scripts/verify_model_run.py`
  - `scripts/verify_portfolio_run.py`
  - `scripts/verify_backtest_run.py`
  - `scripts/verify_alpha_research_run.py`
  - `scripts/export_alpha_candidates_for_backtest.py`

Recommended caution:

- Review `configs/run_config.yaml` carefully because it contains many section additions and should not include secrets.
- Review provider files to ensure no API keys or local-only paths appear.
- Review `main.py`, `run_all.sh`, and `pipelines/pipeline_runner.py` for command compatibility before committing.

## Commit-Worthy Tests

These tests are generally commit-worthy after confirming they use fixtures only and do not require live API keys:

- `tests/test_cmc_provider_research_mode.py`
- `tests/test_universe_agent_research_mode.py`
- `tests/test_market_data_agent_research_mode.py`
- `tests/test_onchain_agent_research_mode.py`
- `tests/test_feature_agent_research_mode.py`
- `tests/test_label_agent_research_mode.py`
- `tests/test_model_agent_research_mode.py`
- `tests/test_portfolio_agent_research_mode.py`
- `tests/test_backtest_agent_research_mode.py`
- `tests/test_alpha_research_agent.py`
- `tests/fixtures/`

Before committing tests:

- Confirm fixture files are small enough for git.
- Confirm no fixture contains secrets or real private API responses that should be kept local.

## Commit-Worthy Docs

These documentation files are commit-worthy because they explain the research result, limitations, and reproducibility:

- `docs/RESEARCH_RESULTS_SUMMARY.md`
- `docs/ALPHA_FINDINGS_REPORT.md`
- `docs/LIMITATIONS_AND_NEXT_STEPS.md`
- `docs/REPRODUCIBILITY_COMMANDS.md`
- `docs/PIPELINE_RUN_REPORT.md`
- `docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md`
- `docs/ALPHA_SIGNAL_SEARCH_REPORT.md`
- `docs/API_DATA_READINESS_AUDIT.md`
- `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`
- `docs/knowledge_base.md`
- `docs/REPO_CLEANUP_AND_SUBMISSION_PLAN.md`

Optional docs to review before committing:

- `README.md`
- `LOCAL_HANDOFF.md`
- `docs/data_dictionary.md`
- `docs/architecture.mmd`
- `docs/architecture.png`

## Generated Research Outputs To Archive Separately

These outputs are valuable as research evidence but should usually be archived outside git or stored with a data/artifact system:

- `data/readiness/`
- `data/research/`
- `data/predictions/alpha_*`
- `data/predictions/candidate_*`
- `data/predictions/candidates_by_signal/`
- `data/allocations_alpha_candidates/`
- `data/allocations_candidate_*`
- `data/backtests_alpha_candidates/`
- `data/backtests_candidate_*`
- `data/raw/`
- `data/features/`
- `data/labels/`
- `data/predictions/`
- `data/allocations/`
- `data/backtests/`

Recommended archive bundle:

- `docs/RESEARCH_RESULTS_SUMMARY.md`
- `docs/ALPHA_FINDINGS_REPORT.md`
- `docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md`
- `docs/API_DATA_READINESS_AUDIT.md`
- `docs/LIMITATIONS_AND_NEXT_STEPS.md`
- `data/research/`
- `data/predictions/candidate_model_manifest.json`
- `data/predictions/candidate_model_leaderboard.parquet`
- `data/predictions/candidates_by_signal/`
- `data/backtests_candidate_lightgbm_14d/`
- `data/backtests_candidate_linear_ridge_30d/`
- `data/backtests_candidate_random_forest_14d/`

Do not archive secrets or `.env`.

## Local-Only Files That Should Be Gitignored

These should not be committed:

- `.env`
- `.env.*`
- `.venv/`
- `__pycache__/`
- `*.pyc`
- `.DS_Store`
- `logs/`
- `data/cache/`
- `data/*_smoke/`
- `data/backups/`
- `metadata/*.db`

The `.gitignore` was updated to include the missing local-only patterns requested in Phase 6.

## Files To Avoid Committing

Avoid committing these unless the repository intentionally tracks data artifacts:

- `data/raw/**`
- `data/features/**`
- `data/labels/**`
- `data/predictions/**`
- `data/allocations/**`
- `data/backtests/**`
- `data/research/**`
- `data/readiness/**`
- `metadata/agent_registry.db`
- `*.pyc`
- `__pycache__/**`
- `.DS_Store`
- `.venv/**`
- `logs/**`

## Files That Should Be Restored Or Reviewed Manually

`git status --short` shows many deleted tracked data/demo files under:

- `data/allocations/`
- `data/backtests/`
- `data/features/`
- `data/labels/`
- `data/predictions/`
- `data/raw/market/`
- `data/raw/onchain/`
- `data/raw/universe/`

These deletions should be reviewed manually before any commit. If they are legacy demo artifacts that should be removed from version control, remove them in a dedicated cleanup commit. If they were accidentally removed by pipeline regeneration, restore them before committing source/docs.

Also manually review:

- `.DS_Store`
- `metadata/agent_registry.db`
- bytecode files under `__pycache__/`
- generated smoke directories under `data/*_smoke/`
- `.venv/`

## Recommended Commit Set

When ready to prepare a clean commit later, use an explicit path list rather than `git add .`.

Recommended source/docs/tests commit set:

- `.gitignore`
- `agents/*.py`
- `features/feature_engineering.py`
- `models/walk_forward.py`
- `providers/*.py`
- `configs/run_config.yaml`
- `main.py`
- `run_all.sh`
- `pipelines/pipeline_runner.py`
- `schemas/schemas.py`
- `scripts/*.py`
- `tests/*.py`
- `tests/fixtures/`, after fixture review
- selected docs listed in the commit-worthy docs section

Recommended separate archive set:

- Final reports in `docs/`
- Candidate manifests and leaderboard files
- Candidate backtest directories
- API/data readiness JSON files

## Safe Submission Summary

The research package is safe to share as an honest no-verified-alpha result if the submission includes:

- final docs,
- verifier results,
- the latest-survivor limitation,
- the CMC historical listings access limitation,
- no API keys,
- no generated caches or virtual environments,
- no statement that CHF outperformed BTC as an overall result.

Final research conclusion remains:

CHF found statistically promising candidate signals, but after deterministic portfolio construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting, no strategy achieved verified alpha against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks under the tested configurations.

## Phase 7 Commit-Readiness Update

Phase 7 confirmed that generated `data/` artifacts are not intended for source-control commits. Running `git rm --cached -r data` returned `fatal: pathspec 'data' did not match any files`, which indicates there was no remaining tracked `data` pathspec for Git to remove at that point. Local data files were verified to still exist on disk.

Additional ignore patterns were added:

- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `.ipynb_checkpoints/`
- `data/`

Validation passed:

- `python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py`
- `python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_portfolio_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q`

Targeted test result:

- `74 passed`

See [repository commit readiness report](./REPOSITORY_COMMIT_READINESS_REPORT.md) before preparing any explicit-path commit.

## Phase 8 Final Source-Only Audit Update

Phase 8 completed the final repository audit for source-only commit preparation. No code, research outputs, generated backtest files, `.env` values, local data, commits, pushes, resets, cleans, or broad staging operations were performed.

Final checks confirmed:

- `.gitignore` contains the required local/generated patterns, including `data/`, `.env`, `.venv/`, bytecode/cache patterns, `logs/`, and `metadata/*.db`.
- `git status --short data | head -100` shows tracked deletions only, with no untracked generated `data/` files appearing.
- Junk/cache/local metadata patterns show expected `D` entries only, not modified tracked junk.
- The secret/overclaim grep found no real keys and no improper claim that CHF found verified alpha.
- Syntax validation passed:
  - `python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py`
- Expanded targeted tests passed:
  - `230 passed`

Nothing was staged automatically. The next commit should be prepared with explicit paths only after manual diff review. Do not use `git add .`.
