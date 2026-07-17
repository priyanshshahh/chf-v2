# Quickstart: CMC Data Pipeline Completion

Validation guide proving the feature works end-to-end. Uses the project `.venv`.

## Prerequisites

- Project `.venv` active (pandas + pyarrow available).
- For live collection: `export CMC_API_KEY=...` (paid Builder tier for full history).
- Tests need no key (they use recorded fixtures).

## 1. Run the test suite (no API key needed)

```
.venv/bin/python -m pytest tests/cmc -q
```

Expected: all pass, including `test_storage_idempotent.py` (upsert applied twice →
equal row count) and `test_collectors.py` (fixtures parse to expected schema).

## 2. Collect one dataset (live)

```
.venv/bin/python -m src.cmc.collectors.map --out cmc_all_data/coinmarketcap_data/pro_api_map
```

Expected: `cmc_map.parquet` + `manifest.json` + a file under `raw_api_samples/`.
Manifest `row_count` > 0; delisted coins present with `is_active=false`.

## 3. Run the idempotent monthly maintenance

```
.venv/bin/python -m src.cmc.maintain            # first run: appends latest month
.venv/bin/python -m src.cmc.maintain            # second run: appends nothing
```

Expected: after the second run every dataset's `row_count` is unchanged from the first
(Success Criterion SC-004).

### Monthly schedule (T031)

Run maintenance automatically on the 1st of each month at 03:00 via cron:

```
0 3 1 * * cd <repo> && .venv/bin/python -m src.cmc.maintain
```

Because maintenance is idempotent, an accidental double-fire is harmless. In the
future this can also be wired into the project scheduler (`jobs/scheduler.py`)
instead of cron.

## 4. Export to CSV for the researcher

```
cd cmc_all_data && python3 convert_to_csv.py
```

Expected: `csv_export/` now includes the four new datasets; `HOW_TO_OPEN_THE_DATA.txt`
documents their columns, USD units, and `YYYY-MM-DD` dates (SC-005).

## Success check (maps to spec)

- SC-001: four new dataset dirs exist, each with a manifest + raw sample.
- SC-002/003: quotes extend past 36 months; map resolves all referenced coins incl. delisted.
- SC-004: double maintenance run = zero duplicates.
- SC-005/006: converter covers new files; all money USD, all dates `YYYY-MM-DD`.
