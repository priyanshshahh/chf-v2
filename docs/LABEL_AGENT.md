# Label Agent — Complete Reference

The Label Agent (`agents/label_agent.py::LabelAgent`) is the **target-generation node** of the
CHF pipeline. It consumes the canonical market panel and the feature store, computes **exact
forward calendar log-returns** at multiple horizons, aligns those targets back to the
feature-date the model will see (`date_t`), and emits the leakage-safe `modeling_dataset.parquet`
that the model stage trains on. It is the single place where labels are created, so it is also
the single place where the leakage boundary between "features as of *t*" and "return realized
*after t*" is enforced.

The headline research result of the whole pipeline is a **deliberate negative**
(`alpha_verified=false` for every tested candidate). The Label Agent's job is to keep that result
honest: if the targets leaked, a false positive would be trivial to manufacture. Every guard
below exists to make that impossible by construction.

> **Status:** the agent is leakage-safe by construction, fully verifier-gated
> (`scripts/verify_label_run.py`), and covered by the research-integrity test suite
> `tests/test_label_agent_research_mode.py` (40 test functions). The canonical horizons are
> **`[7, 14, 30]` days** and the label type is **`forward_log_return`**.

---

## 1. Output contract

Written to `data/labels/` (`labels.output_dir`):

| Artifact | Contents |
|---|---|
| `labels_{h}d.parquet` (one per horizon: 7, 14, 30) | **Long-format** per-(symbol, date) target table for horizon `h` |
| `label_matrix.parquet` | **Wide** table — one row per (symbol, date), all horizons' labels as columns, inner-joined so every row is complete across all horizons |
| `modeling_dataset.parquet` | **The model's input.** Inner join of the (pruned) feature store with `label_matrix` on `(date_ts, symbol)` |
| `modeling_dataset_unpruned.parquet` | Optional. Same join using the **unpruned** feature store (`also_write_unpruned_modeling_dataset`) |
| `label_coverage_report.parquet` | Per-horizon QA: candidate rows, valid rows, drop reasons, label distribution stats, `passed_qa`, `failure_reason` |
| `label_manifest.json` | Full provenance: run_id, snapshot_id, input file hashes, config hash, row/symbol counts, formula string, `recommended_embargo_days`, `purge_train_test_overlap_days` |
| `data_quality_labels.md` | Human-readable QA tear-sheet with PASS/FAIL status |
| `partitioned/horizon={h}/year=…/month=…/part.parquet` | Hive-partitioned copy of each horizon (`output_partitioned`, default on) |

### `labels_{h}d.parquet` columns (long format)

Built in `_compute_horizon_labels`. Verified-required columns (per `verify_label_run.py`):

```
date_ts, symbol, horizon_days, future_date_ts,
close_t, close_t_plus_h,
label_fwd_logret, label_simple_return, label_direction,
label_rank_pct, label_quantile_bucket,
is_complete, snapshot_id, run_id, created_at_utc
```

Also written: `label_value` (alias = `label_fwd_logret`), `label_type` (= `forward_log_return`).

| Column | Meaning |
|---|---|
| `date_ts` | The feature "as-of" date *t* — the date a model row is keyed on |
| `future_date_ts` | The realized date *t+h* whose close defines the target |
| `close_t` / `close_t_plus_h` | Anchor close at *t* and the realized close at *t+h* |
| `label_fwd_logret` | **Primary target:** `ln(close(t+h) / close(t))` |
| `label_simple_return` | `close(t+h)/close(t) - 1` |
| `label_direction` | `Int64` 0/1 — `(label_fwd_logret > 0)` |
| `label_rank_pct` | Cross-sectional percentile rank of `label_fwd_logret` **within each `date_ts`** (`rank_method`, `rank_pct`) |
| `label_quantile_bucket` | `Int64` 1..`quantile_buckets` (default 5) bucket of the cross-sectional rank within each date |
| `is_complete` | True when the exact forward close exists and is positive (see §3) |

### `label_matrix.parquet` columns (wide format)

Built in `_build_label_matrix` by renaming each horizon's columns with a `_{h}d` suffix and
**inner-joining** across horizons on `(date_ts, symbol)`. For horizons `[7, 14, 30]`:

```
date_ts, symbol,
label_fwd_logret_7d,  label_fwd_logret_14d,  label_fwd_logret_30d,
label_simple_return_7d, label_simple_return_14d, label_simple_return_30d,
label_direction_7d, label_direction_14d, label_direction_30d,
label_rank_pct_7d, label_rank_pct_14d, label_rank_pct_30d,
label_quantile_bucket_7d, label_quantile_bucket_14d, label_quantile_bucket_30d,
max_horizon_complete, snapshot_id, run_id, created_at_utc
```

