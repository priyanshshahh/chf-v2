# Feature Agent — Complete Reference

The Feature Agent (`agents/feature_agent.py`) is the **fourth node** of the CHF pipeline. It
consumes the canonical upstream artifacts (the market OHLCV panel, the on-chain wide/observation
tables, and the universe membership) and produces a leakage-safe, research-grade cross-sectional
feature panel that every downstream stage (labels → model → portfolio → backtest) reads. It builds
market features from price/volume, lagged on-chain features from CoinMetrics/DeFiLlama metrics,
cross-sectionally winsorizes and z-scores each feature within a date, deterministically prunes
collinear features (Spearman correlation + VIF), and writes the canonical feature tables plus a full
coverage/quality/provenance trail.

This is the **single exhaustive reference** for the agent: its full output contract, the
research-integrity guards, the complete lifecycle, the full config surface, the limitation register,
and an honest comparison to a real systematic desk.

> Implementation note: `FeatureAgentV1` and `FeatureAgentV2` are both **backward-compatible
> subclasses** of the canonical `FeatureAgent` — the two-tier "market-only vs market+on-chain" split
> referenced in `CLAUDE.md` is realized inside a single agent that emits **three** feature sets
> (`market`, `onchain`, `full`) in one run, not two separately-coded agents.

---

## 1. Output contract

Written to `data/features/` (`features.output_dir`, default `data/features`):

| Artifact | Contents |
|---|---|
| `market_features.parquet` | Market (OHLCV-derived) feature set, `feature_set="market"` |
| `onchain_features.parquet` | On-chain feature set on the market date backbone, `feature_set="onchain"` |
| `full_features.parquet` | **Canonical** market ⨝ on-chain panel, `feature_set="full"` — the file downstream stages read |
| `full_features_pruned.parquet` | The model-ready subset after correlation + VIF pruning (written only when `pruning.enabled`) |
| `feature_coverage_report.parquet` | Per-feature QA: null/finite counts, null_pct, p01/p50/p99, first/last valid date, `passed_qa` |
| `feature_manifest.json` | Full provenance (run_id, snapshot_id, `data_content_hash`, membership_mode, input manifest summaries, row/symbol/feature counts, winsor/zscore/pruning config, warnings, limitations) |
| `feature_dictionary.json` | Per-feature definition record (group, formula, source columns, lookback, leakage policy, rationale) |
| `feature_keep_list.json` | The pruning decision log: `all_candidate_features`, `kept_features`, `dropped_features`, `dropped_reason`, thresholds |
| `data_quality_features.md` | Human-readable QA tear-sheet (also written on fatal-error exit) |
| `partitioned/{market,full}/year=YYYY/month=MM/part-<run_id>.parquet` | Hive-partitioned copies of the market and full panels |

### Shared schema (all three feature parquets)

Identity/metadata columns carried on every row:

```
date_ts, symbol, feature_set, feature_version, snapshot_id, run_id, created_at_utc
```

`onchain_features` and `full_features` additionally carry `onchain_lag_days`. `date_ts` is
UTC-aware and normalized; `symbol` is upper-cased. The row grain is one `(symbol, date_ts)` pair —
duplicates are a fatal error (`fail_on_duplicate_symbol_date`).

### Market feature columns (`feature_set="market"`)

Built per symbol in `_build_market_features`. Exact column list (from `market_feature_cols`):

```
log_ret_1d, log_ret_3d, log_ret_7d, log_ret_14d, log_ret_30d, log_ret_60d, log_ret_90d,
momentum_7_30, momentum_14_90,
realized_vol_7d, realized_vol_14d, realized_vol_30d, realized_vol_60d,
skew_30d, downside_vol_30d,
reversal_3_30, price_sma_gap_14d, price_sma_gap_30d, zscore_close_30d,
dollar_volume, log_dollar_volume, volume_ratio_7d, volume_ratio_30d, dollar_volume_ratio_30d, volume_zscore_30d,
hl_range_pct, atr_proxy_14d,
drawdown_30d, drawdown_90d, distance_from_30d_high, distance_from_90d_high,
beta_btc_60d, corr_btc_60d,
is_forward_filled_market, market_data_available, market_history_days_available
```

