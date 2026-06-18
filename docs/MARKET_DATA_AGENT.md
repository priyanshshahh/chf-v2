# Market Data Agent — Complete Reference

The Market Data Agent (`agents/market_data_agent.py`) is the **second node** of the CHF
pipeline. It ingests daily OHLCV for the universe's assets (exchange-first with a keyless
free-provider fallback), normalizes to a clean daily UTC grid, QA-gates, attaches the
point-in-time universe membership mask, and writes the canonical market table that every
downstream stage (features → labels → model → portfolio → backtest) consumes.

This is the **single exhaustive reference** for the agent: its full output contract, the
research-integrity guards, the complete lifecycle, every fix made during the 8-phase
overhaul, the full limitation register, the complete config surface, the verifiers, the
full test inventory, and how to run the survivorship-free path.

> **Status:** the 8-phase overhaul plus a Phase 9 hardening pass are complete. All 16
> limitation-register items (A–P) are closed and the residual gaps found in a follow-up audit
> are addressed. ~117 market-related tests pass, default `verify_market_run.py` is **PASS**,
> and every behavior change is config-gated to legacy defaults (additive columns / partitioned
> writes / MLflow are safe-on). The on-disk schema is **29 `CANONICAL_COLUMNS`**.

---

## 1. Output contract

Written to `data/raw/market/`:

| Artifact | Contents |
|---|---|
| `market_ohlcv.parquet` | **Canonical** flat OHLCV panel — the file every downstream stage and the verifier read |
| `by_symbol/<SYM>_ohlcv.parquet` | Per-symbol copies |
| `partitioned/symbol=…/year=…/*.parquet` | Hive-partitioned copy (Phase 6, additive) for filter-pushdown reads |
| `market_coverage_report.parquet` | Per-asset QA: fetched/passed_qa, source, row_count, date range, gaps, fills, provider attempts + failure reasons |
| `market_manifest.json` | Full provenance (run_id, snapshot_id, data_content_hash, as_of_date, coverage ratios, exchanges/fallbacks used, API/cache counts, anomaly totals, warnings, limitations) |
| `data_quality_daily.md` | Human-readable QA tear-sheet |

### `CANONICAL_COLUMNS` (27 — the exact on-disk schema)

```
date_ts, symbol, cmc_id, exchange, exchange_symbol,
open, high, low, close, volume, market_cap,
source, snapshot_id, fetched_at_utc,
is_forward_filled, is_incomplete_dropped, data_type, is_full_ohlcv, quote_currency,
is_universe_member, market_cap_rank,        # Phase 1
dollar_volume_usd, volume_basis,            # Phase 2
is_synthetic_ohlc,                          # Phase 3
is_price_anomaly,                           # Phase 5
price_basis,                                # Phase 8 (E)
has_long_gap,                               # Phase 8 (F)
volume_scope, is_stale_price                # Phase 9 (#7, #9)
```

The 8 trailing columns were **added** by the overhaul. They are NA/False-safe when their
gating flag is off, so the legacy schema is a strict subset — existing consumers and
verifiers are unaffected (the verifier uses `.issubset()` checks).

**New-column meanings:**

| Column | Type | Meaning |
|---|---|---|
| `is_universe_member` | bool | True only on days the asset was a real top-N universe member (from the daily mask) |
| `market_cap_rank` | int | Universe market-cap rank on that date (from the mask) |
| `dollar_volume_usd` | float | Canonical USD dollar-volume, unit-correct per source — use this, not raw `volume`, for cross-asset liquidity |
| `volume_basis` | str | `base` (exchange/CCXT base units), `quote_usd` (aggregator USD), or `none` (close-only) |
| `is_synthetic_ohlc` | bool | True on forward-filled gap days where O/H/L are carried-forward fabrications (close is a real mark-to-market carry) |
| `is_price_anomaly` | bool | True on spike-and-revert bars (bad print); sustained large moves are NOT flagged |
| `price_basis` | str | `venue_close` (single exchange), `composite_index` (aggregator), or `unknown` |
| `has_long_gap` | bool | True when a >max-gap discontinuity occurred and `segment_and_flag` kept only the most-recent contiguous segment |

