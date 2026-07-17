# Phase 1 Contracts: Collector & Maintenance CLI

These are the internal interface contracts for the pipeline (a CLI/data tool, not a
web service). Each collector is idempotent and writes `<dataset>.parquet` +
`manifest.json` + `raw_api_samples/` under its dataset directory.

## Common collector contract

```
run(start: date | None, end: date | None, out_dir: Path) -> ManifestResult
```

- Reads `CMC_API_KEY` from env (keyless collectors ignore it).
- Fetches `[start, end]`; `None` = full available range (subject to plan limits).
- Writes rows via `storage.upsert(df, key=KEY, path=...)` (atomic, dedup by key).
- Writes/updates `manifest.json` with source, coverage actually achieved, counts.
- Retains at least one raw JSON sample in `raw_api_samples/`.
- On plan-limit/429: backoff, then degrade to achievable range; record in manifest.

| Collector | Endpoint | Dataset dir | Upsert key |
|-----------|----------|-------------|------------|
| map | `/v1/cryptocurrency/map` | `pro_api_map/` | `cmc_id` |
| quotes | `/v3/cryptocurrency/quotes/historical` | `pro_api_quotes_historical/` | `(cmc_id, date)` |
| fiat_fx | `/v1/fiat/map` + price-conversion | `pro_api_fiat_fx_historical/` | `(currency, date)` |
| global_metrics | `/v1/global-metrics/quotes/historical` | `pro_api_global_metrics_historical/` | `date` |
| index | CMC100 / CMC20 index historical | `pro_api_index_historical/` | `date` (per index file) |
| fear_greed | `/v3/fear-and-greed/historical` | `pro_api_fear_greed/` | `date` |
| altcoin_season | `/v1/altcoin-season-index/historical` | `pro_api_altcoin_season/` | `date` |

## Maintenance orchestrator contract

```
maintain(datasets: list[str] | None = None, month: str | None = None) -> list[ManifestResult]
```

- For each dataset: read `manifest.coverage_end`, fetch only `> coverage_end` (default
  through the latest complete month), upsert, update manifest.
- MUST be idempotent: a second run for the same `month` adds zero rows and changes no
  existing rows (verified by `test_storage_idempotent.py`).
- Exit non-zero if any dataset fails; safe to re-run (resumes cleanly).

## Storage contract (`storage.upsert`)

```
upsert(df: DataFrame, key: list[str], path: Path) -> int   # returns final row count
```

- Concatenate with existing (if present), `drop_duplicates(subset=key, keep="last")`,
  sort by key, write temp file, `os.replace()` onto `path`.
- Guarantee: same input applied twice ⇒ identical file, identical row count.