Key formulas (verified in code):

- `log_ret_Nd = safe_log_ratio(close, close.shift(N))` — natural log of the N-day price ratio,
  NaN whenever either price is ≤ 0.
- `momentum_7_30 = log_ret_7d − log_ret_30d`; `momentum_14_90 = log_ret_14d − log_ret_90d`.
- `realized_vol_Nd = std(log_ret_1d, window=N) * sqrt(365)` with `min_periods = max(N//2, 5)`.
- `skew_30d = skew(log_ret_1d, 30)`; `downside_vol_30d = rolling_downside_vol(log_ret_1d, 30)`
  (RMS of negative-clipped returns × √365).
- `price_sma_gap_14d = close/SMA14 − 1`; `zscore_close_30d = rolling_zscore(close, 30)`.
- `dollar_volume` prefers the unit-correct `dollar_volume_usd` from MarketDataAgent; falls back to
  `close * volume` for legacy files. `log_dollar_volume = log(dollar_volume)` where positive.
- `hl_range_pct = (high − low)/close`; `atr_proxy_14d` is the 14-day mean of true-range/close.
  On `is_synthetic_ohlc` (forward-filled) bars, H/L are nulled first so fabricated zero-range bars
  do not feed range/volatility features.
- `beta_btc_60d`, `corr_btc_60d` — 60-day rolling beta/correlation of the asset's daily log return
  against BTC's daily log return (`rolling_beta_and_corr`, `min_periods=30`).

`is_forward_filled_market`, `market_data_available`, `market_history_days_available` are **diagnostic**
columns (see §3, leakage/diagnostic split).

### On-chain feature columns (`feature_set="onchain"`)

Built in `_build_onchain_features` from `ONCHAIN_RAW_METRICS` (`adr_active_count`, `tx_count`,
`current_supply`, `issuance_total_usd`, `market_cap_usd`, `mvrv_current`, `chain_tvl_usd`,
`protocol_tvl_usd`, `fees_usd`, `dex_volume_usd`). Exact column list (`onchain_feature_cols`):

```
adr_active_count, tx_count, current_supply, issuance_total_usd, market_cap_usd, mvrv_current,
chain_tvl_usd, protocol_tvl_usd, fees_usd, dex_volume_usd,
onchain_available, coinmetrics_available, defillama_available,
missing_adr_active_count, missing_tx_count, missing_mvrv_current, missing_chain_tvl_usd,
missing_protocol_tvl_usd, missing_fees_usd, missing_dex_volume_usd,
onchain_feature_count_non_null, log_adr_active_count, adr_active_growth_7d, adr_active_growth_30d,
tx_count_growth_7d, tx_count_growth_30d, tx_count_zscore_30d, mvrv_change_30d, mvrv_zscore_90d,
realized_cap_proxy, nvt_tx_proxy, nvt_dex_proxy, chain_tvl_growth_7d, chain_tvl_growth_30d,
protocol_tvl_growth_30d, fees_growth_30d, dex_volume_growth_30d, fees_to_tvl, dex_volume_to_tvl,
supply_growth_30d, issuance_to_market_cap, market_cap_growth_30d, onchain_lag_days
```

Selected formulas: `*_growth_Nd = safe_log_ratio(metric, metric.shift(N))`;
`mvrv_change_30d = metric/metric.shift(30) − 1`; `mvrv_zscore_90d = rolling_zscore(mvrv_current, 90)`;
`realized_cap_proxy = market_cap_usd / mvrv_current` (when mvrv > 0);
`nvt_tx_proxy = market_cap_usd / mean(tx_count, 30)`; `nvt_dex_proxy = market_cap_usd / mean(dex_volume_usd, 30)`;
`fees_to_tvl = fees_usd / protocol_tvl_usd`; `issuance_to_market_cap = issuance_total_usd / market_cap_usd`.

The `missing_*` columns are 0/1 missingness indicators; `onchain_available`,
`coinmetrics_available`, `defillama_available`, `onchain_feature_count_non_null`, `onchain_lag_days`
are diagnostic coverage flags.