---

## 2. Research-integrity guards (do not violate)

- **Binance is forbidden in four layers** — `prepare()` (config check), `_load_universe_requests`
  / `_load_union_requests` (per-row route check), `_qa_failure` (output check), and
  `scripts/verify_market_run.py`. `_ordered_exchange_candidates` also silently skips any
  exchange literally named `binance`. **USDT quoting is rejected**; quotes are USD/USDC.
  Rationale: geo-portability and reproducibility.
- **No fabricated OHLC on partial (close-only) sources.** CoinGecko/CoinCap are flagged
  `is_full_ohlcv=false`; `_qa_failure` rejects any partial row carrying O/H/L
  (`fake_ohlc_on_partial_data`).
- **Synthetic forward-filled bars are flagged, never silently real** (`is_synthetic_ohlc`),
  and excluded from range/ATR features.
- **Positivity / OHLC / duplicate invariants are enforced three times**: in
  `_normalize_asset_frame`, again in `persist` (refuses to write bad rows), and again in
  the verifier.

---

## 3. Provider stack & fetch waterfall

Per asset, `_fetch_asset` tries sources in order:

1. **Exchange-first (CCXT, full OHLC)** — `_ordered_exchange_candidates` = the
   universe-assigned exchange, then `exchange_priority`: **Coinbase → Kraken → KuCoin →
   Gemini** (Binance excluded). Symbol resolution prefers USD then USDC (Kraken `BTC→XBT`
   alias); USDT only if `allow_usdt_fallback`.
2. **Keyless fallback waterfall** — `MarketFallbackProvider`, priority **CryptoCompare →
   CoinGecko → CoinCap → CoinPaprika**. CryptoCompare/CoinPaprika are full-OHLC; CoinGecko
   is close+volume; CoinCap is close-only.
3. **Second fallback** — if exchange data normalizes to empty (QA-killed) and wasn't a
   fallback, the waterfall is retried.

CMC mode (`use_cmc_ohlcv`, the `market_data_cmc_3y` section) uses `_fetch_asset_cmc`
(`fetch_ohlcv_historical` by `cmc_id`) with the same fallback.

Pagination (`providers/ccxt_market.py`): forward-paginate from `since`, `max_pages=20`,
`max_rows=3000`, `until` filter applied in-memory. Non-retry markers (401/402/403/404/451,
geo-block, DNS) → `ProviderUnavailableError`; rate limits → `RateLimitError`.

---

## 4. Lifecycle (`prepare → run → persist`)

**`prepare()`** — make dirs; init CMC provider if `use_cmc_ohlcv`; Binance/USDT config
guard; `require_pit_membership` guard (fail if union mode but mask missing);
`_load_universe_requests()` → asset list (must be non-empty).

**`run()`** — compute the window from `as_of_date` (Phase 4); set a window-keyed
`snapshot_id`; loop assets → `_fetch_asset` → collect frames + coverage rows; attach the
membership mask; compute `full_ohlcv_assets`; build `_fatal_errors`.

**`persist()`** — re-validate (non-positive close / invalid OHLC → raise); write flat
parquet + `by_symbol/` + Hive `partitioned/`; write coverage, manifest (incl.
`data_content_hash`, `as_of_date`, `price_anomalies_total`), and the QA md; log the run to
MLflow (non-fatal).

### Normalization pipeline (`_normalize_asset_frame`) — exact order

1. UTC-normalize `date_ts`; numeric-coerce O/H/L/C/V; `inf→NA`.
2. Full-OHLCV: drop rows missing any OHLC, require all `>0`. Partial: drop missing close, `close>0`, null O/H/L.
3. Sort; `drop_duplicates(date_ts, keep=last)`.
4. **`drop_incomplete_current_day`** — drop `date_ts >= as_of_date` (Phase 4: as-of, not wall-clock).
5. **Forward-fill** to a complete daily grid:
   - **Gap guard**: if the longest missing run `> max_forward_fill_gap_days` (3): with
     `long_gap_policy: reject_asset` (default) the whole asset is rejected; with
     `segment_and_flag` (Phase 8 F) only the most-recent contiguous segment is kept and
     `has_long_gap=True` (`_largest_segment_after_long_gap`).
   - ffill `close`; on filled full-OHLCV days synthesize O=H=L=close, volume→0, set
     `is_forward_filled=True` and `is_synthetic_ohlc=True` (Phase 3).