The inner join means a row survives only if **all** horizons have a valid label, so a 30-day
target near the end of the sample (whose *t+30* close does not yet exist) removes that row from
the matrix entirely. `max_horizon_complete` is therefore always `True` in the written file. A
post-build assertion rejects any null in the horizon label columns.

### `modeling_dataset.parquet` columns (the model input)

Built in `_build_modeling_dataset`:

- Feature source: the **pruned** feature store (`full_features_pruned.parquet`) when present and
  `use_pruned_features_for_modeling_dataset` is true; otherwise `full_features.parquet`.
- Join: `feature_df.merge(label_matrix, on=["date_ts", "symbol"], how="inner")` — keyed on
  symbol + feature date only. There is no time-shifted or fuzzy join; the label was already
  shifted to *t* upstream.
- Rows are dropped where any required horizon label (`label_fwd_logret_{h}d`) is null.
- Column order: `date_ts, symbol, snapshot_id, run_id, created_at_utc`, then all feature
  columns, then all `label_*` columns plus `max_horizon_complete`.
- The verifier asserts the dataset contains both feature columns **and** every `label_*` column
  present in the matrix, and that `len(modeling_dataset) == len(label_matrix)` (the join is
  lossless against the matrix key set when `fail_on_feature_label_misalignment` is on).

---

## 2. Research-integrity guards (do not violate)

- **Labels are EXACT forward calendar returns — never approximate.** For each symbol the agent
  shifts the *sorted daily* series by exactly `horizon` rows (`close.shift(-horizon)`,
  `date_ts.shift(-horizon)`) **and then requires the shifted date to equal the calendar date
  `date_ts + Timedelta(days=horizon)`** (`is_exact_horizon`). If a gap in the daily grid means
  the row `horizon` positions ahead is not exactly `h` calendar days later, the row is rejected
  (`dropped_non_exact_horizon_rows`), never silently mislabeled as an `h`-day return. This is the
  core guard: a positional shift alone would fabricate a wrong-horizon return across any missing
  day. (Tested: `test_drops_non_exact_calendar_horizon_rows`.)

- **The forward-return horizon is the leakage boundary.** The target for feature-date *t* is
  defined by a close at *t+h*, which is information from the **future** relative to *t*. The agent
  attaches that future return to the *t* row only as the supervision signal; no future value is
  exposed as a feature. `future_date_ts > date_ts` is asserted in the verifier for every row.

- **Embargo / purge recommendation is emitted, not assumed.** The manifest writes
  `recommended_embargo_days` and `purge_train_test_overlap_days`, both defaulting to the **max
  horizon** (30). Because a label at *t* depends on data through *t+30*, any train/test split must
  purge and embargo by at least 30 days or the test-set labels overlap the train window. The
  verifier hard-asserts `recommended_embargo_days == max(horizons)`. The downstream model stage
  consumes this; the Label Agent's contract is to surface the correct minimum.

- **Feature inputs are scanned for prohibited target/leakage column names before any join.**
  `_find_prohibited_columns` rejects columns whose names contain
  `target, label, future, forward, fwd, lead, next_return, ret_fwd` or start with `y_`
  (`LABEL_LEAKAGE_TOKENS`), excluding known-safe exact names (`ALLOWED_PROHIBITED_EXACT`, e.g.
  `is_forward_filled_market`) and label-metadata columns. Triggered in `prepare()` on the feature
  and pruned-feature inputs, and again in `_build_modeling_dataset` before the merge. Gated by
  `fail_on_target_leakage` (default on). (Tested: `test_no_label_columns_in_feature_inputs`,
  `test_verify_label_run_rejects_feature_leakage_columns`.)

- **The merge is the only path that joins labels to features.** Labels are computed on the market
  panel, the feature store is validated to contain **no** label columns, and the two meet exactly
  once via an inner join on `(date_ts, symbol)`. The verifier additionally fails if any
  `label_*` column was written back into `data/features/` files
  (`test_no_labels_written_to_data_features_dir`).

- **Prices are never clipped to force a valid label.** Non-positive / non-finite closes are
  rejected (`fail_on_non_positive_prices`), not floored. A `0.0` close in the input raises in
  `prepare()`; a negative close fails the run. `inf`/`-inf` log-returns are converted to NA and
  dropped, and a final assertion refuses to write null/infinite labels
  (`fail_on_null_labels`, `fail_on_infinite_labels`). (Tested:
  `test_does_not_clip_non_positive_prices`, `test_fails_on_non_positive_prices`.)

