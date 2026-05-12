# Pipeline Run Report

## Phase

Phase 2-Fallback: corrected latest-survivor/free-provider baseline pipeline.

## Decision Context

CoinMarketCap three-year point-in-time historical universe construction remains blocked by the current API plan. This run used the latest-survivor/free-provider baseline path and must be interpreted with survivorship-bias disclosure.

Required limitation:

> Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.

## Config Sections Used

- Universe: `universe`
- Market data: `market_data`
- On-chain: `onchain`
- Features: `features`
- Labels: `labels`
- Modeling: `modeling`

Portfolio and backtest were not run because model verification failed.

## Commands Run

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

## Stage Results

| Stage | Agent Result | Verifier Result | Notes |
|---|---:|---:|---|
| UniverseAgent | PASS | PASS | Latest snapshot only; no fake historical snapshots. |
| MarketDataAgent | PASS | PASS | 90 persisted/full-OHLCV assets from 100 requested. |
| OnChainAgent | PASS | PASS | 43 assets with any on-chain observations. |
| FeatureAgent | PASS | PASS | 100,293 full feature rows; 57 kept features after pruning. |
| LabelAgent | PASS | PASS | 97,593 all-horizon label/modeling rows. |
| ModelAgent | PASS | PASS | No candidate signal passed; explicit no-candidate manifest state accepted. |
| PortfolioAgent | NOT RUN | NOT RUN | Not run in this repair phase. |
| BacktestAgent | NOT RUN | NOT RUN | Not run in this repair phase. |

## Output Summary

### Universe

- Mode: `latest_snapshot_only`
- `survivor_only_universe`: `true`
- `survivorship_bias_disclosed`: `true`
- Historical snapshots requested: `65`
- Historical snapshots created: `1`
- Actual snapshot date: `2026-05-01`
- Limitation: free provider path uses current market rankings only; historical monthly rankings were not fabricated.

### Market

- Rows: `100,293`
- Symbols: `90`
- Date range: `2020-11-14` to `2026-05-06` UTC
- Requested assets: `100`
- Raw fetched assets: `90`
- QA-passed assets: `90`
- Full OHLCV assets: `90`
- Persisted assets: `90`
- Failed assets: `10`
- Sources observed: `ccxt_kraken`, `cryptocompare`

### On-Chain

- Observations: `311,666`
- Wide rows: `61,404`
- Symbols with observations: `43`
- Date range: `2020-11-14` to `2026-05-06` UTC
- Providers used: `coinmetrics`, `defillama`
- Providers unavailable/disabled included The Graph, Blockchair, and Dune.
- Limitation: sparse observations are not forward-filled and are lagged downstream by FeatureAgent.

### Features

- Full feature rows: `100,293`
- Full feature symbols: `90`
- On-chain available symbols: `43`
- Date range: `2020-11-14` to `2026-05-06` UTC
- Candidate full feature count: `133`
- Kept feature count after pruning: `57`
- Limitation: latest eligible universe membership; point-in-time membership not applied.

### Labels

- Label matrix rows: `97,593`
- Modeling dataset rows: `97,593`
- Symbols: `90`
- Date range: `2020-11-14` to `2026-04-06` UTC
- Horizons: `7`, `14`, `30`
- Dropped non-exact horizon rows: `0` for all horizons.
- Recommended embargo days: `30`

### Model

- Prediction rows: `470,406`
- Fold count: `47`
- Date range: `2022-05-02` to `2026-03-11` UTC
- Symbols: `90`
- Models run: `baseline_cross_sectional_mean`
- Feature sets: `market_only`, `market_plus_onchain`
- Manifest `alpha_status`: `not_evaluated_by_backtest`
- Manifest `research_status`: `no_candidate_signal_passed`
- Manifest `backtest_ready`: `false`
- Manifest `any_signal_gate_passed`: `false`
- Manifest `any_candidate_for_backtest`: `false`
- No model passed the signal gate.
- Baseline rows are diagnostic only and have `candidate_for_backtest=false`.
- No selected model is expected or required in this state.

Model verifier result after contract repair:

```text
Model validation: PASS
```

## Provider/API Status

- CMC key visible, but CMC three-year historical listings access is blocked by the current plan.
- CoinMetrics community API works and provided on-chain observations.
- DeFiLlama works and provided on-chain observations where mappings were available.
- Etherscan probe is fixed and returns HTTP 200, but this run's persisted on-chain providers were CoinMetrics and DeFiLlama.

## Alpha Status

- `alpha_verified`: not evaluated.
- BacktestAgent was not run.
- No verified alpha found under tested configurations.

## Next Recommended Action

Do not run PortfolioAgent or BacktestAgent from diagnostic/no-candidate outputs unless a later alpha-search phase exports a valid `candidate_for_backtest=true` signal.

The next research step is Phase 3 alpha-search expansion, but it should remain signal-gated: do not loosen thresholds or mark the diagnostic baseline as a candidate just to proceed.

## Phase 3 Alpha-Search Expansion

AlphaResearchAgent was run in signal-only mode after the ModelAgent no-candidate verifier repair.

Commands:

```bash
python3 -m py_compile agents/alpha_research_agent.py scripts/verify_alpha_research_run.py
python3 -m pytest tests/test_alpha_research_agent.py -q
python3 main.py alpha_research --config configs/run_config.yaml --section alpha_research
python3 scripts/verify_alpha_research_run.py --config configs/run_config.yaml --section alpha_research
```

Result:

