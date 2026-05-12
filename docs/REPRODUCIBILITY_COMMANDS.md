# Reproducibility Commands

## Secret Handling

Do not paste API keys into code, docs, terminal logs, or chat logs.

API keys should be loaded only through environment variables or a local `.env` file excluded from git.

Required key names, when used:

- `CMC_API_KEY`
- `ETHERSCAN_API_KEY`

Set these only in your local shell environment or local `.env` file. Do not write real keys into repository files, documentation, notebooks, screenshots, shell history exports, or chat logs.

Do not commit `.env`.

## Phase 0 Readiness

```bash
python3 -m py_compile scripts/probe_api_readiness.py scripts/audit_pipeline_inputs.py
python3 scripts/probe_api_readiness.py --config configs/run_config.yaml
python3 scripts/audit_pipeline_inputs.py --config configs/run_config.yaml
```

Outputs:

- `docs/API_DATA_READINESS_AUDIT.md`
- `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`
- `data/readiness/api_probe_results.json`
- `data/readiness/pipeline_input_audit.json`

## Phase 1 Integrity Tests

Representative targeted checks:

```bash
python3 -m py_compile agents/universe_agent.py agents/market_data_agent.py agents/onchain_agent.py agents/feature_agent.py agents/label_agent.py agents/model_agent.py agents/portfolio_agent.py agents/backtest_agent.py agents/alpha_research_agent.py

python3 -m pytest tests/test_universe_agent_research_mode.py -q
python3 -m pytest tests/test_market_data_agent_research_mode.py -q
python3 -m pytest tests/test_onchain_agent_research_mode.py -q
python3 -m pytest tests/test_feature_agent_research_mode.py -q
python3 -m pytest tests/test_label_agent_research_mode.py -q
python3 -m pytest tests/test_model_agent_research_mode.py -q
python3 -m pytest tests/test_portfolio_agent_research_mode.py -q
python3 -m pytest tests/test_backtest_agent_research_mode.py -q
python3 -m pytest tests/test_alpha_research_agent.py -q
```

## Phase 2-Fallback Baseline Pipeline

Config sections used:

- Universe: `universe`
- Market: `market_data`
- On-chain: `onchain`
- Features: `features`
- Labels: `labels`
- Modeling: `modeling`

Commands:

```bash
python3 main.py universe --config configs/run_config.yaml
python3 scripts/verify_universe_run.py --config configs/run_config.yaml

python3 main.py market --config configs/run_config.yaml
python3 scripts/verify_market_run.py --config configs/run_config.yaml

python3 main.py onchain --config configs/run_config.yaml
python3 scripts/verify_onchain_run.py --config configs/run_config.yaml

python3 main.py features --config configs/run_config.yaml
python3 scripts/verify_feature_run.py --config configs/run_config.yaml

python3 main.py labels --config configs/run_config.yaml
python3 scripts/verify_label_run.py --config configs/run_config.yaml

python3 main.py model --config configs/run_config.yaml
python3 scripts/verify_model_run.py --config configs/run_config.yaml
```

Model verifier repair result:

```text
Model validation: PASS
```

The baseline ModelAgent run produced no candidate signals and did not proceed to PortfolioAgent or BacktestAgent.

## Phase 3 AlphaResearchAgent Signal Search

Config section:

- `alpha_research`

Commands:

```bash
python3 -m py_compile agents/alpha_research_agent.py scripts/verify_alpha_research_run.py
python3 -m pytest tests/test_alpha_research_agent.py -q
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
python3 scripts/verify_alpha_research_run.py --config configs/run_config.yaml --section alpha_research
```

Outputs:

- `data/research/research_leaderboard.parquet`
- `data/research/best_experiments.parquet`
- `data/research/research_manifest.json`
- `data/research/alpha_research_report.md`
- `data/predictions/alpha_research_predictions.parquet`
- `data/predictions/alpha_model_leaderboard.parquet`
- `data/predictions/alpha_fold_metrics.parquet`

Verifier result:

```text
Alpha research validation: PASS
```

## Phase 4A Combined Candidate Export And Backtest

Config sections:

- Portfolio: `portfolio_alpha_candidates`
- Backtest: `backtesting_alpha_candidates`

Commands:

```bash
python3 -m py_compile scripts/export_alpha_candidates_for_backtest.py
python3 scripts/export_alpha_candidates_for_backtest.py --config configs/run_config.yaml

python3 main.py portfolio --config configs/run_config.yaml --section portfolio_alpha_candidates
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_alpha_candidates

python3 main.py backtest --config configs/run_config.yaml --section backtesting_alpha_candidates
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_alpha_candidates
```

Outputs:

- `data/predictions/candidate_model_predictions.parquet`
- `data/predictions/candidate_model_leaderboard.parquet`
- `data/predictions/candidate_model_manifest.json`
- `data/allocations_alpha_candidates/`
- `data/backtests_alpha_candidates/`

Verifier results:

```text
Portfolio validation: PASS
Backtest validation: PASS
```

## Phase 4B Individual Candidate Backtests

Candidate-specific sections:

- `portfolio_candidate_lightgbm_14d`
- `backtesting_candidate_lightgbm_14d`
- `portfolio_candidate_linear_ridge_30d`
- `backtesting_candidate_linear_ridge_30d`
- `portfolio_candidate_random_forest_14d`
- `backtesting_candidate_random_forest_14d`

Commands:

```bash
python3 main.py portfolio --config configs/run_config.yaml --section portfolio_candidate_lightgbm_14d
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_candidate_lightgbm_14d
python3 main.py backtest --config configs/run_config.yaml --section backtesting_candidate_lightgbm_14d
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_candidate_lightgbm_14d

python3 main.py portfolio --config configs/run_config.yaml --section portfolio_candidate_linear_ridge_30d
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_candidate_linear_ridge_30d
python3 main.py backtest --config configs/run_config.yaml --section backtesting_candidate_linear_ridge_30d
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_candidate_linear_ridge_30d

python3 main.py portfolio --config configs/run_config.yaml --section portfolio_candidate_random_forest_14d
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_candidate_random_forest_14d
python3 main.py backtest --config configs/run_config.yaml --section backtesting_candidate_random_forest_14d
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_candidate_random_forest_14d
```

Outputs:

- `data/predictions/candidates_by_signal/`
- `data/allocations_candidate_lightgbm_14d/`
- `data/allocations_candidate_linear_ridge_30d/`
- `data/allocations_candidate_random_forest_14d/`
- `data/backtests_candidate_lightgbm_14d/`
- `data/backtests_candidate_linear_ridge_30d/`
- `data/backtests_candidate_random_forest_14d/`

Verifier result for each candidate:

```text
Portfolio validation: PASS
Backtest validation: PASS
```

## Final Package Documents

- `docs/RESEARCH_RESULTS_SUMMARY.md`
- `docs/ALPHA_FINDINGS_REPORT.md`
- `docs/LIMITATIONS_AND_NEXT_STEPS.md`
- `docs/REPRODUCIBILITY_COMMANDS.md`
- `docs/PIPELINE_RUN_REPORT.md`
- `docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md`
- `docs/API_DATA_READINESS_AUDIT.md`
- `docs/knowledge_base.md`

## Final Result

No verified alpha found under tested configurations.
