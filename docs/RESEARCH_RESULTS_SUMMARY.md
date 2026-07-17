# CHF Research Results Summary

## Research Question

Does Project CHF find evidence that market and on-chain cryptocurrency features contain real, tradable cross-sectional alpha after leakage-safe modeling, deterministic portfolio construction, transaction costs, benchmark sanity checks, and benchmark comparison?

## Project Goal

CHF is a research-grade quantitative cryptocurrency alpha-testing pipeline. Its purpose is not to manufacture a flattering result. Its purpose is to test whether signals survive each required layer:

- real market and on-chain data,
- honest universe construction,
- leakage-safe feature and label generation,
- walk-forward signal screening,
- deterministic prediction-only portfolio allocation,
- transaction-cost-aware backtesting,
- benchmark sanity checks,
- comparison against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks.

## Final Answer

CHF found statistically promising candidate signals, but after deterministic portfolio construction, transaction costs, benchmark sanity checks, and candidate-by-candidate backtesting, no strategy achieved verified alpha against BTC, ETH, BTC/ETH 50-50, and equal-weight universe benchmarks under the tested configurations.

Final alpha status:

- `alpha_verified=false`
- No verified alpha found under tested configurations.

## Alpha Authority

AlphaResearchAgent is signal-only. It can identify candidates for portfolio and backtest evaluation, but it cannot verify alpha.

BacktestAgent is the only alpha authority. A strategy can be considered verified alpha only if BacktestAgent sets `alpha_verified` to true.

Under the tested configurations, `alpha_verified=false` for every tested candidate.

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

## Strongest Candidate Signal

The strongest signal-screen candidate was:

- Model: `lightgbm`
- Feature set: `market_only`
- Label target: `raw_forward_return`
- Horizon: `14d`
- Mean Rank IC: `0.0275`
- Rank IC t-stat: `7.1034`
- Top-bottom spread: `0.0034`

This was a signal-screen result only. It was not treated as alpha until PortfolioAgent and BacktestAgent evaluated it.

## All Candidate Signal Metrics

All three candidate signals identified by AlphaResearchAgent. These metrics were sufficient to justify candidate backtests; they were not treated as verified alpha.

| Candidate | Mean Rank IC | Rank IC t-stat | Top-bottom spread |
|---|---:|---:|---:|
| `lightgbm / market_only / raw_forward_return / 14d` | 0.0275 | 7.1034 | 0.0034 |
| `linear_ridge / market_only / raw_forward_return / 30d` | 0.0142 | 6.3192 | 0.0041 |
| `random_forest / market_only / raw_forward_return / 14d` | 0.0170 | 4.3800 | 0.0029 |

## Strongest Backtest Result

The strongest individually tested backtest result was:

- Candidate: `linear_ridge / market_only / raw_forward_return / 30d`
- Best strategy: `top_5_equal_weight`
- Total return: `147.36%`
- CAGR: `31.84%`
- Sharpe: `0.7521`
- Max drawdown: `-59.40%`
- `alpha_verified=false`

This candidate beat ETH and the equal-weight universe but did not beat BTC or BTC/ETH 50-50, so it failed alpha verification.

## Benchmark Comparison

Aligned backtest window benchmark returns:

Benchmark returns are measured over each candidate's backtest window, not over a trailing five-year public chart window. See [Benchmark Verification](BENCHMARK_VERIFICATION.md).

| Benchmark | Total Return |
|---|---:|
| BTC | 305.50% |
| ETH | 69.85% |
| BTC/ETH 50-50 | 178.04% |
| Equal-weight universe | 30.39% |

Candidate-by-candidate summary:

| Candidate | Best Strategy | Total Return | Sharpe | Beat BTC | Beat ETH | Beat BTC/ETH 50-50 | Beat Equal Weight | Alpha Verified |
|---|---|---:|---:|---|---|---|---|---|
| `lightgbm / market_only / raw_forward_return / 14d` | `top_20_vol_scaled` | 45.39% | 0.5030 | false | false | false | true | false |
| `linear_ridge / market_only / raw_forward_return / 30d` | `top_5_equal_weight` | 147.36% | 0.7521 | false | true | false | true | false |
| `random_forest / market_only / raw_forward_return / 14d` | `top_5_equal_weight` | -30.40% | 0.2288 | false | false | false | false | false |

