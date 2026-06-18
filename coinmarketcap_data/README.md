# CoinMarketCap data (everything CHF pulled from CMC)

This folder consolidates **only** the data CHF obtained from CoinMarketCap, separated by
which CMC API produced it. Every row is a real API response — **no synthetic data**.
The narrative (endpoints, limits, credits, how each file is used) is in
[`../docs/COINMARKETCAP.md`](../docs/COINMARKETCAP.md).

> These files are normally regenerated under `data/external/` (gitignored). This folder
> holds a local, self-contained copy on your machine. Only this README is tracked on GitHub;
> regenerate the parquet/JSON files with the scripts in `scripts/build_cmc_*.py`.

## Layout

```
coinmarketcap_data/
├── keyless_data_api_listings/          # PUBLIC, keyless data-API (the production source)
│   ├── cmc_web_listings_historical.parquet   # 19,800 rows · 66 monthly snapshots · 2021-01→2026-06 · top-300
│   ├── cmc_web_history_manifest.json          # build provenance (survivorship_bias_free=true)
│   └── raw_api_samples/                        # untouched API responses (the raw shape)
│       ├── listings_historical_2021-01-01_top300.json
│       └── listings_historical_2024-02-01_top300.json
│
├── pro_api_quotes_historical/          # CMC PRO /v2 quotes/historical (needs CMC_API_KEY)
│   ├── cmc_quotes_history.parquet             # 225,854 rows · 299 symbols · ~36 months daily mcap/price/vol
│   └── cmc_prices_history.parquet             # same span, backtest-shaped (close/volume/market_cap)
│
└── pro_api_listings_historical/        # CMC PRO /v1 listings/historical (Hobbyist = 1 month deep)
    ├── cmc_listings_historical.parquet        # 100 rows · single PIT snapshot 2026-06-01
    ├── cmc_history_manifest.json
    └── raw_api_samples/
        └── listings_2026-06-01.json
```

## What each dataset is

| File | CMC endpoint | Key? | Span | Used for |
|---|---|---|---|---|
| `keyless_data_api_listings/cmc_web_listings_historical.parquet` | `data-api/v3/cryptocurrency/listings/historical` | **none** | 66 monthly snapshots, 2021-01→2026-06, top-300 incl. delisted | **Production universe** (`source: cmc_web_pit`) — survivorship-bias-FREE PIT membership |
| `pro_api_quotes_historical/cmc_quotes_history.parquet` | `v2/cryptocurrency/quotes/historical` | yes | ~36 months daily, 299 symbols | Earlier universe build input (market-cap history) |
| `pro_api_quotes_historical/cmc_prices_history.parquet` | derived from the same pull | yes | ~36 months daily | Backtest price/volume input |
| `pro_api_listings_historical/cmc_listings_historical.parquet` | `v1/cryptocurrency/listings/historical` | yes | 1 month (Hobbyist cap) | Proof-of-capability sample of the Pro PIT path |

## Column reference

- **`cmc_web_listings_historical.parquet`**: `snapshot_date, cmc_id, rank, symbol, name,
  slug, market_cap_usd, price_usd, volume_24h_usd, circulating_supply, total_supply,
  max_supply, num_market_pairs, date_added, raw_category_tags (list[str]), source`.
- **`cmc_quotes_history.parquet`**: `date, symbol, name, market_cap, price, volume_24h,
  categories`.
- **`cmc_prices_history.parquet`**: `date, symbol, close, volume, market_cap`.
- **`cmc_listings_historical.parquet`**: `cmc_id, provider_asset_id, coin_id, symbol,
  name, slug, market_cap_rank, market_cap_usd, volume_24h_usd, price_usd,
  is_active_at_snapshot, raw_category_tags, source, snapshot_date`.

## Regeneration

```bash
# keyless (production) — no key required
python3 scripts/build_cmc_web_history.py --start 2021-01-01 --end 2026-06-01 --top 300 --freq monthly
# Pro quotes/historical (needs CMC_API_KEY)
python3 scripts/build_cmc_quotes_history.py --top 300 --months 36
# Pro listings/historical (Hobbyist = 1 month)
python3 scripts/build_cmc_history.py --start 2026-06-01 --end 2026-06-01 --top 100
```

No API key appears in any file here — the raw samples are response bodies only.
</content>
