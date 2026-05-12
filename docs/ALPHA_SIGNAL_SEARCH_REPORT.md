# Alpha Signal Search Report

## Scope

Phase 3 Alpha-Search Expansion ran `AlphaResearchAgent` in signal-only mode. PortfolioAgent and BacktestAgent were not run in this phase, and no alpha is verified by this report.

Required limitation:

> Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.

## Commands Run

```bash
python3 -m py_compile agents/alpha_research_agent.py scripts/verify_alpha_research_run.py
python3 -m pytest tests/test_alpha_research_agent.py -q
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
python3 scripts/verify_alpha_research_run.py --config configs/run_config.yaml --section alpha_research
```

## Verification Result

```text
Alpha research validation: PASS
```

## Run Summary

- Experiments run: `80`
- Experiments skipped by configured budget: `1,090`
- Prediction rows: `16,491,600`
- Fold metric rows: `3,040`
- Final alpha passed: `0`
- Signal candidates found: `3`
- `export_candidate_to_predictions`: `false`
- `canonical_outputs_mutated`: `false`
- `signal_only`: `true`

Canonical `data/predictions/model_predictions.parquet` was not overwritten by AlphaResearchAgent in this phase.

## Search Space Sampled

The bounded `max_experiments=80` run sampled across these observed dimensions:

- Horizons: `7`, `14`, `30`
- Label targets observed: `raw_forward_return`, `excess_vs_equal_weight`, `excess_vs_btc`
- Feature sets observed: `market_only`, `market_plus_onchain`
- Models/signals observed: `baseline_cross_sectional_mean`, `linear_ridge`, `elastic_net`, `random_forest`, `lightgbm`, `rule_momentum_14d`, `rule_momentum_30d`, `rule_vol_adjusted_momentum`, `rule_reversal_3d`, `rule_liquidity_momentum`, `rule_onchain_growth`, `rule_valuation_onchain`, `rule_composite_market_onchain`

Because the configured grid is larger than the budget, not every requested feature-set and label-target combination was run. Skipped experiments are explicitly recorded in `data/research/research_manifest.json`.

## Candidate Signals

These passed the signal screen only. They are not verified alpha until exported safely, allocated by PortfolioAgent, and backtested by BacktestAgent.

| model_name | feature_set | label_target | horizon_days | mean_rank_ic | rank_ic_tstat | top_bottom_spread | percent_positive_ic_folds | n_folds | n_predictions |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lightgbm | market_only | raw_forward_return | 14 | 0.0275 | 7.1034 | 0.0034 | 0.5892 | 38 | 206145 |
| linear_ridge | market_only | raw_forward_return | 30 | 0.0142 | 6.3192 | 0.0041 | 0.5958 | 38 | 206145 |
| random_forest | market_only | raw_forward_return | 14 | 0.0170 | 4.3800 | 0.0029 | 0.5617 | 38 | 206145 |

## Top 20 Signal-Only Experiments

| model_name | feature_set | label_target | horizon_days | mean_rank_ic | rank_ic_tstat | top_bottom_spread | percent_positive_ic_folds | n_folds | candidate_for_backtest |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| lightgbm | market_only | raw_forward_return | 14 | 0.0275 | 7.1034 | 0.0034 | 0.5892 | 38 | true |
| linear_ridge | market_only | raw_forward_return | 30 | 0.0142 | 6.3192 | 0.0041 | 0.5958 | 38 | true |
| lightgbm | market_only | raw_forward_return | 30 | 0.0224 | 5.5881 | 0.0077 | 0.5250 | 38 | false |
| linear_ridge | market_only | excess_vs_equal_weight | 14 | 0.0135 | 5.7529 | -0.0050 | 0.6083 | 38 | false |
| linear_ridge | market_only | excess_vs_equal_weight | 7 | 0.0125 | 5.6468 | -0.0023 | 0.6050 | 38 | false |
| random_forest | market_only | raw_forward_return | 30 | 0.0240 | 5.3293 | -0.0001 | 0.5383 | 38 | false |
| random_forest | market_plus_onchain | raw_forward_return | 7 | 0.0186 | 4.9588 | 0.0026 | 0.5458 | 38 | false |
| random_forest | market_only | raw_forward_return | 14 | 0.0170 | 4.3800 | 0.0029 | 0.5617 | 38 | true |
| linear_ridge | market_only | raw_forward_return | 14 | 0.0074 | 3.3462 | -0.0003 | 0.5750 | 38 | false |
| random_forest | market_only | raw_forward_return | 7 | 0.0105 | 2.7795 | 0.0039 | 0.5275 | 38 | false |
| random_forest | market_only | excess_vs_equal_weight | 14 | 0.0157 | 2.6625 | -0.0016 | 0.5250 | 38 | false |
| linear_ridge | market_only | raw_forward_return | 7 | 0.0054 | 2.6117 | -0.0003 | 0.5650 | 38 | false |
| rule_reversal_3d | market_only | raw_forward_return | 7 | 0.0158 | 2.4509 | -0.0028 | 0.5308 | 38 | false |
| rule_reversal_3d | market_only | excess_vs_equal_weight | 7 | 0.0158 | 2.4509 | -0.0028 | 0.5308 | 38 | false |
| rule_reversal_3d | market_plus_onchain | raw_forward_return | 7 | 0.0158 | 2.4509 | -0.0028 | 0.5308 | 38 | false |
| lightgbm | market_plus_onchain | raw_forward_return | 7 | 0.0075 | 2.1145 | 0.0009 | 0.5383 | 38 | false |
| rule_onchain_growth | market_only | raw_forward_return | 30 | 0.0073 | 1.5505 | 0.0134 | 0.5200 | 38 | false |
| rule_valuation_onchain | market_only | raw_forward_return | 30 | 0.0047 | 1.2992 | 0.0205 | 0.5050 | 38 | false |
| lightgbm | market_only | excess_vs_equal_weight | 14 | 0.0086 | 1.4740 | -0.0043 | 0.5233 | 38 | false |
| rule_valuation_onchain | market_only | raw_forward_return | 7 | 0.0044 | 1.3065 | 0.0043 | 0.5117 | 38 | false |

## Interpretation

Candidate signals found, but alpha is not verified until PortfolioAgent and BacktestAgent evaluate them.

The strongest signal-only candidate was `lightgbm / market_only / raw_forward_return / 14d`, with mean Rank IC `0.0275`, Rank IC t-stat `7.1034`, and top-bottom spread `0.0034`.

The current run did not prove tradable alpha. It only found signal-screen candidates worth a later, explicit candidate export and portfolio/backtest verification phase.

## Known Warnings And Limitations

- Current production universe is the latest-survivor baseline, not a three-year point-in-time CMC universe.
- CMC three-year historical listings remain blocked by the current API plan.
- `max_experiments=80` means many configured experiments were skipped by budget.
- Some configured feature sets and label targets were not sampled in this bounded run.
- On-chain coverage remains sparse relative to market coverage.
- AlphaResearchAgent is signal-only and does not produce verified Sharpe, CAGR, total return, max drawdown, or final alpha status.
- A local pyarrow/dask warning was emitted about pyarrow `11.0.0`; this is an environment security/dependency warning, not alpha evidence.

## Next Step

If continuing, export only the candidate signals in a portfolio-safe prediction file, then run PortfolioAgent and BacktestAgent. BacktestAgent remains the only stage allowed to set `alpha_verified` to true.
