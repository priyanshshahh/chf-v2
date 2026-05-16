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

## Final Conclusion

No verified alpha found under tested configurations.
