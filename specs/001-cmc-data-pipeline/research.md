# Phase 0 Research: CMC Data Pipeline Completion

## R1. Idempotent monthly append strategy

**Decision**: Per-dataset upsert keyed on a natural primary key, written atomically.
Read existing Parquet, concatenate new rows, `drop_duplicates(subset=KEY, keep="last")`,
sort, write to a temp file, then `os.replace()` over the target.

- Quotes key: `(cmc_id, date)`
- Global metrics / index key: `date` (one row per day; index also `index_id`)
- Exchange rates key: `(currency, date)`
- Map key: `cmc_id` (full replace acceptable — small reference table)

**Rationale**: `keep="last"` lets a re-fetched month correct prior values without
duplicating rows; atomic replace prevents partial-run corruption (Constitution VI).
A twice-run test asserting equal row counts proves idempotency (SC-004).

**Alternatives considered**: Append-only Parquet parts (rejected — no dedup, re-runs
duplicate); a database (rejected — over-engineered for a file-shipped dataset and
breaks the "download a folder" delivery model).

## R2. Full-history quotes extension under plan limits

**Decision**: Drive the historical-quotes collector from a date range derived from the
map's `first_historical_data`/`last_historical_data` per coin. Attempt full range; on a
plan-limit error, fall back to the widest range the key allows and record the achieved
`coverage_start`/`coverage_end` in the manifest.

**Rationale**: Satisfies FR-002 when the paid key is active and FR-013 (record achieved
coverage, no silent truncation) when it is not. Coverage is auditable per coin.

**Alternatives considered**: Hard-fail when full history is unavailable (rejected — the
key may not be upgraded yet; work must proceed and degrade gracefully).

## R3. Fiat/USD exchange-rate provenance

**Decision**: Store one row per `(currency, date)` with `usd_rate`, `source`, and
`provenance_date`. Regional conversions elsewhere reference this table rather than
embedding a bare number.

**Rationale**: Constitution IV requires conversions be auditable and reversible.

**Alternatives considered**: Convert on ingest and discard the rate (rejected —
irreversible, unverifiable).

## R4. Rate limiting & secrets

**Decision**: Single `client.py` with token-bucket-style pacing + exponential backoff
on HTTP 429/5xx; API key read from `CMC_API_KEY` env var. Keyless source needs no key.

**Rationale**: Centralizes retry policy; keeps secrets out of the repo (plan Constraints).

**Alternatives considered**: Per-collector HTTP handling (rejected — duplicated retry
logic, inconsistent limits).

## R5. Testing without a live API

**Decision**: Record representative raw JSON per endpoint into `tests/cmc/fixtures/`;
collectors parse fixtures in tests. Idempotency test operates on synthetic frames.

**Rationale**: Deterministic, offline, fast; also doubles as the retained raw samples
required by Constitution II.

**Alternatives considered**: Live calls in CI (rejected — flaky, costs quota, leaks key).

## Resolved unknowns

All Technical Context items are resolved; no remaining NEEDS CLARIFICATION.