6. **`min_history_days`** (365): reject if shorter. With `min_history_days_policy:
   membership_aware` (Phase 8 G) the floor drops to `min_history_days_floor` (90) so
   genuinely short-lived members survive.
7. **Price-anomaly flagging** (`_flag_price_anomalies`, Phase 5) — `is_price_anomaly` on
   spike-and-revert; `anomaly_policy` flag_only/drop/winsorize.
8. Stamp identity, `dollar_volume_usd`/`volume_basis` (Phase 2), `price_basis` (Phase 8 E),
   reorder to `CANONICAL_COLUMNS`, run `_qa_failure`.

### QA gate (`_qa_failure`) returns the first failing reason

`empty_after_normalization`, `null_date_ts`, `non_positive_close`, `missing_full_ohlc`,
`non_positive_ohlc`, `high_below_low`, `fake_ohlc_on_partial_data`, `negative_volume`,
`duplicate_symbol_date`, and the Binance/USDT/source checks.

### Fatal errors (`_fatal_errors`)

`fetched_assets==0`; empty output/coverage; no provider attempts; `full_ohlcv_assets <
minimum_assets_required` (50); `failed_assets > maximum_failed_assets_allowed`.

---

## 5. The 8-phase overhaul — everything we did

### Phase 1 — survivorship-free PIT universe reaches the model (limitations A, B)

**The bug:** the UniverseAgent produces a survivorship-free monthly membership (66 snapshots,
2021→2026, 324 unique `cmc_id`s, dead coins like FTT/LUNA/CEL retained in their live months),
but **two stages silently collapsed it to the single latest snapshot** —
`MarketDataAgent._load_universe_requests` (`df["snapshot_date"].max()`) and
`FeatureAgent._load_allowed_symbols` (line ~184). So the model trained/backtested on **today's
survivors**; the survivorship-free universe was discarded at the first consumer.

**The fix (gated):**
1. **`scripts/build_membership_daily.py`** expands monthly → daily PIT mask
   `universe_membership_daily.parquet`. Each month-start's membership is forward-held to the
   next month-start; membership on date *d* derives only from a snapshot `snapshot_date <= d`
   (**no look-ahead** — verified 0 violations on the real build: 200,700 rows, 100 members/day,
   2007 days, dead coins bounded e.g. CEL→2022-10, LUNA→2024-04, FTT→2025-04).
2. **`universe_membership_mode: union_full_history`** (`_load_union_requests`) — one request per
   stable `cmc_id` across **all** snapshots (every coin ever a member, incl. delisted), routing
   from its most recent eligible snapshot.
3. **`attach_membership_mask: true`** (`_attach_membership_mask`) — left-join the daily mask on
   `(cmc_id, date_ts)` (symbol fallback); adds `is_universe_member`, fills `market_cap_rank`
   (and `market_cap` value — Phase 8 J). Non-member rows are **retained** for feature warmup.
4. **FeatureAgent `membership_mode: pit_daily`** (`_load_pit_membership` +
   `_apply_membership_filter`) — build features over the full union panel (correct rolling
   windows), then emit only true per-day member rows.
5. **`require_pit_membership: true`** — hard-fail if union mode is on but the mask is missing.

### Phase 2 — canonical USD dollar-volume (limitation D)

Exchange (CCXT) candle volume is **base-asset units**; aggregator sources report **USD**.
`dollar_volume = volume*close` double-counts price for USD sources. Fix: `volume_basis_for_source`
tags each row (`base`/`quote_usd`/`none`) and `dollar_volume_usd` is computed correctly
(`volume*close` for base; `volume` for USD; NA for none). `FeatureAgent._build_market_features`
auto-upgrades to `dollar_volume_usd` when present (falls back to `close*volume` for legacy files).

