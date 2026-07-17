# Tasks: CoinMarketCap Data Pipeline Completion & Monthly Maintenance

**Feature**: `specs/001-cmc-data-pipeline` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

Tests are included because the spec/plan explicitly require pytest fixtures and an
idempotency test. `[P]` = parallelizable (different files, no incomplete deps).

## Phase 1: Setup

- [X] T001 Create package layout `src/cmc/`, `src/cmc/collectors/`, and `tests/cmc/fixtures/` with `__init__.py` files
- [X] T002 Add `requests` to the project dependencies and confirm it imports in the `.venv`
- [X] T003 [P] Add `.env.example` documenting `CMC_API_KEY` and ensure real env files stay git-ignored
- [X] T004 [P] Add a `pytest` config (or `pyproject`/`pytest.ini` section) registering `tests/cmc` as a test path

## Phase 2: Foundational (blocking prerequisites)

- [X] T005 Implement CMC HTTP client with env-based auth, rate-limit pacing, and 429/5xx exponential backoff in `src/cmc/client.py`
- [X] T006 Implement manifest read/write (source, coverage_start/end, row/symbol counts, run metadata) in `src/cmc/manifest.py`
- [X] T007 Implement atomic upsert `upsert(df, key, path)` (concat → drop_duplicates keep=last → sort → temp file → os.replace) in `src/cmc/storage.py`
- [X] T008 [P] Write `tests/cmc/test_storage_idempotent.py`: apply the same frame twice, assert equal row count and identical file (SC-004)
- [X] T009 [P] Write `tests/cmc/test_manifest.py`: round-trip a manifest and assert coverage/counts fields

**Checkpoint**: client, manifest, and idempotent storage exist and are tested — all collectors depend on these.

## Phase 3: User Story 1 — Coin reference map (P1) 🎯 MVP

**Goal**: A stable-id reference table incl. delisted coins, keyed on `cmc_id`.
**Independent test**: a known delisted coin resolves to a stable id with `is_active=false`.

- [X] T010 [P] [US1] Record a raw `/v1/cryptocurrency/map` sample into `tests/cmc/fixtures/map_sample.json`
- [X] T011 [US1] Implement the map collector (parse id/symbol/name/slug/is_active/first_/last_historical_data) writing `cmc_map.parquet` + manifest + raw sample in `src/cmc/collectors/map.py`
- [X] T012 [P] [US1] Write `tests/cmc/test_map_collector.py`: parse fixture → expected schema; assert a delisted row has `is_active=false`

**Checkpoint**: map dataset produced and joinable — foundation for quotes coverage.

## Phase 4: User Story 2 — Full-history daily quotes incl. delisted (P1)

**Goal**: Extend daily quotes toward full history keyed on `(cmc_id, date)`, all USD.
**Independent test**: a coin delisted >36 months ago has daily records in its active period.

- [X] T013 [P] [US2] Record a raw `/v3/cryptocurrency/quotes/historical` daily sample (retained at `pro_api_quotes_historical/raw_api_samples/quotes_daily_sample.json`)
- [X] T014 [US2] Full-universe quotes collector: batches ids under the 10000-datapoint cap, drives the universe from the map (active + in-window inactive), upserts on `(cmc_id, date)`, flushes incrementally (resumable) in `src/cmc/collectors/quotes.py`
- [X] T015 [US2] Graceful degradation: `time_start` pinned to the plan's rolling 36-month window; per-batch errors recorded; credit-budget hard-stop with `coins_done`/`stopped_reason`/`credits_used_for_this_dataset` in the manifest (FR-013) in `src/cmc/collectors/quotes.py`
- [X] T016 [P] [US2] Write `tests/cmc/test_quotes_collector.py`: fixture → USD fields present, `(cmc_id, date)` unique, delisted coin retained

**Checkpoint**: survivorship-free daily quotes extend beyond 36 months.

## Phase 5: User Story 6 — Idempotent monthly maintenance (P1)

**Goal**: One orchestrator appends the latest month across datasets without duplication.
**Independent test**: running the job twice for the same month adds zero rows.

- [X] T017 [US6] Implement the maintenance orchestrator (per dataset: read manifest coverage_end → fetch only newer → upsert → update manifest) in `src/cmc/maintain.py`
- [X] T018 [US6] Add a CLI entrypoint `python -m src.cmc.maintain [--datasets ...] [--month YYYY-MM]` with non-zero exit on any dataset failure
- [X] T019 [P] [US6] Write `tests/cmc/test_maintain_idempotent.py`: run maintain twice against fixtures, assert per-dataset row counts unchanged

