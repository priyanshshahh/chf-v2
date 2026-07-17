# Feature Specification: CoinMarketCap Data Pipeline Completion & Monthly Maintenance

**Feature Branch**: `001-cmc-data-pipeline`

**Created**: 2026-07-01

**Status**: Draft

**Input**: Complete the CoinMarketCap historical dataset (four missing data domains + full-history quotes) and add an idempotent monthly maintenance job, for a non-technical researcher who opens the data in SAS/Excel.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reference map to identify every coin, active or delisted (Priority: P1)

The researcher needs a single reference table that maps each coin's stable identifier to its ticker, name, slug, and active/inactive status, so that every other dataset can be joined reliably even when tickers are reused or a coin is delisted.

**Why this priority**: Every other dataset (quotes, listings, global metrics) is keyed on coin identity. Without a trustworthy id↔symbol map, joins are ambiguous and delisted coins cannot be resolved. It is the foundation the other stories depend on.

**Independent Test**: Produce the map dataset and confirm a known delisted coin (e.g., LUNA/Terra Classic) resolves to a stable id with an "inactive" flag, and that at least one reused ticker maps to distinct ids. Delivered value: reliable joins across all datasets.

**Acceptance Scenarios**:

1. **Given** the map dataset, **When** the researcher looks up a delisted coin by name, **Then** they find its stable id, ticker, slug, and an inactive status.
2. **Given** two coins that shared a ticker at different times, **When** joined by stable id, **Then** each resolves to a distinct record.

---

### User Story 2 - Full-history daily quotes including delisted coins (Priority: P1)

The researcher needs daily price, market cap, and volume for every coin across its entire available history (not just the most recent ~36 months), including coins that have since been delisted, so that backtests are free of survivorship bias.

**Why this priority**: The core research asset is a survivorship-free daily price/market-cap history. Extending coverage to full history is the single most valuable data expansion once the paid plan unlocks it.

**Independent Test**: Pick a coin delisted more than 36 months ago and confirm its daily quotes exist for dates in its active life. Delivered value: unbiased long-horizon return series.

**Acceptance Scenarios**:

1. **Given** the extended quotes dataset, **When** the researcher filters to a pre-36-month date range, **Then** daily records are present for coins that were active then.
2. **Given** a coin delisted years ago, **When** its history is requested, **Then** daily records exist up to its delisting and stop thereafter.
3. **Given** all quote records, **When** any monetary field is inspected, **Then** it is expressed in USD.

---

### User Story 3 - Fiat / USD historical exchange rates (Priority: P2)

The researcher needs historical fiat-to-USD exchange rates with provenance, so that any regionally quoted figure can be converted to USD and the conversion can be audited or reversed.

**Why this priority**: USD normalization is a project principle; without retained exchange-rate provenance, conversions are unverifiable. Needed for correctness but not for the core price series.

**Independent Test**: Convert a known regional value to USD for a historical date using the stored rate and confirm the rate's source and date are recorded. Delivered value: auditable currency normalization.

**Acceptance Scenarios**:

1. **Given** the exchange-rate dataset, **When** a date and currency are supplied, **Then** a USD rate with its source and date is returned.
2. **Given** a converted figure, **When** its provenance is inspected, **Then** the originating rate and date are recoverable.

---

### User Story 4 - Historical global market metrics (Priority: P2)

The researcher needs daily market-wide metrics (total market cap, total volume, BTC/ETH dominance, and per-market breakdown) over history, so that individual coin behavior can be studied against the whole market.

**Why this priority**: Provides market context/regime signals that enrich research; valuable but secondary to per-coin history.

**Independent Test**: Retrieve total market cap and BTC dominance for a historical date and confirm they are internally consistent. Delivered value: market-context features.

**Acceptance Scenarios**:

1. **Given** the global-metrics dataset, **When** a historical date is requested, **Then** total market cap, total volume, and dominance figures are present in USD.

---

### User Story 5 - CMC market index history (Priority: P3)

The researcher needs the history of CoinMarketCap's market index (e.g., CMC 20 / CMC 100 / a Fear-&-Greed-style sentiment index), so that a single market benchmark and sentiment series are available.

**Why this priority**: A convenient benchmark/sentiment series; useful but the smallest and most optional of the datasets.

**Independent Test**: Retrieve the index value for a range of historical dates and confirm a continuous daily series. Delivered value: ready-made market benchmark.

**Acceptance Scenarios**:

1. **Given** the index dataset, **When** a date range is requested, **Then** a continuous daily index series is returned.

---

### User Story 6 - Idempotent monthly maintenance (Priority: P1)

The researcher (or a scheduled job on their behalf) runs a single monthly maintenance step that appends the latest month of data across all datasets without re-downloading existing history, and re-running it never duplicates data.

**Why this priority**: The stated long-term goal is to build history forward via monthly appends rather than repeated full extraction; prior attempts failed. Idempotency is what makes maintenance safe and sustainable.