### Phase 3 — synthetic-OHLC integrity (limitation C)

Forward-filled gap bars (O=H=L=close) are flagged `is_synthetic_ohlc=True`; FeatureAgent's
range features (`hl_range_pct`, `atr_proxy_14d`) null H/L on synthetic rows so no fake
zero-range data feeds volatility/range features. Close (mark-to-market carry) is retained.

### Phase 4 — determinism & robustness (limitations H, K, L, O)

- **`as_of_date`** (`_as_of_date`) replaces wall-clock `today` for the data window and the
  incomplete-current-day cutoff → reproducible across calendar days.
- **`data_content_hash`** (`_content_hash`) — SHA-256 of sorted `(symbol, date_ts, close)` in
  the manifest, order-independent (mirrors UniverseAgent).
- **window-keyed `snapshot_id`** — `market:{snap}:{start}:{end}`.
- **bounded provider cooldown** — `provider_cooldown_seconds` (60) replaces the run-global
  blacklist for `RateLimitError`; `ProviderUnavailableError` (geo/DNS) stays permanent. (A
  fast test still sees a single call within the window; a long run retries after cooldown.)
- **CCXT cache key** stabilizes transitively (`requested_start` derives from pinned `as_of_date`).

### Phase 5 — price-anomaly guard (limitation I)

`_flag_price_anomalies`: `is_price_anomaly=True` only when the move **into** and **out of** a
bar both exceed `max_abs_daily_log_return` (default ln(5) ≈ +400%/day) **with opposite sign**
(spike-and-revert). A sustained 10× move is **not** flagged. `anomaly_policy`:
`flag_only` (default, non-destructive) / `drop` / `winsorize` (neutralize to prior close).
Manifest reports `price_anomalies_total` + `anomaly_policy`; verifier warns if >1% flagged.

### Phase 6 — storage & access layer (limitations M, N)

- **Hive partitioning** — `_write_partitioned_market` writes
  `partitioned/symbol=…/year=…/*.parquet` via DuckDB `COPY … PARTITION_BY (symbol, year)`,
  **additive** to the flat file. Yearly depth (small-file guard); deterministic rewrite;
  fully defensive (a DuckDB failure warns, never fails persist). Gated by `write_partitioned_market` (default on).
- **DuckDB views** — `DuckDBEngine.create_market_views()` registers `v_market_ohlcv` (flat),
  `v_market_ohlcv_partitioned` (pushdown via `hive_partitioning=true`), and `v_market_members`
  (PIT member rows only). Degrades gracefully if files/columns are absent.

### Phase 7 — MLflow wiring (limitation P)

`_log_to_mlflow` (called at the end of `persist`) logs tags (`agent`, `run_id`, `snapshot_id`,
`data_content_hash`), params (`as_of_date`, window, `min_history_days`, `anomaly_policy`, …),
metrics (`fetched_assets`, `full_ohlcv_assets`, coverage ratios, `price_anomalies_total`), and
attaches `market_manifest.json` + `data_quality_daily.md` as artifacts, backed by `./mlruns`.
Gated by `mlflow.log_market_run` (default on); **fully non-fatal** if MLflow is absent or errors.

### Phase 8 — remaining register items + one-command PIT path (E, F, G, J)

- **E** — `price_basis_for_source` tags `venue_close`/`composite_index`/`unknown`; verifier
  warns if an asset mixes bases within its series.
- **F** — `long_gap_policy: segment_and_flag` keeps the most-recent contiguous segment
  (`_largest_segment_after_long_gap`) + `has_long_gap`, instead of dropping the whole asset.
- **G** — `min_history_days_policy: membership_aware` lowers the floor to `min_history_days_floor`
  (90) so short-lived dead coins survive.
- **J** — `_attach_membership_mask` now fills the `market_cap` **value** (not just rank) from the
  daily mask.
