# Implementation Plan: CoinMarketCap Data Pipeline Completion & Monthly Maintenance

**Branch**: `001-cmc-data-pipeline` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-cmc-data-pipeline/spec.md`

## Summary

Complete the survivorship-free CoinMarketCap research dataset by adding four missing
data domains (coin map, fiat/USD exchange rates, global metrics, market index),
extending daily quotes toward full history, and delivering an idempotent monthly
maintenance job that appends new data without duplication. Approach: reuse the
existing `cmc_all_data/coinmarketcap_data/*` layout (Parquet + manifest JSON +
retained raw samples), add one collector per domain, and a single maintenance
orchestrator that upserts by primary key. Non-technical access is preserved by
extending the existing `convert_to_csv.py` + `HOW_TO_OPEN_THE_DATA.txt`.

## Technical Context

**Language/Version**: Python 3.11+ (project `.venv`; interpreter is 3.14 locally, code targets 3.11+)

**Primary Dependencies**: pandas 2.3.3, pyarrow (installed); `requests` for HTTP; stdlib `json`/`pathlib`. No new heavy deps.

**Storage**: Parquet (canonical) — one directory per domain under `cmc_all_data/coinmarketcap_data/`, each with `manifest.json` + `raw_api_samples/`. CSV is a derived export only.

**Testing**: pytest with recorded raw-JSON fixtures (no live API calls in tests); an idempotency test that runs the monthly upsert twice and asserts row-count equality.

**Target Platform**: Local/CLI on macOS + a monthly scheduled run (cron or the `/schedule` cloud agent).

**Project Type**: Data pipeline / CLI collectors (single project, no frontend in scope).

**Performance Goals**: Not latency-sensitive; must respect CMC rate limits with backoff. Monthly incremental run completes within a normal session (minutes, not hours).

**Constraints**: CMC API key from environment (never committed); graceful degradation + recorded achieved coverage when the paid plan has not unlocked full history; atomic writes (temp file + rename) to avoid partial-run corruption.

**Scale/Scope**: ~12k unique symbols; hundreds of thousands of daily quote rows growing monthly; 5 new/extended datasets + 1 orchestrator.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Data Integrity — No Survivorship Bias | Listings keep delisted coins; map carries `is_active`; quotes include delisted | ✅ PASS |
| II. Reproducibility | Every collector writes a manifest + retains raw samples; datasets regenerable from raw | ✅ PASS |
| III. Keyless-Source-First | Listings stay on keyless `cmc_web_pit`; paid API only for quotes/fx/global/index/map where keyless cannot supply | ✅ PASS |
| IV. USD Normalization | All monetary fields USD; fx dataset stores rate + provenance | ✅ PASS |
| V. Consumer Accessibility | `convert_to_csv.py` + docs extended to all new datasets | ✅ PASS |
| VI. Incremental Maintenance | Orchestrator upserts by primary key; idempotency covered by test | ✅ PASS |

No violations — Complexity Tracking left empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-cmc-data-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (per-dataset schema contracts)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
cmc_all_data/
├── coinmarketcap_data/
│   ├── pro_api_quotes_historical/          # EXISTING — extend to full range
│   ├── keyless_data_api_listings/          # EXISTING — unchanged (survivorship-free)
│   ├── pro_api_map/                        # NEW — coin reference map
│   ├── pro_api_fiat_fx_historical/         # NEW — fiat/USD exchange rates
│   ├── pro_api_global_metrics_historical/  # NEW — global metrics
│   ├── pro_api_index_historical/           # NEW — CMC market index (CMC100 + CMC20)
│   ├── pro_api_fear_greed/                 # NEW — Fear & Greed index (own dir since split)
│   └── pro_api_altcoin_season/             # NEW — Altcoin Season index (own dir since split)
│       └── (each: <dataset>.parquet, manifest.json, raw_api_samples/)
├── convert_to_csv.py                       # EXISTING — extend to new datasets
└── HOW_TO_OPEN_THE_DATA.txt                # EXISTING — document new datasets

coinmarketcap_extract/                      # EXISTING extract package (reuse conventions)
src/cmc/                                     # NEW shared code
├── client.py           # CMC HTTP client: auth from env, rate-limit backoff, retries
├── manifest.py         # read/write manifest.json (source, coverage, counts, run meta)
├── storage.py          # atomic parquet upsert (dedup by key, temp+rename)
├── collectors/
│   ├── map.py          # /v1/cryptocurrency/map
│   ├── quotes.py       # /v3/cryptocurrency/quotes/historical (full-range extension)
│   ├── fiat_fx.py      # /v1/fiat/map + price-conversion history
│   ├── global_metrics.py  # /v1/global-metrics/quotes/historical
│   └── index.py        # CMC index / fear-and-greed historical
└── maintain.py         # monthly orchestrator: per-dataset incremental upsert

tests/cmc/
├── fixtures/           # recorded raw JSON responses per endpoint
├── test_storage_idempotent.py   # run upsert twice → equal row counts
├── test_manifest.py
└── test_collectors.py  # parse fixtures → expected rows/schema
```

**Structure Decision**: Single-project data pipeline. Shared concerns (HTTP client,
manifest, atomic upsert) live in `src/cmc/`; one collector module per data domain;
data lands in the existing `cmc_all_data/coinmarketcap_data/` tree so the professor's
download layout and converter keep working.

## Complexity Tracking

> No constitution violations — not applicable.
