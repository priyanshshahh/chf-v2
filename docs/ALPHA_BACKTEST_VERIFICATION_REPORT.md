# Alpha Backtest Verification Report

## Scope

Phase 4A exported AlphaResearchAgent candidate signals into a portfolio-safe prediction file, ran PortfolioAgent on the candidate export, and ran BacktestAgent on the resulting allocations.

BacktestAgent is the only alpha authority in this phase.

Required limitation:

> Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.

## Commands Run

```bash
python3 -m py_compile scripts/export_alpha_candidates_for_backtest.py
python3 scripts/export_alpha_candidates_for_backtest.py --config configs/run_config.yaml

python3 main.py portfolio --config configs/run_config.yaml --section portfolio_alpha_candidates
python3 scripts/verify_portfolio_run.py --config configs/run_config.yaml --section portfolio_alpha_candidates

python3 main.py backtest --config configs/run_config.yaml --section backtesting_alpha_candidates
python3 scripts/verify_backtest_run.py --config configs/run_config.yaml --section backtesting_alpha_candidates
```

## Candidate Export

Output files:

- `data/predictions/candidate_model_predictions.parquet`
- `data/predictions/candidate_model_leaderboard.parquet`
- `data/predictions/candidate_model_manifest.json`

Export result:

- Candidate combos exported: `3`
- Candidate prediction rows after de-duplication: `217,215`
- Overlapping out-of-sample prediction rows dropped deterministically: `401,220`
- Forbidden realized/label/future/target columns in portfolio prediction file: `0`
- `alpha_verified=false`
- `backtest_required=true`

Candidate signals exported:

| model_name | feature_set | label_target | horizon_days | rank_ic_mean | rank_ic_tstat | top_bottom_10_spread |
|---|---|---|---:|---:|---:|---:|
| lightgbm | market_only | raw_forward_return | 14 | 0.0275 | 7.1034 | 0.0034 |
| linear_ridge | market_only | raw_forward_return | 30 | 0.0142 | 6.3192 | 0.0041 |
| random_forest | market_only | raw_forward_return | 14 | 0.0170 | 4.3800 | 0.0029 |

PortfolioAgent selected the highest-ranked backtest-ready candidate available for the configured horizon:

- Selected model: `lightgbm`
- Selected feature set: `market_only`
- Selected horizon: `14`
- Allocation mode: `signal_candidate_for_backtest`
- `signal_gate_passed=true`
- `candidate_for_backtest=true`

## Portfolio Verification

Config section:

- `portfolio_alpha_candidates`

Portfolio outputs:

- `data/allocations_alpha_candidates/allocations_from_predictions.parquet`
- `data/allocations_alpha_candidates/allocation_coverage_report.parquet`
- `data/allocations_alpha_candidates/allocation_manifest.json`

Portfolio result:

- Allocation rows: `22,253`
- Rebalance count: `172`
- Unique symbols allocated: `90`
- Portfolio verifier: `PASS`

## Backtest Verification

Config section:

- `backtesting_alpha_candidates`

Backtest outputs:

- `data/backtests_alpha_candidates/backtest_summary.parquet`
- `data/backtests_alpha_candidates/benchmark_summary.parquet`
- `data/backtests_alpha_candidates/strategy_comparison.parquet`
- `data/backtests_alpha_candidates/cost_sweep.parquet`
- `data/backtests_alpha_candidates/benchmark_sanity_report.parquet`
- `data/backtests_alpha_candidates/alpha_report.json`
- `data/backtests_alpha_candidates/alpha_report.md`

Backtest verifier:

```text
Backtest validation: PASS
```

BacktestAgent result:

- `alpha_verified=false`
- `benchmark_sanity_passed=true`
- Any strategy passed alpha status: `false`
- Best strategy by Sharpe: `top_20_vol_scaled`
- Best strategy by total return: `top_20_vol_scaled`

## Benchmark Results

Backtest window:

- Start: `2022-12-15`
- End: `2026-03-24`

Benchmark returns are measured over each candidate's backtest window, not over a trailing five-year public chart window. See [Benchmark Verification](BENCHMARK_VERIFICATION.md).

| benchmark | total_return | CAGR | Sharpe | max_drawdown |
|---|---:|---:|---:|---:|
| BTC | 3.0550 | 0.5330 | 1.1346 | -0.4964 |
| ETH | 0.6985 | 0.1755 | 0.5701 | -0.6379 |
| BTC_ETH_50_50 | 1.7804 | 0.3663 | 0.8509 | -0.5550 |
| equal_weight_universe | 0.3039 | 0.0844 | 0.4627 | -0.6924 |
| cash | 0.0000 | 0.0000 | n/a | 0.0000 |

Benchmark sanity:

- BTC: PASS
- ETH: PASS
- BTC_ETH_50_50: PASS
- equal_weight_universe: PASS
- cash: PASS