**Checkpoint**: safe, repeatable monthly maintenance proven idempotent.

## Phase 6: User Story 3 — Fiat/USD exchange rates (P2)

**Goal**: `(currency, date)` rates with provenance so conversions are auditable.
**Independent test**: a date+currency lookup returns a USD rate with source + provenance_date.

- [X] T020 [P] [US3] Record raw `/v1/fiat/map` + price-conversion samples into `tests/cmc/fixtures/fiat_fx_sample.json`
- [X] T021 [US3] Implement the fiat_fx collector writing `cmc_fiat_usd_snapshot.parquet` (usd_to_fiat_rate, source, snapshot_date) + manifest + raw sample in `src/cmc/collectors/fiat_fx.py`
- [X] T022 [P] [US3] Write `tests/cmc/test_fiat_fx_collector.py`: fixture → `(currency, date)` unique, provenance fields populated

## Phase 7: User Story 4 — Global market metrics (P2)

**Goal**: Daily total market cap/volume/dominance in USD keyed on `date`.
**Independent test**: a historical date returns total market cap + BTC dominance.

- [X] T023 [P] [US4] Record a raw `/v1/global-metrics/quotes/historical` sample into `tests/cmc/fixtures/global_metrics_sample.json`
- [X] T024 [US4] Implement the global_metrics collector writing `cmc_global_metrics.parquet` + manifest + raw sample in `src/cmc/collectors/global_metrics.py`
- [X] T025 [P] [US4] Write `tests/cmc/test_global_metrics_collector.py`: fixture → USD fields present, `date` unique

## Phase 8: User Story 5 — CMC market index (P3)

**Goal**: Continuous daily index series keyed on `(date, index_id)`.
**Independent test**: a date range returns a continuous daily index series.

- [X] T026 [P] [US5] Record a raw CMC index / fear-greed historical sample into `tests/cmc/fixtures/index_sample.json`
- [X] T027 [US5] Implement the index collector writing `cmc100_index.parquet` + `cmc20_index.parquet` + manifests + raw samples in `src/cmc/collectors/cmc_index.py`
- [X] T028 [P] [US5] Write `tests/cmc/test_index_collector.py`: fixture → `date` unique per index file, continuous dates

## Phase 9: Polish & Cross-Cutting

- [X] T029 [P] Extend `cmc_all_data/convert_to_csv.py` coverage to the four new datasets (it already globs recursively — add a smoke test)
- [X] T030 [P] Update `cmc_all_data/HOW_TO_OPEN_THE_DATA.txt` documenting each new dataset's columns, USD units, and `YYYY-MM-DD` dates (FR-012, SC-005)
- [X] T031 [P] Add a monthly schedule wiring note/config (cron or `/schedule`) invoking `python -m src.cmc.maintain` (see "Monthly schedule" in quickstart.md)
- [X] T032 Run `/ecc:python-review` over `src/cmc/` and address findings (Development Workflow) — review applied 2026-07-05; T033 blocked on valid CMC_API_KEY
- [ ] T033 Run `quickstart.md` end-to-end and confirm SC-001…SC-006

## Dependencies & Execution Order

- **Setup (P1 tasks T001–T004)** → **Foundational (T005–T009)** block everything.
- **US1 (map)** should land first — **US2 (quotes)** consumes the map's historical-date fields.
- **US6 (maintenance)** depends on Foundational + at least one collector; best after US1/US2.
- **US3, US4, US5** are independent of each other and can proceed in parallel once Foundational is done.
- **Polish (T029–T033)** last.

## Parallel Opportunities

- Foundational tests T008, T009 in parallel.
- All fixture-recording tasks (T010, T013, T020, T023, T026) in parallel.
- Per-collector test tasks (T012, T016, T022, T025, T028) in parallel with each other.
- US3, US4, US5 collectors can be built concurrently by different workers.

## MVP Scope

**Minimum viable**: Phase 1 + Phase 2 + **User Story 1 (coin map)** — delivers a
trustworthy, survivorship-aware reference table on its own. Add **US2 (full-history
quotes)** and **US6 (idempotent maintenance)** to reach the core research asset the
professor asked for.

**Total tasks**: 33 (Setup 4, Foundational 5, US1 3, US2 4, US6 3, US3 3, US4 3, US5 3, Polish 5).