**Independent Test**: Run the monthly job twice against the same period and confirm the second run adds zero new rows and changes no existing rows. Delivered value: safe, repeatable maintenance.

**Acceptance Scenarios**:

1. **Given** existing history, **When** the monthly job runs for a new month, **Then** only that month's new records are appended.
2. **Given** a completed monthly run, **When** the same job is run again for the same month, **Then** no rows are duplicated and no existing rows change.
3. **Given** a run that fails partway, **When** it is re-run, **Then** it resumes to a correct, non-duplicated state.

---

### Edge Cases

- A coin is delisted between two monthly runs — its final active-period records are retained; no future rows are fabricated.
- A ticker is reused by a new coin — records remain distinguishable by stable id.
- A source is temporarily unavailable during a monthly run — the run fails safely and can be re-run without duplication.
- A historical date has no data (coin not yet listed) — absence is represented as missing, not as zero.
- The paid plan has not yet unlocked full history — full-history extension degrades gracefully to the currently available range and records the achieved coverage in the manifest.
- A new data column appears upstream — schema change requires documentation + manifest update before publication.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST produce a coin reference map keyed on a stable coin identifier, containing id, ticker/symbol, name, slug, and active/inactive status.
- **FR-002**: The system MUST extend daily quotes (price, market cap, volume) to the full available history per coin, including coins that are now delisted.
- **FR-003**: The system MUST produce a historical fiat-to-USD exchange-rate dataset that records, for each rate, its source and effective date (provenance).
- **FR-004**: The system MUST produce a historical global-metrics dataset (total market cap, total volume, dominance, per-market breakdown) in USD.
- **FR-005**: The system MUST produce a historical market-index dataset as a continuous daily series.
- **FR-006**: Every dataset MUST be accompanied by a manifest recording its source, date coverage, and row/symbol counts, and MUST retain representative raw samples.
- **FR-007**: All monetary values across all datasets MUST be stored in USD, and all dates MUST use the `YYYY-MM-DD` format.
- **FR-008**: Historical listings MUST continue to include delisted/inactive coins (no survivorship bias) and MUST NOT be re-filtered against today's active catalog.
- **FR-009**: The system MUST provide a monthly maintenance operation that appends the latest month across all datasets without re-downloading existing history.
- **FR-010**: The monthly maintenance operation MUST be idempotent — re-running for the same period MUST NOT duplicate rows or alter existing rows.
- **FR-011**: Each dataset MUST record which source produced it (keyless source vs. paid API), preferring the keyless source wherever it can supply the data.
- **FR-012**: The one-command Parquet→CSV converter and its plain-language documentation MUST cover all newly added datasets so a non-technical user can open them.
- **FR-013**: When full history cannot yet be retrieved (plan limits), the system MUST record the actually achieved coverage rather than silently truncating without note.

### Key Entities *(include if feature involves data)*

- **Coin Reference Map**: One row per coin — stable id, symbol, name, slug, active/inactive status, source.
- **Daily Quote**: One row per coin per day — date, coin id/symbol, price (USD), market cap (USD), volume (USD).
- **Exchange Rate**: One row per currency per day — date, currency, USD rate, source, provenance date.
- **Global Metric**: One row per day — date, total market cap (USD), total volume (USD), dominance figures, per-market breakdown.
- **Market Index**: One row per day — date, index identifier, index value.
- **Dataset Manifest**: Per dataset — source, date coverage, row/symbol counts, run metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All four new datasets (map, exchange rates, global metrics, index) exist, each with a manifest and retained raw samples.
- **SC-002**: Daily quotes coverage extends beyond 36 months toward full available history, and at least one coin delisted more than 36 months ago has daily records in its active period.
- **SC-003**: The coin reference map resolves 100% of coins referenced by the quotes and listings datasets to a stable id, including delisted coins.
- **SC-004**: Running the monthly maintenance job twice for the same month results in zero duplicated rows and zero changed existing rows.
- **SC-005**: A non-technical user can open every new dataset as CSV using the single documented converter command, with columns, USD units, and date format documented.
- **SC-006**: 100% of monetary fields across all datasets are expressed in USD, and 100% of date fields use `YYYY-MM-DD`.

## Assumptions

- The paid CoinMarketCap plan (Builder tier) is being upgraded and will unlock full historical quotes and the additional data domains; until then, extension degrades to the available range and records achieved coverage (FR-013).
- The keyless `cmc_web_pit` source remains the survivorship-free authority for historical listings.
- Daily granularity is sufficient beyond three years; sub-daily/intraday data is explicitly out of scope.
- No UI/frontend and no trading logic are in scope; deliverables are datasets, manifests, docs, and the maintenance job.
- The primary consumer is a non-technical researcher using SAS/Excel; usability is judged from that user's perspective.
- Existing extraction code, the manifest convention, and the Parquet→CSV converter are reused and extended rather than replaced.
