# Alpha Findings Report

## Alpha Authority

AlphaResearchAgent is signal-only. It can identify candidates for portfolio and backtest evaluation, but it cannot verify alpha.

BacktestAgent is the only alpha authority. A strategy can be considered verified alpha only if BacktestAgent sets `alpha_verified` to true.

Final result:

- `alpha_verified=false` for every tested candidate.
- No verified alpha found under tested configurations.

## Signal Search Setup

AlphaResearchAgent was run in signal-only mode using the corrected latest-survivor/free-provider baseline pipeline outputs.

Run counts:

- Experiments run: `80`
- Experiments skipped by budget: `1,090`
- Prediction rows: `16,491,600`
- Fold metric rows: `3,040`
- Candidate signals found: `3`
- Final alpha passed inside AlphaResearchAgent: `0`

AlphaResearchAgent did not overwrite canonical `data/predictions/model_predictions.parquet`.

## Candidate Signal Metrics

| Candidate | Mean Rank IC | Rank IC t-stat | Top-bottom spread |
|---|---:|---:|---:|
| `lightgbm / market_only / raw_forward_return / 14d` | 0.0275 | 7.1034 | 0.0034 |
| `linear_ridge / market_only / raw_forward_return / 30d` | 0.0142 | 6.3192 | 0.0041 |
| `random_forest / market_only / raw_forward_return / 14d` | 0.0170 | 4.3800 | 0.0029 |

These metrics were sufficient to justify candidate backtests. They were not treated as verified alpha.

## Candidate-By-Candidate Backtest Results

Each candidate was exported into a prediction-only file, allocated by PortfolioAgent, and evaluated by BacktestAgent.

| Candidate | Best Strategy | Total Return | CAGR | Sharpe | Max Drawdown | Alpha Verified |
|---|---|---:|---:|---:|---:|---|
| `lightgbm / market_only / raw_forward_return / 14d` | `top_20_vol_scaled` | 45.39% | 12.10% | 0.5030 | -71.45% | false |
| `linear_ridge / market_only / raw_forward_return / 30d` | `top_5_equal_weight` | 147.36% | 31.84% | 0.7521 | -59.40% | false |
| `random_forest / market_only / raw_forward_return / 14d` | `top_5_equal_weight` | -30.40% | -10.47% | 0.2288 | -86.86% | false |

## Benchmark Comparison

| Benchmark | Total Return |
|---|---:|
| BTC | 305.50% |
| ETH | 69.85% |
| BTC/ETH 50-50 | 178.04% |
| Equal-weight universe | 30.39% |

Candidate benchmark outcomes:

- `lightgbm / 14d` beat equal-weight universe but did not beat BTC, ETH, or BTC/ETH 50-50.
- `linear_ridge / 30d` beat ETH and equal-weight universe but did not beat BTC or BTC/ETH 50-50.
- `random_forest / 14d` did not beat any required benchmark.

## Why Each Candidate Failed Alpha Verification

`lightgbm / market_only / raw_forward_return / 14d`:

- Positive return and positive Sharpe.
- Beat equal-weight universe.
- Failed against BTC, ETH, and BTC/ETH 50-50.
- `alpha_verified=false`.

`linear_ridge / market_only / raw_forward_return / 30d`:

- Strongest candidate-by-candidate result.
- Beat ETH and equal-weight universe.
- Failed against BTC and BTC/ETH 50-50.
- `alpha_verified=false`.

`random_forest / market_only / raw_forward_return / 14d`:

- Negative total return.
- Severe drawdown.
- Failed all required benchmark comparisons.
- `alpha_verified=false`.

## Final Statement

No verified alpha found under tested configurations.

The research found signal candidates worth studying, especially the `linear_ridge / market_only / raw_forward_return / 30d` candidate, but BacktestAgent rejected all candidates as verified alpha under the benchmark rules.