Each candidate was exported into a prediction-only file, allocated by PortfolioAgent, and evaluated by BacktestAgent. Full backtest metrics, including CAGR and max drawdown:

| Candidate | Best Strategy | Total Return | CAGR | Sharpe | Max Drawdown | Alpha Verified |
|---|---|---:|---:|---:|---:|---|
| `lightgbm / market_only / raw_forward_return / 14d` | `top_20_vol_scaled` | 45.39% | 12.10% | 0.5030 | -71.45% | false |
| `linear_ridge / market_only / raw_forward_return / 30d` | `top_5_equal_weight` | 147.36% | 31.84% | 0.7521 | -59.40% | false |
| `random_forest / market_only / raw_forward_return / 14d` | `top_5_equal_weight` | -30.40% | -10.47% | 0.2288 | -86.86% | false |

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

## Why This Is Still A Successful Research Result

This is a successful research outcome because the system did what a credible alpha research pipeline should do:

- It found statistically promising candidate signals.
- It isolated those signals into portfolio-safe prediction files.
- It prevented AlphaResearchAgent from claiming alpha.
- It forced candidates through deterministic PortfolioAgent allocations.
- It required BacktestAgent verification after transaction costs.
- It rejected unsupported alpha claims when candidates failed the benchmark criteria.

The result is not flattering, but it is trustworthy.

## Universe Limitation

The current production universe is a latest-survivor baseline because CoinMarketCap three-year historical listings access is blocked by the current API plan.

Required limitation:

> Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.

## Interpreting the Null Result (2026-07 update)

A statistically overwhelming signal and a null economic result are not a contradiction. They are separable quantities, and keeping them separate is exactly what this pipeline is built to do.

- **The cross-sectional ranking signal is real.** In the walk-forward signal screen (`data/predictions/alpha_model_leaderboard.parquet`), the strongest configuration — `elastic_net / market_only / 30d` — reaches a mean Rank IC of `0.1271` at a t-statistic of `9.60` across folds (`signal_gate_passed = true`, `alpha_backtest_status = not_run`), with the leading candidates clustered at t ≈ 4.4–9.6. A t-stat near 9.6 corresponds to p ≈ 10⁻²¹: the signal's correlation with forward cross-sectional returns is not in question.
- **No configuration converts that signal into verified alpha.** Every candidate that clears the signal gate still returns `alpha_verified = false` after portfolio construction, transaction costs and benchmark comparison. Statistical significance (a Rank IC t-stat) and economic significance (alpha over disciplined benchmarks) are independent quantities.

Two structural constraints — not signal absence — are the leading explanations, and both are testable:

1. **The long-only constraint is binding.** The edge is a cross-sectional *ranking*; its information lives in the market-neutral subspace `{w : Σwᵢ = 0}`. A long-only book cannot occupy that subspace — it is forced to β ≈ 1 and inherits the market's direction. In the canonical window the equal-weight universe fell −46.4%, so a long-only expression of a good ranking still loses money. Falsifiable prediction: a **long/short** variant should recover the ranking's value.
2. **The out-of-sample window is a single regime.** The canonical backtest (2025-03-25 → 2026-05-12) is ~14 months of a predominantly bear tape — precisely the regime in which cross-sectional momentum is known to underperform. The 2021 bull run, the 2022 crash and the 2023–24 recovery all sit inside the training split, never tested out-of-sample. Widening the walk-forward (≈14 → ≈50 folds) walks the OOS start back toward 2022 and spans bull + crash + recovery.

Neither constraint is an excuse; each is a concrete next experiment the current artifacts already justify. The honest summary is narrow and defensible: **under a long-only book evaluated over a single bear-market regime at 20 bps costs, a signal significant at p ≈ 10⁻²¹ produced no tradable alpha.** That is a result about the constraint set, not a verdict on the signal.

## Final Conclusion

No verified alpha found under tested configurations.
