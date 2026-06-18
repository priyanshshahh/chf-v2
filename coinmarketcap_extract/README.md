# coinmarketcap_extract/ — 3-year DAILY historical ticker list + data

Fresh extraction built from the live test of the supplied CMC key (2026-06-17).
Full write-up: [`../docs/COINMARKETCAP.md`](../docs/COINMARKETCAP.md).

## The one-line finding
The Pro `/v1/cryptocurrency/listings/historical` (historical **ticker list**) endpoint is
**still 1-month-capped** on this plan — the "3 years daily" upgrade applies to
`/v2 quotes/historical`, and the rate limit is now 300/min. We therefore extract the
3-year daily ticker list (active **+ inactive/delisted**) from CMC's **free, keyless**
data-API, which has no plan limit and reaches back to 2013.

## Contents
```
coinmarketcap_extract/
├── extract_cmc_daily_history.py     # resumable daily extractor (keyless; no key needed)
├── raw_daily_json/YYYY-MM-DD.json   # untouched API response per day (audit + resume cache)
├── processed/
│   ├── cmc_daily_listings_historical.parquet   # tidy combined daily table
│   ├── cmc_daily_listings_historical.csv
│   └── extraction_manifest.json     # authoritative coverage/row/coin counts + failures
└── extract_run.log                  # run log
```

## Data (final)
- Endpoint: `api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical` (keyless)
- **1,095 daily snapshots · 2023-06-19 → 2026-06-17 · top-200/day · 218,643 rows · 507 unique coins** (1 future date skipped).
- Survivorship-free via churn (507 unique vs ~200/day). Note: `is_active` is always 1 — use membership churn, not that column, as the delisted signal.
- Columns: `snapshot_date, cmc_id, rank, symbol, name, slug, is_active, market_cap_usd,
  price_usd, volume_24h_usd, circulating_supply, total_supply, max_supply,
  num_market_pairs, date_added, raw_category_tags, source`.
- See `processed/extraction_manifest.json` for the final totals.

## Run / extend
```bash
python3 coinmarketcap_extract/extract_cmc_daily_history.py --years 3 --top 200          # 3y daily
python3 coinmarketcap_extract/extract_cmc_daily_history.py --freq monthly --start 2013-05-05  # toward full history
```
Resumable: cached days are skipped, so extending only fetches missing dates.

No API key is required for this extraction and none is stored here.
(Note: a separate, earlier consolidation of CMC data lives in `../coinmarketcap_data/`.)
</content>
