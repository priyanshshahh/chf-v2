# CoinMarketCap Historical Access Limitation

## ✅ RESOLVED — survivorship-free PIT achieved via the keyless public data-API

The Pro-API limitations below are still accurate, but the requirement is now met by a
verified alternative source (the second unblock option in §"Requirement To Unblock").

CoinMarketCap's **public, keyless** data-API that powers `coinmarketcap.com/historical`
returns the true Top-N ranking **as of any date back to 2013-05-05, including
since-delisted/inactive coins**:

```
GET https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical?date=YYYY-MM-DD&start=1&limit=1000&convert=USD
```

Each row carries `id` (stable cmc_id), `cmcRank`, `dateAdded` (point-in-time maturity),
category `tags`, `numMarketPairs`, and a point-in-time `quote` (price/marketCap/volume).
Verified point-in-time-accurate (BTC market cap $546B on 2021-01-01; LTC #2 in 2014).

This is ingested by `scripts/build_cmc_web_history.py` and consumed by the unified
`UniverseAgent` via `source: cmc_web_pit`. The production universe is therefore now
**survivorship-bias-free over 2021-01 → present (66 monthly snapshots, top-100)**, no
key and no plan upgrade required. `professor_historical_universe_ready=true` for this
source. The Pro `listings/historical` endpoint remains 400-blocked on the Hobbyist plan
(facts below), but it is no longer the only path.

---

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