- **Cross-sectional ranks/buckets are computed strictly within a single `date_ts`** (groupby
  `date_ts`), so no information crosses dates and a sparse date never borrows rank context from a
  denser one. Dates with fewer than `min_assets_per_label_date` (default 20) assets are dropped
  to keep cross-sectional statistics meaningful.

- **Snapshot consistency.** `prepare()` cross-checks that the market/feature parquet
  `snapshot_id`s match their respective manifests, refusing mismatched inputs.

---

## 3. Lifecycle (`prepare → run → persist`)

The base `AgentBase.execute()` wraps these three with retries, status tracking, registry writes,
and snapshot-id generation — the subclass only implements the three methods.

**`prepare()`** — resolve `output_dir`; require the four mandatory inputs to exist
(`market_ohlcv.parquet`, `full_features.parquet`, `market_manifest.json`,
`feature_manifest.json`) — missing → `FileNotFoundError`; optionally load
`full_features_pruned.parquet`. Validate required columns; UTC-normalize `date_ts`
(`features.feature_engineering.ensure_utc`); sort; reject duplicate `(symbol, date_ts)`. Coerce
`close` numeric and reject non-positive prices. Scan feature columns for prohibited leakage
tokens. Verify `snapshot_id` against the manifests. Select the symbol universe from the
**feature** store (optionally capped by `max_symbols`), and filter market + feature frames to it
(must be non-empty). The agent deliberately reads only the **canonical** flat market file — the
old per-symbol `SYM_ohlcv.parquet` pattern is rejected
(`test_label_agent_does_not_use_old_per_symbol_market_pattern`).

**`run()`** — generate the snapshot id keyed on `labels:{horizons}`; for each horizon call
`_compute_horizon_labels` (the exact-forward-return + exactness-guard + per-date rank/bucket +
coverage logic); build the wide `label_matrix`; build `modeling_dataset` (pruned) and optionally
`modeling_dataset_unpruned`; assert matrix vs. modeling key alignment
(`fail_on_feature_label_misalignment`); assemble the coverage report, manifest, and QA markdown;
record metrics (row counts per horizon, dropped-incomplete counts, symbol counts).

**`persist()`** — guard against empty canonical outputs (`fail_on_empty_output`); run final
coverage floors (`fail_on_low_label_coverage` → `_final_coverage_checks`); write each
`labels_{h}d.parquet`, `label_matrix.parquet`, `modeling_dataset.parquet`, optional unpruned,
`label_coverage_report.parquet`, `data_quality_labels.md`, the Hive `partitioned/` tree (if
`output_partitioned`), then stamp file SHA-256 hashes into the manifest and write
`label_manifest.json`. Finally, `_log_to_mlflow(manifest)` records the run to MLflow (gated by
`mlflow.log_label_run`, non-fatal). All output paths are registered on `self.output_paths`.

`load_labels(horizon)` is a convenience reader for a horizon's parquet (returns empty frame if
absent).

### Reproducibility & experiment tracking (parity with every other agent)

- **`data_content_hash`** (manifest, top level) — a deterministic 16-hex SHA-256 fingerprint of
  the **modeling dataset** computed by `_content_hash()`. It excludes provenance columns
  (`snapshot_id`, `run_id`, `created_at_utc`) and is **order-independent** (rows are sorted by
  `symbol, date_ts` before hashing), so an identical dataset always yields an identical
  fingerprint regardless of row order. This sits alongside the existing per-file SHA-256 hashes
  in `data_hashes` (`input_market`, `input_features`, `input_pruned_features`, `label_matrix`,
  `modeling_dataset`, `config_hash`) — the blueprint's "SHA-256 cryptographic hash of the exact
  data state" requirement, realized as a content fingerprint rather than a byte-level file hash.
- **MLflow logging** (`_log_to_mlflow`, gated `mlflow.log_label_run`, default `true`,
  non-fatal if MLflow is absent). Logs: **tags** (`agent`, `run_id`, `snapshot_id`,
  `data_content_hash`, `label_type`); **params** (`label_type`, `horizons`, `max_horizon_days`,
  `recommended_embargo_days`, `min_assets_per_label_date`); **metrics** (`label_matrix_rows`,
  `modeling_dataset_rows`, `modeling_dataset_symbols`, `horizon_count`, `max_horizon_days`); and
  **artifacts** (`label_manifest.json`, `label_coverage_report.parquet`, `data_quality_labels.md`).
  This satisfies the blueprint's mandate that the snapshot hash be "logged into an MLflow
  tracking server, running locally via SQLite." Tests disable it via `log_label_run: false`.

---

## 4. Coverage accounting & QA (per horizon)