- **One-command PIT path** — new `python main.py membership` subcommand; `PipelineRunner.run_universe`
  auto-builds the daily mask after the universe stage (`build_membership_daily`). Also fixed a
  stale `tests/test_cli_commands.py` test (it patched the removed two-stage `cmd_features`).

### Phase 9 — hardening (residual-gap audit follow-up)

A self-audit after Phase 8 surfaced residual gaps; Phase 9 closes the high-value ones (all
gated/additive, every change *reduces* fake data):

- **#1 content hash over the full panel** — `_content_hash` now covers
  `(symbol, date_ts, open, high, low, close, volume)`, so a change in any OHLC field or volume
  changes the fingerprint (was close-only).
- **#2 anomaly catches moderate bad prints** — `_flag_price_anomalies` adds a round-trip-to-origin
  test: a spike beyond `anomaly_secondary_log_return` (ln 2.5) whose next close returns within
  `anomaly_roundtrip_tolerance` (15%) of the pre-spike close is flagged. Sustained moves still
  survive (no return-to-origin).
- **#3 winsorized bars are flagged synthetic** — under `anomaly_policy: winsorize` the neutralized
  bar is marked `is_synthetic_ohlc=True`, so range/ATR features exclude it (no unflagged fake bar).
- **#4 collision-safe + vectorized membership join** — `_attach_membership_mask` now uses a
  vectorized merge (no per-row Python loop) and the **symbol fallback excludes ambiguous tickers**
  (a symbol mapping to >1 `cmc_id` is never credited by symbol), eliminating cross-coin
  mis-attribution. Dead `has_cmc` variable removed.
- **#7 `volume_scope`** — tags `single_venue` vs `global` volume (`volume_scope_for_source`).
- **#9 `is_stale_price`** — flags genuinely frozen feeds (runs of an identical REAL close longer
  than `max_flat_close_days`; forward-filled synthetic flats excluded). Non-destructive diagnostic.
- **PIT-mode coverage probe** — `scripts/probe_pit_coverage.py` reports, per union coin (incl.
  dead), whether usable data is already on disk (`ingested_full` / `cache_only` / `no_local_data`),
  writing `data/readiness/pit_coverage.{json,md}`. On the real universe it reports **324 union
  coins: 80 ingested-full, 14 cache-only, 230 no-local-data** — quantifying the dead-coin coverage
  risk *before* the multi-hour live ingest. Honest disk inspection only; never fabricates.
- **Wider keyless exchanges** — `market_data_pit` adds `bitstamp, bitfinex` to the exchange
  waterfall (all non-Binance, USD/USDC) to maximize dead-coin coverage.

---

## 6. Limitation register — 16 of 16 closed

| # | Limitation | Status |
|---|---|---|
| A | Survivorship collapse (latest-snapshot only) | **Fixed (Phase 1)** |
| B | No time-varying membership re-join (Feature collapse) | **Fixed (Phase 1)** |
| C | Forward-fill fabricates synthetic OHLC marked full-OHLCV | **Fixed (Phase 3)** |
| D | Volume units inconsistent (base vs USD) | **Fixed (Phase 2)** |
| E | Price-definition heterogeneity (venue vs composite) | **Fixed (Phase 8)** |
| F | >3-day gap drops the whole asset | **Fixed (Phase 8)** |
| G | `min_history_days=365` excludes short-lived coins | **Fixed (Phase 8)** |
| H | Run-global provider blacklist → non-determinism | **Fixed (Phase 4)** |
| I | No price-outlier / anomaly guard | **Fixed (Phase 5)** |
| J | `market_cap` NA in non-CMC mode | **Fixed (Phase 8)** |
| K | Wall-clock window; `snapshot_id` not content-hashed | **Fixed (Phase 4)** |
| L | CCXT cache key shifts daily | **Fixed (Phase 4)** |
| M | No Hive partitioning | **Fixed (Phase 6)** |
| N | No DuckDB views | **Fixed (Phase 6)** |
| O | No content hash on market output | **Fixed (Phase 4)** |
| P | MLflow declared but unwired | **Fixed (Phase 7)** |

