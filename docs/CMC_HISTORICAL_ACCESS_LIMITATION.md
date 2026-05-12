# CoinMarketCap Historical Access Limitation

## Current Probe Result

The runtime can see a CoinMarketCap API key, but the current plan does not provide the historical access needed for a three-year point-in-time universe.

Non-secret CoinMarketCap errors observed:

- `/v1/cryptocurrency/listings/historical` returned HTTP 400 for `2023-05-01`, `2024-05-01`, and `2026-03-31`.
- The CMC error stated that the current plan allows only `1 month` of historical access for `listings/historical`.
- `/v2/cryptocurrency/quotes/historical` returned HTTP 400 for `2023-05-01` to `2023-05-10`.
- The CMC error stated that the current plan allows only `12 months` of historical access for `quotes/historical`.
- `/v2/cryptocurrency/ohlcv/historical` returned HTTP 403 with CMC error code `1006`.
- The CMC error stated that the current plan does not support the historical OHLCV endpoint.

## Listings Versus Daily Market History

Daily historical quote or market data is not the same thing as point-in-time universe membership.

For professor-grade three-year historical universe construction, CHF needs `/v1/cryptocurrency/listings/historical` because that endpoint returns the assets listed at a historical date, including active and inactive tickers when available. That is the required source for monthly Top-N historical membership snapshots.

Historical quotes can help price assets after they are selected, but quotes alone cannot tell CHF which assets belonged in the universe at each past month. Using today’s survivor list with old prices would still be a latest-survivor universe and can overstate historical tradability.

## Research Decision

Do not build or run the CMC three-year historical universe path under the current plan.

Do not fake point-in-time historical listings from current rankings, quote history, or free-provider latest snapshots.

The valid fallback is:

- continue with the latest-survivor/free-provider baseline pipeline,
- explicitly disclose survivorship bias in every research report,
- treat results as conditional on the latest eligible survivor universe,
- avoid claiming professor-grade point-in-time universe validity.

## Requirement To Unblock

To satisfy the historical universe requirement, CHF needs one of:

- an upgraded CoinMarketCap plan with at least three years of `/v1/cryptocurrency/listings/historical` access, or
- another verified point-in-time historical listings source that includes inactive and active assets with historical ranks/market caps.

Until then, `professor_historical_universe_ready=false` and the recommended mode is `latest_survivor_baseline_until_cmc_upgrade`.