### Full feature set (`feature_set="full"`)

`market_features` left-joined to the on-chain feature columns on `(date_ts, symbol)` (validated
`one_to_one`). It is the union of the market and on-chain features plus the shared metadata; the
on-chain join is `how="left"` so market rows are always retained even where on-chain data is absent.

### Partitioning

`_write_partitioned` writes `market` and `full` Hive-partitioned by `year=YYYY/month=MM`, one
`part-<run_id>.parquet` per partition. The partition root is fully rewritten (`shutil.rmtree`) each
run for determinism.

---

## 2. Research-integrity guards (do not violate)

- **No target/label/forward columns can ever enter the panel.** `check_for_prohibited_columns`
  rejects any column whose lower-cased name contains `target`, `label`, `forward`, `future`,
  `ret_fwd`, `lead`, or `next_return` (the single exemption is `is_forward_filled_market`). This is
  enforced in `_validate_feature_frame` (gated by `fail_on_target_leakage`, default on) **and**
  independently in `scripts/verify_feature_run.py`.
- **On-chain features are lagged by `onchain_lag_days` (default 1).** In `_build_onchain_features`
  every on-chain feature column — including the coverage flags — is `groupby(symbol).shift(lag_days)`
  *before* the join onto the market date backbone, so the value visible on day *d* was knowable
  strictly before *d*. The policy key `onchain_feature_policy: lagged_asof` documents this. Market
  features follow `market_feature_policy: completed_daily_candle`: the incomplete current-day candle
  is already dropped upstream by MarketDataAgent, and all market features use only past/current
  *completed* bars (rolling windows look backward via `.shift`/`.rolling`).
- **Two-tier feature split is emitted, not collapsed.** A single run produces `market`, `onchain`,
  and `full` sets. The on-chain join cannot delete or overwrite a market row (left join, validated
  one-to-one), so adding on-chain coverage never silently shrinks the market panel.
- **Leakage-aware cross-sectional post-processing.** Winsorization and z-scoring are computed
  *within each `date_ts`* (`cross_sectional_winsorize_by_date`, `cross_sectional_zscore_by_date`) —
  they never reach across dates, so no future cross-section informs a past row. The z-score variant
  appends `_cs_z` columns and is suppressed for a date with fewer than `min_assets_per_date` (10)
  valid assets.
- **`data_content_hash` for reproducibility.** `_content_hash` is a 16-hex SHA-256 fingerprint of
  the sorted `(symbol, date_ts, all-feature-columns)` panel, written to the manifest and the MLflow
  tags. It is order-independent and value-sensitive (tested in `test_content_hash_deterministic_and_order_independent`),
  so identical inputs always produce an identical, auditable fingerprint regardless of wall-clock.
- **Diagnostic features are excluded from the model by default.** `_model_feature_columns` removes
  `DIAGNOSTIC_FEATURE_COLUMNS` (`onchain_available`, `coinmetrics_available`, `defillama_available`,
  `onchain_feature_count_non_null`, `market_data_available`, `market_history_days_available`,
  `is_forward_filled_market`, `onchain_lag_days`) from the pruning candidate pool unless
  `allow_diagnostic_model_features` is set, so coverage/availability flags do not become signals.
- **PIT membership masking (`membership_mode: pit_daily`).** Features are computed over the **full
  union panel** (so rolling windows see real pre-membership history), then `_apply_membership_filter`
  masks the emitted rows down to actual `(date, symbol)` member pairs from
  `universe_membership_daily.parquet`. The model therefore only ever sees a coin on days it was a
  genuine top-N member — survivorship-free. No-op in `latest_snapshot` mode.
- **MLflow gating.** `_log_to_mlflow` logs params/metrics/tags (including `data_content_hash`) and
  artifacts, gated by `mlflow.log_feature_run` (default on) and **fully non-fatal** if MLflow is
  absent or errors.

---

## 3. Lifecycle (`prepare → run → persist`)