- AlphaResearchAgent: PASS.
- AlphaResearch verifier: PASS.
- Experiments run: `80`.
- Experiments skipped by budget: `1,090`.
- Prediction rows: `16,491,600`.
- Fold metric rows: `3,040`.
- Candidate signals found: `3`.
- Final alpha passed: `0`.
- `export_candidate_to_predictions=false`.
- `canonical_outputs_mutated=false`.
- PortfolioAgent was not run.
- BacktestAgent was not run.

Candidate signals found:

| model_name | feature_set | label_target | horizon_days | mean_rank_ic | rank_ic_tstat | top_bottom_spread | percent_positive_ic_folds |
|---|---|---|---:|---:|---:|---:|---:|
| lightgbm | market_only | raw_forward_return | 14 | 0.0275 | 7.1034 | 0.0034 | 0.5892 |
| linear_ridge | market_only | raw_forward_return | 30 | 0.0142 | 6.3192 | 0.0041 | 0.5958 |
| random_forest | market_only | raw_forward_return | 14 | 0.0170 | 4.3800 | 0.0029 | 0.5617 |

Interpretation:

- Candidate signals found, but alpha is not verified until PortfolioAgent and BacktestAgent evaluate them.
- `data/predictions/model_predictions.parquet` was not overwritten by AlphaResearchAgent in this phase.
- Current `alpha_verified` remains not evaluated.

Detailed report:

- [Alpha signal search report](./ALPHA_SIGNAL_SEARCH_REPORT.md)

## Phase 4A Candidate Backtest Verification

AlphaResearchAgent candidate signals were exported to a separate portfolio-safe prediction file. Canonical `data/predictions/model_predictions.parquet` was not overwritten.

Commands:

```bash
python3 -m py_compile scripts/export_alpha_candidates_for_backtest.py
python3 scripts/export_alpha_candidates_for_backtest.py --config configs/run_config.yaml

python3 main.py portfolio --config configs/run_config.yaml --section portfolio_alpha_candidates
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_alpha_candidates

python3 main.py backtest --config configs/run_config.yaml --section backtesting_alpha_candidates
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_alpha_candidates
```

Candidate export:

- Candidate combos exported: `3`.
- Portfolio-safe prediction rows: `217,215`.
- Dropped overlapping out-of-sample duplicate rows: `401,220`.
- Forbidden realized/label/future/target columns in candidate prediction file: `0`.

PortfolioAgent:

- Config: `portfolio_alpha_candidates`.
- Selected model: `lightgbm`.
- Selected feature set: `market_only`.
- Selected horizon: `14`.
- Allocation mode: `signal_candidate_for_backtest`.
- Allocation rows: `22,253`.
- Rebalance count: `172`.
- Portfolio verifier: PASS.

BacktestAgent:

- Config: `backtesting_alpha_candidates`.
- Backtest verifier: PASS.
- Benchmark sanity: PASS.
- Best strategy by Sharpe: `top_20_vol_scaled`.
- Best strategy by total return: `top_20_vol_scaled`.
- Best strategy total return: `45.39%`.
- Best strategy Sharpe: `0.5030`.
- BTC total return: `305.50%`.
- ETH total return: `69.85%`.
- BTC/ETH 50-50 total return: `178.04%`.
- Equal-weight universe total return: `30.39%`.
- Any strategy passed alpha status: `false`.
- `alpha_verified=false`.

Conclusion:

No verified alpha found under tested candidate signals. The best candidate portfolio beat the equal-weight universe, but it did not beat BTC, ETH, or BTC/ETH 50-50 under the BacktestAgent criteria.

Detailed report:

- [Alpha backtest verification report](./ALPHA_BACKTEST_VERIFICATION_REPORT.md)

## Phase 4B Candidate-By-Candidate Verification

Each AlphaResearch candidate was exported and tested separately through PortfolioAgent and BacktestAgent.

All completed candidate runs passed both verifiers:

| candidate | Portfolio verifier | Backtest verifier | best strategy | total return | Sharpe | alpha_verified |
|---|---|---|---|---:|---:|---|
| `lightgbm_market_only_raw_forward_return_14d` | PASS | PASS | `top_20_vol_scaled` | 45.39% | 0.5030 | false |
| `linear_ridge_market_only_raw_forward_return_30d` | PASS | PASS | `top_5_equal_weight` | 147.36% | 0.7521 | false |
| `random_forest_market_only_raw_forward_return_14d` | PASS | PASS | `top_5_equal_weight` | -30.40% | 0.2288 | false |

Backtest benchmark returns over the aligned window were unchanged:

- BTC: `305.50%`
- ETH: `69.85%`
- BTC/ETH 50-50: `178.04%`
- Equal-weight universe: `30.39%`

Conclusion:

No verified alpha found across individually tested candidates. The `linear_ridge` candidate was the strongest candidate-by-candidate result and beat ETH plus equal-weight universe, but it did not beat BTC or BTC/ETH 50-50.

## Phase 5 Final Research Package

Final package documents:

- [Research results summary](./RESEARCH_RESULTS_SUMMARY.md)
- [Alpha findings report](./ALPHA_FINDINGS_REPORT.md)
- [Limitations and next steps](./LIMITATIONS_AND_NEXT_STEPS.md)
- [Reproducibility commands](./REPRODUCIBILITY_COMMANDS.md)
- [Alpha backtest verification report](./ALPHA_BACKTEST_VERIFICATION_REPORT.md)
- [API data readiness audit](./API_DATA_READINESS_AUDIT.md)

Final research conclusion:

CHF found statistically promising candidate signals, but after deterministic portfolio construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting, no strategy achieved verified alpha against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks under the tested configurations.

Final alpha status:

- `alpha_verified=false`
- No verified alpha found under tested configurations.

The latest-survivor universe limitation remains active because CMC three-year historical listings are blocked by the current plan.