`_compute_horizon_labels` tracks every dropped row so coverage is fully auditable. The
`label_coverage_report.parquet` row per horizon includes:

`horizon_days, total_candidate_rows, valid_label_rows, dropped_incomplete_rows,
dropped_non_exact_horizon_rows, dropped_bad_price_rows, dropped_missing_feature_rows,
dropped_low_cross_section_dates, dropped_low_cross_section_rows, symbols_with_labels,
first_label_date, last_label_date, first_future_date, last_future_date,
null_label_count, infinite_label_count, non_finite_label_count,
positive_label_count, negative_label_count, zero_label_count,
mean_label, std_label, min_label, p01_label, p50_label, p99_label, max_label,
passed_qa, failure_reason`.

Drop reasons, in order: rows missing a feature `(symbol, date_ts)` key
(`dropped_missing_feature_rows` — labels are kept only where a matching feature row exists);
incomplete / non-exact-horizon tails (`drop_incomplete_horizon_rows`, default on); bad-price
rows; and low-cross-section dates. The verifier independently re-checks that
`coverage.valid_label_rows` equals the actual written row count for each horizon, and that the
coverage horizon set equals the configured horizons.

> **No fabricated performance numbers.** The distribution stats above (`mean_label`, `p50_label`,
> etc.) are computed from whatever data the run produced; this doc states no specific values. The
> pipeline's headline alpha result is a deliberate negative (`alpha_verified=false`) and is owned
> by `BacktestAgent`, never by labels.

---

## 5. Complete config surface (`configs/run_config.yaml → labels`)

All keys read by `LabelAgent` / `verify_label_run.py`, with their code defaults:

```yaml
# Inputs (required unless noted)
input_market_path:           "data/raw/market/market_ohlcv.parquet"
input_features_path:         "data/features/full_features.parquet"
input_pruned_features_path:  "data/features/full_features_pruned.parquet"   # optional on disk
input_market_manifest_path:  "data/raw/market/market_manifest.json"
input_feature_manifest_path: "data/features/feature_manifest.json"

# Output
output_dir:                  "data/labels"
output_partitioned:          true          # write Hive partitioned/horizon=…/year=…/month=… tree

# Targets
horizons:                    [7, 14, 30]   # forward-return horizons in days
label_type:                  "forward_log_return"
max_horizon_days:            30            # documentation/manifest mirror of max(horizons)
drop_incomplete_horizon_rows: true         # drop rows whose exact t+h close is absent

# Cross-sectional label variants
include_cross_sectional_ranks: true
rank_method:                 "average"     # pandas rank() method for label_rank_pct
rank_pct:                    true          # rank as percentile
add_binary_direction_labels: true
add_quantile_bucket_labels:  true
quantile_buckets:            5             # buckets for label_quantile_bucket
min_assets_per_label_date:   20            # drop a date with fewer assets than this

# Modeling-dataset assembly
use_pruned_features_for_modeling_dataset: true
also_write_unpruned_modeling_dataset:     true

# Leakage / sanity floors emitted to the model stage
recommended_embargo_days:      30          # verifier asserts == max(horizons)
purge_train_test_overlap_days: 30

# Fail-fast guards (all default true)
fail_on_missing_inputs:            true
fail_on_empty_output:              true
fail_on_duplicate_symbol_date:     true
fail_on_non_positive_prices:       true
fail_on_null_labels:               true
fail_on_infinite_labels:           true
fail_on_target_leakage:            true
fail_on_feature_label_misalignment: true
fail_on_low_label_coverage:        true

# Coverage floors (enforced in persist via _final_coverage_checks)
min_symbols_required:          90
min_label_rows_required:       50000
min_rows_per_horizon_required: 50000
min_common_rows_all_horizons:  50000

# Optional
max_symbols:                 null          # cap selected symbols (used by smoke/labels_25 sections)
research_mode:               true
```

> Note on defaults: when a key is read with `.get(key, fallback)` and the key is absent from
> config, the code fallback applies (e.g. `min_assets_per_label_date` → 20, `quantile_buckets`
> → 5, the `min_*` floors → 1). The base `labels` section above sets the production floors
> (90 symbols / 50,000 rows) explicitly.

**Ready-to-run override sections** (merged over `labels` by `main.py --section` / verifier
`--section`):

- `labels_pit` — survivorship-free PIT handoff. The membership-masked modeling set is far
  sparser than the dense legacy universe, so the floors are lowered to sanity levels
  (`min_symbols_required: 50`, `min_*_rows: 5000`). Integrity note (from the config comment):
  less data makes alpha **harder** to verify, so the negative headline stays conservative.