**`prepare()`** — make `output_dir`; assert the six canonical inputs exist (market parquet, on-chain
wide parquet, on-chain observations parquet, universe monthly parquet, market manifest, on-chain
manifest) — missing inputs raise when `fail_on_missing_inputs` (default true). Read the input
manifests; resolve the allowed-symbol set and its snapshot id via `_load_allowed_symbols`
(dispatches to `_load_pit_membership` or the latest-eligible-snapshot path by `membership_mode`);
load and clean the market, on-chain wide, and on-chain observation frames (UTC-normalize, upper-case
symbols, filter to allowed symbols, drop duplicates, keep only `is_full_ohlcv` market rows with
positive O/H/L/C). Empty market data raises `FeatureAgentError`. Finally derive a deterministic
`snapshot_id` from `features:{universe_snap}:{market_snap}:{onchain_snap}:{n_market_rows}`.

**`run()`** — build `market_features` (`_build_market_features`) and `onchain_features`
(`_build_onchain_features`, lagged); join into `full_features`; apply `_apply_membership_filter` to
all three (no-op outside `pit_daily`); `_drop_all_null_features` on each; `_validate_feature_frame`
on each (dup check, prohibited-column check, infinite-value check); compute the per-feature
`coverage` report; run `_prune_full_features` (correlation + VIF) to produce `keep_info` and the
pruned panel; build the `feature_dictionary`; populate `self.metrics`; and compute `_fatal_errors`
(low-coverage / empty / all-null gates). Returns all frames plus `keep_info` and `fatal_errors`.

**`persist(result)`** — if `fatal_errors` is non-empty, write the quality report and **raise**
(no parquet is written). Otherwise write `market_features.parquet`, `onchain_features.parquet`,
`full_features.parquet`, the pruned parquet (when pruning enabled), the coverage report, the
dictionary JSON, the keep-list JSON, the Hive-partitioned copies, the quality `.md`, and the
`feature_manifest.json` (with `data_content_hash`, membership mode, input-manifest summaries,
row/symbol/feature counts, and the winsor/zscore/pruning config). Then call `_log_to_mlflow`
(non-fatal). The base `AgentBase.execute()` wraps all three with retries, status tracking, and the
SQLite registry write (`metadata/agent_registry.db`).

### Pruning (`_prune_full_features`)

Candidate pool = model feature columns (diagnostics excluded) that are numeric, have any non-null
values, and have null fraction ≤ `missingness.max_null_pct_feature` (0.95). Then:

1. `deterministic_correlation_prune` — drops the member of any |Spearman| ≥ `correlation_threshold`
   (0.85) pair, choosing the **loser deterministically** by (higher null %, higher average absolute
   correlation, then name), never dropping below `min_final_features` (20); caps the survivors at
   `max_final_features` (60).
2. `iterative_vif_prune` — iteratively removes the highest-VIF feature while max VIF >
   `vif_threshold` (10.0), down to `min_final_features`. Degrades to a no-op if `statsmodels` is
   unavailable. Both decisions are logged into `feature_keep_list.json`.

### Fatal-error gates (`_fatal_errors`)

`market_features_empty` / `full_features_empty` (when `fail_on_empty_output`);
`feature_coverage_report_empty`; coverage floors when `fail_on_low_feature_coverage`:
`market_symbols < min_market_symbols_required` (90), `onchain_symbols < min_onchain_symbols_required`
(40), `full_symbols < min_full_feature_symbols_required` (90),
`full_rows < min_rows_required` (50000); and `all_null_features_present` when
`fail_on_all_null_feature`. (`onchain_symbols` counts only symbols where `onchain_available` is true.)

### Verifier (`scripts/verify_feature_run.py`)

Independently re-checks the on-disk outputs: required files present; UTC-parseable `date_ts`; no
duplicate `(symbol, date_ts)`; no prohibited columns; no infinite or all-null numeric features;
`onchain_features` carries `onchain_available` and at least one `missing_*` indicator; coverage
report feature names exactly match each set's columns; manifest row/symbol counts match the
parquets; `feature_dictionary` defines every full feature; pruned columns are a subset of full
columns and a superset of the keep list (warning); the symbol/row floors; `len(full) ≥ len(market)`;
and, in `pit_daily` mode, that `full_features` spans more than one month (guards against silent
survivor-collapse).

---