## Strategy Comparison

| strategy_name | Sharpe | CAGR | max_drawdown | total_return | beats_btc | beats_eth | beats_btc_eth_50_50 | beats_equal_weight | alpha_status |
|---|---:|---:|---:|---:|---|---|---|---|---|
| top_20_vol_scaled | 0.5030 | 0.1210 | -0.7145 | 0.4539 | false | false | false | true | failed |
| score_weighted_vol_scaled | 0.4493 | 0.0746 | -0.7765 | 0.2657 | false | false | false | false | failed |
| top_10_vol_scaled | 0.4477 | 0.0666 | -0.7849 | 0.2351 | false | false | false | false | failed |
| top_10_equal_weight | 0.3970 | 0.0189 | -0.8308 | 0.0632 | false | false | false | false | failed |
| top_20_equal_weight | 0.3956 | 0.0325 | -0.7793 | 0.1105 | false | false | false | false | failed |
| score_weighted_long_only | 0.3214 | -0.0289 | -0.8316 | -0.0915 | false | false | false | false | failed |
| turnover_controlled | 0.2771 | 0.0057 | -0.6957 | 0.0187 | false | false | false | false | failed |
| top_5_vol_scaled | 0.2576 | -0.0886 | -0.8793 | -0.2621 | false | false | false | false | failed |
| top_5_equal_weight | 0.2531 | -0.0978 | -0.8875 | -0.2862 | false | false | false | false | failed |

## Cost Sweep Summary

Transaction cost used for the main backtest was `20 bps`.

For the best strategy by Sharpe, `top_20_vol_scaled`:

- It remained positive after `20 bps`, with total return `45.39%`.
- It did not beat BTC, ETH, or BTC/ETH 50-50.
- It beat equal-weight universe on total return and Sharpe, but not enough to pass the full alpha criteria.

Costs materially reduced strategies with higher turnover, especially top-5 and score-weighted variants.

## Conclusion

No verified alpha found under tested candidate signals.

The selected candidate signal created a positive-return portfolio, and the best strategy beat the equal-weight universe benchmark. However, no strategy beat BTC, ETH, and BTC/ETH 50-50 under the BacktestAgent criteria. BacktestAgent therefore correctly kept `alpha_verified=false`.

These results are useful but not sufficient for a research claim of tradable alpha.

## Phase 4B Candidate-By-Candidate Verification

Each exported candidate signal was tested separately with its own PortfolioAgent and BacktestAgent output directories.

Commands run:

```bash
python3 -m py_compile scripts/export_alpha_candidates_for_backtest.py
python3 scripts/export_alpha_candidates_for_backtest.py --config configs/run_config.yaml

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

Candidate-specific prediction files:

- `data/predictions/candidates_by_signal/lightgbm_market_only_raw_forward_return_14d_predictions.parquet`
- `data/predictions/candidates_by_signal/linear_ridge_market_only_raw_forward_return_30d_predictions.parquet`
- `data/predictions/candidates_by_signal/random_forest_market_only_raw_forward_return_14d_predictions.parquet`

All candidate-specific prediction files were portfolio-safe:

- No actual/realized/label/future/target/y columns.
- Finite predictions.
- No duplicate `date_ts + symbol` rows within candidate.
- `candidate_for_backtest=true`.
- `alpha_status=not_evaluated_by_backtest`.
- `alpha_verified=false` before BacktestAgent.

### Candidate Results

| candidate | best strategy | total return | CAGR | Sharpe | max drawdown | BTC return | ETH return | BTC/ETH 50-50 return | equal-weight return | beats BTC | beats ETH | beats BTC/ETH 50-50 | beats equal weight | benchmark sanity | alpha verified |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| `lightgbm_market_only_raw_forward_return_14d` | `top_20_vol_scaled` | 45.39% | 12.10% | 0.5030 | -71.45% | 305.50% | 69.85% | 178.04% | 30.39% | false | false | false | true | true | false |
| `linear_ridge_market_only_raw_forward_return_30d` | `top_5_equal_weight` | 147.36% | 31.84% | 0.7521 | -59.40% | 305.50% | 69.85% | 178.04% | 30.39% | false | true | false | true | true | false |
| `random_forest_market_only_raw_forward_return_14d` | `top_5_equal_weight` | -30.40% | -10.47% | 0.2288 | -86.86% | 305.50% | 69.85% | 178.04% | 30.39% | false | false | false | false | true | false |

### Phase 4B Conclusion

No verified alpha found across individually tested candidates.

The strongest individual result was `linear_ridge_market_only_raw_forward_return_30d`, whose best strategy beat ETH and the equal-weight universe but still failed against BTC and BTC/ETH 50-50. BacktestAgent therefore correctly kept `alpha_verified=false`.