---

## 7. Complete config surface (`configs/run_config.yaml → market_data`)

**Legacy keys (unchanged behavior):**
```yaml
research_mode: true          live_api_enabled: true       use_fixtures: false
max_assets: null             quote_currency: "USD"        allow_usdt_fallback: false
exchange_priority: [coinbase, kraken, kucoin, gemini]
fallback_provider_priority: [cryptocompare, coingecko, coincap, coinpaprika]
timeframe: "1d"              backfill_days: 2000          min_history_days: 365
minimum_assets_required: 50  maximum_failed_assets_allowed: 50
cache_enabled: true          force_refresh: false         cache_dir: "data/cache/market"
request_timeout_seconds: 30  per_asset_timeout_seconds: 180
max_pages_per_asset: 20      max_rows_per_asset: 3000
min_seconds_between_requests: 1.5   max_retries: 5
backoff_base_seconds: 3      backoff_jitter_seconds: 1.5
drop_incomplete_current_day: true   forward_fill_missing_days: true
set_filled_volume_to_zero: true
log_each_asset: true         log_each_provider_attempt: true
fail_on_low_coverage: true   fail_on_empty_output: true
fail_on_binance_usage: true  fail_on_demo_data: true
```

**New keys (all gated; defaults preserve legacy behavior):**
```yaml
# Phase 1 — PIT membership
universe_membership_mode: latest_snapshot     # | union_full_history
attach_membership_mask: false
membership_daily_path: "data/raw/universe/universe_membership_daily.parquet"
require_pit_membership: false
# Phase 4 — determinism
as_of_date: null                              # pin e.g. "2026-03-24"
provider_cooldown_seconds: 60
# Phase 5 / 9 — anomaly guard
max_abs_daily_log_return: 1.6094379124341003  # ln(5); both-legs-huge round trip
anomaly_secondary_log_return: 0.9162907318741551  # ln(2.5); round-trip-to-origin leg (Phase 9)
anomaly_roundtrip_tolerance: 0.15             # next close within 15% of pre-spike close (Phase 9)
anomaly_policy: flag_only                     # | drop | winsorize (winsorized → marked synthetic)
# Phase 9 — stale-price detection
max_flat_close_days: 10                       # identical-real-close run beyond this → is_stale_price
# Phase 6 — storage
write_partitioned_market: true
# Phase 8 — gap & history
long_gap_policy: reject_asset                 # | segment_and_flag
min_history_days_policy: absolute             # | membership_aware
min_history_days_floor: 90
```

**`mlflow` section (Phase 7):** `tracking_uri: mlruns`, `experiment_name: CHF_experiments`,
`log_artifacts: true`, `log_market_run: true`.

**Ready-to-run sections:**
- `market_data_pit` — `universe_membership_mode: union_full_history`, `attach_membership_mask: true`,
  `require_pit_membership: true`, `long_gap_policy: segment_and_flag`,
  `min_history_days_policy: membership_aware`, `min_history_days_floor: 90`.
- `features_pit` — `membership_mode: pit_daily`, `require_pit_membership: true`.

---

## 8. Verifiers

`scripts/verify_market_run.py` — schema (`.issubset`), null/non-positive close, full-OHLC
validity, `high<low`, fake-OHLC-on-partial, negative volume, dup `(symbol,date_ts)`,
Binance/USDT/source bans, `minimum_assets_required`; **gated additions**: `is_universe_member`
present/non-null when mask attached; panel spans >1 month in union mode; `volume_basis` values
valid + `dollar_volume_usd ≥ 0`; synthetic rows are forward-filled; anomaly-density warning;
mixed-`price_basis` warning; `data_content_hash` present (warning if legacy manifest).

`scripts/verify_feature_run.py` — gated: when `membership_mode=pit_daily`, asserts
`full_features` spans >1 month.

All gated checks are dormant in legacy mode, so default runs validate **PASS**.

---

## 9. Test inventory (9 new files — 46 new test functions; some parametrized)

