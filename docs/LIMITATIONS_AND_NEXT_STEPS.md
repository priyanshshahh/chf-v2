# Limitations And Next Steps

## Current Limitations

### CoinMarketCap Historical Universe Access

CoinMarketCap three-year historical listings are blocked by the current API plan.

Observed access:

- `/v1/cryptocurrency/listings/historical`: recent-window access works, but three-year access is blocked.
- Historical listings access window observed: `1 month`.
- `/v2/cryptocurrency/quotes/historical`: access window observed as `12 months`.
- `/v2/cryptocurrency/ohlcv/historical`: unsupported under the current plan.

This blocks professor-grade three-year point-in-time universe construction from CMC historical listings.

### Latest-Survivor Universe

The current production research run uses the latest-survivor/free-provider baseline universe.

Required limitation:

> Results are conditional on the latest eligible survivor universe and may overstate historical tradability because full historical membership and delisting data are not yet modeled.

### No Full Point-In-Time Historical Universe Yet

The current pipeline does not yet have a verified three-year historical active + inactive universe membership file. It must not claim point-in-time historical universe validity until historical listings are available and verified.

### Sparse On-Chain Coverage

On-chain data was available for fewer assets than market data. CoinMetrics community and DeFiLlama worked, but on-chain coverage remained sparse relative to the full market universe.

### Alpha Search Budget

The final signal-search expansion ran:

- `80` experiments,
- skipped `1,090` configured experiments by budget,
- tested only a bounded sample of the larger search grid.

This is enough to evaluate the current candidates, but not enough to exhaust the full alpha-search space.

### Market-Only Signals Led This Run

The candidates that passed the signal screen were market-only:

- `lightgbm / market_only / raw_forward_return / 14d`
- `linear_ridge / market_only / raw_forward_return / 30d`
- `random_forest / market_only / raw_forward_return / 14d`

On-chain candidates did not produce a verified alpha result in this bounded run.

### No Verified Alpha Yet

BacktestAgent reported `alpha_verified=false` for every individually tested candidate.

## Recommended Next Steps

1. Upgrade or obtain access to a full historical listings source.

   Preferred: CoinMarketCap historical listings over the complete research window, including active and inactive assets.

2. Rebuild the universe with point-in-time membership.

   Use historical monthly snapshots and avoid current-survivor universe bias.

3. Rerun the full canonical pipeline.

   Rebuild market data, on-chain data, features, labels, model predictions, portfolio allocations, and backtests using the corrected historical universe.

4. Expand the alpha-search budget.

   Run more of the configured grid beyond the initial `80` experiments.

5. Test stronger label targets and regime filters.

   Prioritize excess-return labels, volatility-adjusted labels, cross-sectional rank targets, and explicit bull/bear/high-volatility regimes.

6. Add deeper robustness analysis.

   Evaluate subperiod stability, cost sensitivity, turnover sensitivity, and regime-specific performance.

7. Improve on-chain coverage.

   Add more robust mapping and provider coverage where possible. Keep sparse observations honest and avoid forward-filling unavailable on-chain data.

8. Retest candidate strategies after point-in-time universe correction.

   Compare results before and after survivorship-bias correction.

9. Preserve the alpha verification contract.

   AlphaResearchAgent may nominate candidates, but only BacktestAgent may verify alpha.

## Current Research Direction

The best near-term research direction is not to tune the existing result until it passes. The correct next step is to reduce universe bias, expand the experiment grid, and rerun the same strict verification process.
