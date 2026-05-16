# Artifact Manifest

This manifest lists reviewer-facing files and important local generated artifact directories for the final CHF research package.

| Item | Purpose | Category | Manual Editing | Contains Research Numbers |
|---|---|---|---|---|
| `README.md` | GitHub landing page and quick orientation. | documentation | Yes, but only for wording/links. | Yes |
| `docs/FINAL_REVIEWER_PACKET.md` | Professor/reviewer-facing summary and reading order. | documentation | Yes, for clarification only. | Yes |
| `docs/BENCHMARK_VERIFICATION.md` | Explains benchmark windows and verifies BTC return math. | documentation | Yes, for clarification only. | Yes |
| `docs/RESEARCH_RESULTS_SUMMARY.md` | Final research conclusion and candidate summary. | documentation | Yes, for clarification only. | Yes |
| `docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md` | Candidate-by-candidate BacktestAgent verification report. | documentation | Yes, for clarification only. | Yes |
| `docs/knowledge_base.md` | Project status log and operating notes. | documentation | Yes, append-only preferred. | Yes |
| `docs/REPRODUCIBILITY_CHECKLIST.md` | Final validation and no-regeneration checklist. | documentation | Yes, for process clarification. | Yes |
| `docs/ARTIFACT_MANIFEST.md` | Inventory of reviewer-facing files and artifacts. | documentation | Yes, for inventory updates. | Yes |
| `docs/FINAL_RELEASE_AUDIT.md` | Phase 11 release audit results. | documentation | Yes, after re-running audit commands. | Yes |
| `docs/USER_GUIDE.md` | Beginner setup and runbook. | documentation | Yes. | No |
| `docs/API_KEYS_AND_DATA_SOURCES.md` | API key and provider explanation. | documentation | Yes. | No |
| `docs/DASHBOARD_GUIDE.md` | Dashboard setup and troubleshooting. | documentation | Yes. | No |
| `docs/REPRODUCIBILITY_COMMANDS.md` | Detailed commands used by phase. | documentation | Yes, with care. | Yes |
| `docs/API_DATA_READINESS_AUDIT.md` | API/data readiness summary. | documentation | Yes, when readiness is re-probed. | Yes |
| `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md` | CMC historical-universe access limitation. | documentation | Yes, if access changes. | Yes |
| `tests/test_alpha_research_agent.py` | AlphaResearchAgent signal-only integrity tests. | test | Only for test maintenance. | No |
| `tests/test_model_agent_research_mode.py` | ModelAgent signal-gate and verifier tests. | test | Only for test maintenance. | No |
| `tests/test_backtest_agent_research_mode.py` | BacktestAgent benchmark/alpha verification tests. | test | Only for test maintenance. | No |
| `configs/run_config.yaml` | Pipeline configuration. | config | Yes, but changes can affect outputs. | No |
| `data/backtests_candidate_lightgbm_14d/` | Stored local backtest outputs for the LightGBM candidate, if present. | generated artifact | No; regenerate intentionally only. | Yes |
| `data/backtests_candidate_linear_ridge_30d/` | Stored local backtest outputs for the strongest linear ridge candidate, if present. | generated artifact | No; regenerate intentionally only. | Yes |
| `data/backtests_candidate_random_forest_14d/` | Stored local backtest outputs for the random forest candidate, if present. | generated artifact | No; regenerate intentionally only. | Yes |
| `data/backtests_alpha_candidates/` | Combined candidate backtest outputs, if present. | generated artifact | No; regenerate intentionally only. | Yes |
| `data/research/` | AlphaResearchAgent signal-search outputs, if present. | generated artifact | No; regenerate intentionally only. | Yes |
| `data/predictions/` | Model and candidate prediction outputs, if present. | generated artifact | No; regenerate intentionally only. | Yes |

## Notes

- `data/` is ignored by Git and is not expected in a fresh clone.
- Generated artifacts should be archived separately if exact local outputs are needed for review.
- Source logic and generated outputs should not be edited during documentation-only release audits.