| File | Functions | Covers |
|---|---:|---|
| `tests/test_membership_daily.py` | 7 | builder: no look-ahead, forward-hold continuity, survivorship (dead coin bounded), uniqueness, end-date isolation, manifest, empty-input |
| `tests/test_market_data_pit_membership.py` | 4 | mask flags members/non-members, off-by-default (NA), feature PIT filter, no-op in legacy mode |
| `tests/test_market_data_dollar_volume.py` | 5 | `volume_basis` mapping (parametrized), base vs USD correctness, no double-count, normalize emits columns |
| `tests/test_market_data_synthetic_ohlc.py` | 3 | synthetic flagged + close carried, no-synthetic-when-clean, feature range excludes synthetic |
| `tests/test_market_data_determinism.py` | 7 | as-of pinning, hash determinism/order-independence/sensitivity/empty, bounded-cooldown expiry, geo-block permanence |
| `tests/test_market_data_anomaly.py` | 5 | spike-and-revert flagged, sustained move NOT flagged, clean data clean, drop + winsorize policies |
| `tests/test_market_data_partitioning.py` | 5 | Hive tree, round-trip row-count, empty→None, view creation + member filter, no-op without file |
| `tests/test_market_data_mlflow.py` | 3 | logs run + artifacts, disabled no-op, failure non-fatal |
| `tests/test_market_data_phase8.py` | 7 | price_basis mapping + stamping, segment helper + segment_and_flag, membership-aware floor, market_cap value fill |
| `tests/test_market_data_phase9.py` | 8 | full-panel content hash, moderate round-trip anomaly, winsorize→synthetic, volume_scope, stale-price, collision-safe membership |
| `tests/test_pit_coverage_probe.py` | 2 | probe classifies ingested_full/cache_only/no_local_data; writes JSON+MD reports |

Plus `tests/test_market_data_agent_research_mode.py` (46) and the feature suite — **all still
pass** (the research-mode helper disables MLflow for test hygiene).

---

## 10. Key methods reference (`agents/market_data_agent.py`)

`prepare` · `run` · `persist` · `_load_universe_requests` · `_load_union_requests` (Phase 1) ·
`_attach_membership_mask` (1, J) · `_membership_daily_path` · `_as_of_date` (4) · `_content_hash`
(4) · `_write_partitioned_market` (6) · `_flag_price_anomalies` (5) · `_ordered_exchange_candidates`
· `_fetch_asset` / `_fetch_asset_cmc` · `_normalize_asset_frame` · `_qa_failure` · `_coverage_row`
· `_fatal_errors` · `_log_to_mlflow` (7). Module-level: `volume_basis_for_source` (2),
`price_basis_for_source` (8 E), `_largest_segment_after_long_gap` (8 F), `VOLUME_BASIS_BY_SOURCE`.

Provenance: every run writes the AgentBase SQLite registry (`metadata/agent_registry.db`) and,
when enabled, an MLflow run under `./mlruns`.

---

## 11. Running the survivorship-free path (one command per stage)

```bash
python main.py universe                       # survivorship-free monthly universe
python main.py membership                     # daily PIT mask (also auto-built by run_full_pipeline)
python main.py market   --section market_data_pit   # union ingest incl. dead coins (multi-hour, networked)
python scripts/verify_market_run.py --section market_data_pit
python main.py features --section features_pit
python scripts/verify_feature_run.py
python main.py labels && python main.py models && python main.py portfolio && python main.py backtest
```

> The verifier asserts the PIT panel spans >1 month (guards against silent re-collapse).

---

## 12. What still requires a human (the payoff)

None of this produces a **new `alpha_verified` number** until the survivorship-free path is run
on a machine with exchange network access — the `market_data_pit` ingest fetches ~324 coins ×
~5 years (including dead coins), a multi-hour job that cannot run in a sandbox. After it runs,
re-run labels → models → portfolio → backtest, then **re-freeze and disclose the new headline
result honestly**. That run is the experiment this entire overhaul was built to make possible.
