# Phase 1 Data Model: CMC Data Pipeline Completion

All monetary fields are USD. All dates are `YYYY-MM-DD`. Primary keys listed per entity
drive idempotent upsert (see research.md R1).

## Coin Reference Map — `pro_api_map/cmc_map.parquet`

| Field | Type | Notes |
|-------|------|-------|
| cmc_id | int | **PK**. Stable CoinMarketCap id |
| symbol | str | Ticker (may be reused across coins) |
| name | str | Display name |
| slug | str | URL slug |
| is_active | bool | Active/inactive (delisted → false) |
| first_historical_data | date | Earliest data date available |
| last_historical_data | date | Latest data date available |
| source | str | e.g., `pro_api:/v1/cryptocurrency/map` |

Validation: `cmc_id` unique; delisted coins retained with `is_active=false` (FR-001, FR-008).

## Daily Quote — `pro_api_quotes_historical/cmc_quotes_history.parquet` (extended)

| Field | Type | Notes |
|-------|------|-------|
| date | date | **PK part** |
| cmc_id | int | **PK part**. Join key to map |
| symbol | str | Denormalized for convenience |
| price | float | USD |
| market_cap | float | USD |
| volume_24h | float | USD |

Validation: `(cmc_id, date)` unique; coverage extended toward full history incl.
delisted coins (FR-002); achieved coverage recorded in manifest (FR-013).

## Exchange Rate — `pro_api_fiat_fx_historical/cmc_fiat_fx.parquet`

| Field | Type | Notes |
|-------|------|-------|
| date | date | **PK part** |
| currency | str | **PK part**. ISO currency code |
| usd_rate | float | Units of currency per 1 USD (or inverse — documented) |
| source | str | Provenance source |
| provenance_date | date | Effective date of the rate |

Validation: `(currency, date)` unique; provenance retained (FR-003, Constitution IV).

## Global Metric — `pro_api_global_metrics_historical/cmc_global_metrics.parquet`

| Field | Type | Notes |
|-------|------|-------|
| date | date | **PK** |
| total_market_cap | float | USD |
| total_volume_24h | float | USD |
| btc_dominance | float | Percent |
| eth_dominance | float | Percent |
| active_cryptocurrencies | int | Count |

Validation: `date` unique; monetary fields USD (FR-004, FR-007).

## Market Index — `pro_api_index_historical/cmc100_index.parquet` + `cmc20_index.parquet`

| Field | Type | Notes |
|-------|------|-------|
| date | date | **PK** (per index file: `cmc100_index.parquet` / `cmc20_index.parquet`) |
| value | float | Index value |

Validation: `date` unique within each index file; continuous daily series (FR-005).
Fear & Greed and Altcoin Season are separate datasets in their own dirs
(`pro_api_fear_greed/`, `pro_api_altcoin_season/`), each keyed on `date`.

## Dataset Manifest — `<dataset>/manifest.json` (every dataset)

```json
{
  "dataset": "pro_api_global_metrics_historical",
  "source": "pro_api:/v1/global-metrics/quotes/historical",
  "coverage_start": "2013-04-28",
  "coverage_end": "2026-06-30",
  "row_count": 4813,
  "symbol_count": null,
  "generated_by": "src/cmc/collectors/global_metrics.py",
  "run": {"achieved_full_history": true, "notes": ""}
}
```

Validation: present for every dataset; updated on any schema/coverage change
(FR-006, Constitution II & Development Workflow).