## 4. Complete config surface (`configs/run_config.yaml → features`)

Every key the agent actually reads, with the code default applied when the key is absent:

**Inputs / outputs / engine**
```yaml
input_market_path: "data/raw/market/market_ohlcv.parquet"
input_onchain_wide_path: "data/raw/onchain/onchain_wide.parquet"
input_onchain_observations_path: "data/raw/onchain/onchain_observations.parquet"
input_universe_path: "data/raw/universe/universe_monthly.parquet"
input_market_manifest_path: "data/raw/market/market_manifest.json"
input_onchain_manifest_path: "data/raw/onchain/onchain_manifest.json"
output_dir: "data/features"
use_duckdb: true              # read parquet via DuckDB; false → pandas.read_parquet
```

**Membership / PIT (Phase 1)**
```yaml
membership_mode: latest_snapshot      # | pit_daily
membership_daily_path: "data/raw/universe/universe_membership_daily.parquet"
require_pit_membership: false         # pit_daily + missing mask → raise (true) or fall back (false)
max_symbols: null                     # cap on the symbol universe (both modes)
```

**Failure gates**
```yaml
fail_on_missing_inputs: true
fail_on_empty_output: true
fail_on_duplicate_symbol_date: true
fail_on_target_leakage: true
fail_on_all_null_feature: true
fail_on_low_feature_coverage: true
# (fail_on_future_leakage appears in the YAML but is not read by the agent today)
```

**Coverage floors**
```yaml
min_market_symbols_required: 90
min_onchain_symbols_required: 40
min_full_feature_symbols_required: 90
min_rows_required: 50000
```

**Feature windows / lag**
```yaml
market_windows:
  returns:    [1, 3, 7, 14, 30, 60, 90]   # read by the agent (returns/volatility used)
  volatility: [7, 14, 30, 60]
  # volume / skewness / beta / atr / drawdown windows are present in YAML for documentation;
  # the corresponding features use hard-coded windows in code (e.g. SMA 14/30, beta 60, ATR 14).
onchain_lag_days: 1
market_feature_policy: "completed_daily_candle"
onchain_feature_policy: "lagged_asof"
```

**Cross-sectional post-processing**
```yaml
winsorization:
  enabled: true
  lower_quantile: 0.01
  upper_quantile: 0.99
cross_sectional_zscore:
  enabled: true
  min_assets_per_date: 10
missingness:
  max_null_pct_feature: 0.95            # candidate drop threshold in pruning
```

**Pruning**
```yaml
pruning:
  enabled: true
  correlation_threshold: 0.85
  vif_enabled: true
  vif_threshold: 10.0
  max_final_features: 60
  min_final_features: 20
```

**Versioning / model-feature policy**
```yaml
feature_versions:
  market: "market_v1"
  onchain: "onchain_v1"
  full: "full_v1"
allow_diagnostic_model_features: false  # true → keep coverage/availability flags as model candidates
```

**MLflow section (`configs/run_config.yaml → mlflow`)**
```yaml
log_feature_run: true                   # gate for FeatureAgent MLflow logging
tracking_uri: mlruns
experiment_name: CHF_experiments
log_artifacts: true
```

**Ready-to-run section:** `features_pit` — `membership_mode: pit_daily`,
`require_pit_membership: true`, and PIT-appropriate (lower) coverage floors
(`min_market_symbols_required: 50`, `min_onchain_symbols_required: 15`,
`min_full_feature_symbols_required: 50`, `min_rows_required: 20000`), because dead coins
legitimately have no on-chain data in a survivorship-free run. Invoked via
`python main.py features --section features_pit`.

---

## 5. Limitations / what work is left

- **On-chain coverage is sparse relative to market coverage**, by data availability, not bug:
  CoinMetrics/DeFiLlama only cover a subset of the universe, so most on-chain features are null for
  many symbols. The agent surfaces this honestly via `onchain_available`, the `missing_*` indicators,
  `onchain_feature_count_non_null`, and the per-feature coverage report rather than imputing — and
  the `features_pit` floors are deliberately relaxed to avoid mislabeling legitimate sparsity as a
  data-quality failure.