- `labels_smoke` — offline smoke path: `output_dir: data/labels_smoke`, `max_symbols: 10`,
  floors `min_*_rows: 1000` / `min_symbols_required: 5`.
- `labels_25` — 25-symbol path: `max_symbols: 25`, floors `min_*_rows: 10000` /
  `min_symbols_required: 20`.

---

## 6. Verifier (`scripts/verify_label_run.py`)

`validate_label_outputs(cfg)` returns a list of `FAIL:` strings (empty = PASS). It is an
**independent** re-derivation, not a trust of the manifest:

- **Existence + schema** of every `labels_{h}d.parquet`, `label_matrix`, `modeling_dataset`,
  coverage, manifest, quality file, including the exact horizon-suffixed columns.
- **Formula re-computation** — recomputes `ln(close_t_plus_h / close_t)` on a sample of rows and
  fails on any mismatch (`atol=1e-12`) — catches silent label corruption
  (`test_verify_label_run_rejects_formula_mismatch`).
- **Per-row invariants** — no duplicate `(symbol, date_ts)`; `date_ts` and `future_date_ts`
  normalized to **UTC midnight**; `future_date_ts > date_ts`; no null / infinite
  `label_fwd_logret`; no non-positive `close_t` / `close_t_plus_h`.
- **Leakage** — re-scans the feature input for prohibited token columns and fails if any
  `label_*` column leaked into `data/features/`.
- **Coverage / manifest reconciliation** — `coverage.valid_label_rows` must equal the actual
  written rows; manifest `label_matrix_rows`, `modeling_dataset_rows`, `*_symbols`,
  `label_rows_by_horizon` must match the parquet files; `recommended_embargo_days` must equal
  `max(horizons)`.
- **Coverage floors** — every configured `min_*` floor re-checked against the files.
- **Modeling dataset** — must contain all `label_*` columns from the matrix **and** at least one
  feature column.

Run: `python scripts/verify_label_run.py --config configs/run_config.yaml --section labels`.

---

## 7. Limitations / what work is left

- **Single label family.** Only forward log-return (and its derived simple-return / direction /
  rank / bucket variants) is produced. There is no volatility-scaled / risk-adjusted target, no
  market- or beta-neutral residual return, and no triple-barrier / meta-labeling target. A real
  cross-sectional book often models risk-adjusted or residualized returns.
- **No per-asset cost or borrow adjustment in the label.** Targets are raw price log-returns;
  transaction costs and benchmark sanity are applied **only** downstream in `BacktestAgent`. The
  label says nothing about tradability.
- **Coverage inherits upstream survivorship.** Labels are only as point-in-time as the market /
  feature panels feeding them. In the legacy (latest-survivor) path the targets carry that bias;
  the `labels_pit` section exists for the survivorship-free handoff but its floors are
  intentionally loose because the masked panel is sparse.
- **No outlier/winsorization policy on the label itself.** Extreme-but-real moves pass through as
  large log-returns (only non-finite values are dropped). There is no fat-tail clipping or
  return-cap option at the label stage; any robust-loss handling is deferred to the model.
- **Exactness guard can thin sparse coins.** The `is_exact_horizon` requirement (correctly)
  discards rows where the daily grid has gaps; for sparsely-listed assets this can materially
  reduce usable label rows — a correctness/coverage trade-off, accepted in favor of correctness.

---

## 8. How it compares to a hedge fund (honest)

**Where it meets the bar.** The exact-calendar-horizon guard, the explicit embargo/purge
recommendation tied to the max horizon, the as-of alignment of a future return to the feature
date, the pre-join leakage-token scan, the no-clipping price discipline, and a fully independent
re-computing verifier are exactly the controls a serious systematic desk uses to keep a
backtest honest. The cross-sectional, within-date rank/bucket construction matches how a
cross-sectional equity/crypto book frames targets. Full provenance (input hashes, config hash,
snapshot ids, coverage accounting) is production-grade reproducibility.

**Where a real fund goes further.** A production desk would typically model **risk-adjusted or
residualized** returns (vol-scaled, market/sector/beta-neutralized) rather than raw log-returns;
would maintain **multiple label families** and meta-labels for sizing; would bake **borrow/funding
and realistic cost** assumptions closer to the signal; would have **true point-in-time** universe
and corporate-action handling end-to-end; and would manage label/feature versioning and lineage
through a feature/label store with formal governance rather than parquet files plus a manifest.

**The honest bottom line.** This Label Agent is a research-integrity instrument, not an alpha
generator. Its design goal is to make a *false* positive hard to manufacture, not to make a
positive appear. That is why the pipeline's headline result remains a defensible
**`alpha_verified=false`**: the targets are clean, so the negative is real.