- **On-chain metric mapping is upstream and symbol-keyed.** The agent consumes whatever metrics the
  OnChainAgent wrote into `onchain_wide.parquet`/`onchain_observations.parquet` (the
  symbol/metric→column mapping, including any substring/heuristic matching of on-chain identifiers,
  lives in the OnChain stage, not here). A mis-mapped symbol upstream silently surfaces as missing
  on-chain coverage downstream.
- **Several documented `market_windows` sub-keys are not wired.** `volume`, `skewness`, `beta`,
  `atr`, `drawdown` (and the entire `onchain_windows` block) appear in `run_config.yaml` but the
  corresponding features use hard-coded windows in code (SMA 14/30, beta 60d, ATR 14d, drawdown
  30/90d, growth 7/30d, etc.). Editing those YAML lists has no effect today — a real config-as-truth
  cleanup would route every window through config.
- **`fail_on_future_leakage` is declared but inert.** The future-leakage guard is realized via the
  prohibited-column check and the on-chain lag, not via this specific key; the key is currently not
  read by the agent.
- **`market_history_days_available` is a positional counter, not a calendar age.** It is
  `arange(1, len(grp)+1)` over the in-panel rows for a symbol, so it reflects rows present after
  cleaning, not true listing age.
- **Pruning is correlation/VIF only.** It removes redundancy and multicollinearity deterministically
  but performs **no** predictive feature selection (no information-coefficient ranking, no
  forward/backward selection, no stability across folds) — by design, to keep the negative-result
  pipeline free of in-sample target peeking.
- **VIF pruning silently degrades without `statsmodels`** (returns the candidates unpruned), so the
  effective feature set depends on the runtime environment unless `statsmodels` is pinned.
- **No real-time / streaming feature path.** All features are batch-computed over the historical
  panel; this is research/education, not live execution.

---

## 6. How it compares to a hedge fund

**What is genuinely fund-grade here:**

- **Leakage discipline as a first-class, enforced contract.** Prohibited-column rejection, an
  explicit on-chain as-of lag, strictly cross-sectional (within-date) winsorization and z-scoring,
  and an independent verifier that re-checks the same invariants on disk. Many real desks rely on
  convention; this enforces it in code and fails the run otherwise.
- **Full provenance and reproducibility.** A deterministic, order-independent `data_content_hash`,
  a window-keyed `snapshot_id`, the SQLite agent registry, MLflow logging, and a complete
  `feature_dictionary.json` / `feature_keep_list.json` audit trail — the kind of lineage a
  production research platform requires.
- **Survivorship-free construction.** The `pit_daily` path computes features over the full union
  panel (correct warmup) and masks to true per-day membership, which is exactly how a careful
  systematic desk avoids survivorship bias.
- **Deterministic, reproducible dimensionality reduction.** Correlation + VIF pruning with stable
  tie-breaks and a logged decision trail.

**What a real systematic desk adds that this does not:**

- **Point-in-time fundamental/reference data with vendor-grade restatement handling** and a true
  PIT database, rather than symbol-keyed on-chain metrics with sparse coverage.
- **Predictive feature selection and signal research** — IC/IR-ranked feature evaluation, regime
  conditioning, decay/half-life analysis, cross-validated stability — instead of redundancy pruning
  alone.
- **Far richer feature families** — order-book/microstructure, funding/basis/term-structure,
  options-implied surfaces, alternative data, and cross-asset/macro factors — sourced from low-latency
  feeds.
- **Real-time and incremental feature computation** with point-in-time-consistent online/offline
  parity (a feature store serving both training and live execution from identical code).
- **Config-as-truth everywhere** (every window parameterized) and continuous data-quality monitoring
  with alerting, not a single batch quality report.

The honest framing: this Feature Agent is engineered to **protect the integrity of a negative
result** (`alpha_verified=false`) — its discipline around leakage, provenance, and survivorship is
strong, while its breadth of data sources and its predictive signal-selection machinery are
deliberately minimal. The pipeline computes Rank IC and the backtest metrics downstream by name; this
stage fabricates no performance numbers and claims no alpha — `BacktestAgent` remains the sole alpha
authority.
