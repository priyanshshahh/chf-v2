# Model Agent — Complete Reference

The Model Agent (`agents/model_agent.py`) is the **modeling node** of the CHF pipeline. It
reads the canonical leakage-checked panel `data/labels/modeling_dataset.parquet`, trains every
requested `(horizon × feature_set × model)` combination under **purged + embargoed
walk-forward cross-validation** (`models/walk_forward.py`), scores each combination on
out-of-sample predictions with **Rank IC**, **Rank IC t-stat**, and **hit-rate** metrics, runs
a deterministic **signal gate** to nominate at most one candidate for downstream evaluation,
and writes the predictions, leaderboard, fold metrics, and manifest that the portfolio and
backtest stages consume. It is a **signal-quality stage, not an alpha authority** — it never
claims verified alpha; every leaderboard row and the manifest carry
`alpha_status="not_evaluated_by_backtest"`, and only `BacktestAgent` can verify or reject
alpha. The headline research result remains a deliberate **negative** (`alpha_verified=false`).

---

## 1. Output contract

All artifacts are written by `persist()` to `data/predictions/`.

| Artifact | Contents |
|---|---|
| `model_predictions.parquet` | Out-of-sample predictions only — one row per `(model, feature_set, horizon, symbol, test date)` across all folds |
| `model_leaderboard.parquet` | One row per `(model, feature_set, horizon)` combination, with quality metrics, signal-gate verdict, and selection flags |
| `fold_metrics.parquet` | One row per walk-forward fold (per combination), with train/test/embargo boundaries and per-fold quality metrics |
| `model_manifest.json` | Full provenance: run/snapshot ids, `data_content_hash`, requested vs completed vs failed runs, selected model, alpha/research status, limitations |
| `feature_importance.parquet` | Mean tree-model feature importances per combination (empty for the baseline, which has no `feature_importances_`) |
| `data_quality_model.md` | Human-readable summary (prediction/fold/leaderboard/failure counts + limitations) |

### `model_predictions.parquet` columns

Built per fold in `_train_combination`. Required columns enforced by `scripts/verify_model_run.py`:

```
date_ts, symbol, model_name, feature_set, horizon_days, fold_id,
prediction, actual_forward_return,
prediction_rank, prediction_rank_pct, actual_rank, actual_rank_pct,
is_top_5, is_top_10, is_top_20, is_bottom_10,
train_start, train_end, test_start, test_end,
snapshot_id, run_id
```

- `prediction_rank` / `actual_rank` — within-date dense ordering (`rank(method="first", ascending=False)`); rank 1 = highest predicted / highest realized return.
- `prediction_rank_pct` / `actual_rank_pct` — within-date percentile (`method="average", pct=True, ascending=True`).
- `is_top_5/10/20` — `prediction_rank <= k`. `is_bottom_10` — within the lowest 10 predicted ranks of the date.
- `train_end` carries `split.train_end_purged` (the **purged** train end, not the raw end), so `train_end < test_start` is always true.
- Dates with fewer than `min_assets_per_prediction_date` (default **20**) distinct symbols are dropped from the prediction frame (insufficient cross-section to rank).

### `model_leaderboard.parquet` columns

Each row = `summarize_predictions()` output (see §3) plus the selection block added in
`_train_combination` and overwritten by `_apply_selection_logic`:

```
model_name, feature_set, horizon_days,
fold_count, test_date_count, test_symbol_count, prediction_rows, prediction_coverage,
rank_ic_mean, rank_ic_std, rank_ic_tstat, rank_ic_hit_rate, n_features,
top_5_mean_actual_return, top_10_mean_actual_return, top_20_mean_actual_return,
bottom_10_mean_actual_return, top_bottom_10_spread, top_10_hit_rate,
rmse, mae, r2,
failure_reason, missing_feature_fraction,
composite_score,                              # rank_ic_mean + top_bottom_10_spread + top_10_hit_rate
signal_status,                                # passed_signal_screen | failed_signal_screen
signal_gate_passed, signal_gate_failure_reason,
candidate_for_backtest, selected_for_backtest,
alpha_status                                  # always "not_evaluated_by_backtest"
```

### `fold_metrics.parquet` columns

One row per fold, from `_train_combination`:

```
model_name, feature_set, horizon_days, fold_id,
train_start, train_end_raw, train_end_purged, embargo_start, embargo_end, test_start, test_end,
train_rows, test_rows, train_symbols, test_symbols,
purge_days, embargo_days, n_features,
dropped_non_finite_train_labels, dropped_non_finite_test_labels,
<all summarize_predictions() metrics for the fold's predictions>
```

The verifier asserts `train_end_purged < test_start` on every fold and `train_end < test_start`
on every prediction row.

### `model_manifest.json` keys

```
run_id, snapshot_id, data_content_hash, created_at_utc,
modeling_dataset_path, label_manifest_path, feature_manifest_path,
requested_models, requested_horizons, requested_feature_sets,
completed_runs, failed_runs,
selected_model, selected_feature_set, selected_horizon_days,
alpha_status,                       # "not_evaluated_by_backtest"
any_signal_gate_passed, any_candidate_for_backtest, no_candidate_reason, backtest_ready,
research_status,                    # candidate_signal_ready_for_backtest | no_candidate_signal_passed
best_rank_ic, best_rank_ic_tstat, best_top_bottom_spread,
prediction_rows, fold_count, embargo_days, purge_days,
warnings, limitations, output_files
```

---

## 2. Lifecycle (`prepare → run → persist`)

The base `AgentBase.execute()` wraps these three with retries, status tracking, logging, and
the SQLite registry write — subclasses never reimplement that.

**`prepare()`** — resolve `modeling.input_path` (default `data/labels/modeling_dataset.parquet`);
require it plus `data/labels/label_manifest.json` and `data/features/feature_manifest.json`
(`FileNotFoundError` if any is missing). Load the dataset, UTC-normalize `date_ts`, sort by
`(date_ts, symbol)`. Optionally subset to the first `max_symbols` symbols. Fail
(`ModelAgentError`) if the panel is empty or has duplicate `(date_ts, symbol)` rows. Run the
**leakage screen** (`_prohibited_feature_columns`) over non-metadata, non-`label_` columns; if
any prohibited column survives and `fail_on_leakage` (default true), raise. Load the label and
feature manifests, and `feature_keep_list.json` if present (`kept_features` / `keep_list`).

**`run()`** — generate a `modeling_research` snapshot id, then loop
`horizons → feature_sets → models`:
- Per horizon, require `label_fwd_logret_{h}d`; drop rows where that label is NaN.
- Per feature set, select feature columns via `_select_feature_columns` (§3); skip if `<1`.
- Per model, call `_train_combination`; on success append predictions, fold rows, the
  leaderboard row, and feature-importance rows; on any exception, record a structured row in
  `failed_runs` (never crashes the whole run).

Then concatenate, apply `_apply_selection_logic` to the leaderboard, build the manifest, and
populate run metrics (`prediction_rows`, `fold_count`, `completed_runs`, `best_rank_ic`).
`fail_on_empty_output` / `fail_on_no_valid_folds` (both default true) raise if nothing valid
was produced.

**`persist()`** — write the five parquet/JSON artifacts plus `data_quality_model.md` to
`data/predictions/`, record each in `output_paths`, then `_log_to_mlflow(manifest)`
(non-fatal, gated).

---

## 3. Research-integrity guards (do not violate)

### Purged + embargoed walk-forward CV — the exact math

Implemented in `generate_purged_walk_forward_splits` (`models/walk_forward.py`). Splits walk
**forward in calendar time** over the sorted unique dates. With `initial_train_days`,
`test_days`, `step_days`, `embargo_days`, and `purge_days` (defaulting to the horizon), for
each fold:

```
test_start_pos    = initial_train_days + embargo_days        # first test date index
test_start        = unique_dates[test_start_pos]
test_end          = unique_dates[min(test_start_pos + test_days - 1, last)]

raw_train_end_pos = test_start_pos - embargo_days - 1        # break if < 0
train_end_raw     = unique_dates[raw_train_end_pos]

train_end_purged  = min(train_end_raw, test_start - Timedelta(days = purge_days + 1))
embargo_start     = train_end_purged + 1 day
embargo_end       = test_start - 1 day

train_mask = date_ts <= train_end_purged
test_mask  = test_start <= date_ts <= test_end
```

`test_start_pos += step_days` each iteration; folds stop when `test_start_pos + test_days - 1`
runs off the end of the date list.

**Why this is leakage-safe.** Labels are **forward** calendar returns over `horizon_days`, so
a training row dated `t` embeds information up to `t + horizon`. Two cutbacks remove that
overlap:
- **Embargo** — the test window starts `embargo_days` after the nominal end of the initial
  train window, and `raw_train_end_pos` is pulled back a further `embargo_days`, so there is a
  calendar gap between any training row and the first test date.
- **Purge** — `train_end_purged = min(train_end_raw, test_start − (purge_days + 1))`
  guarantees the last *used* training date is at least `purge_days + 1` days before
  `test_start`. With `purge_days` defaulting to the horizon, **no training label's forward
  window can reach into the test period**. `train_end_purged < test_start` is therefore an
  invariant the verifier and tests (`test_model_enforces_embargo`,
  `test_verify_model_rejects_leakage_split`) check explicitly.

A fold is only yielded if `train_rows ≥ min_train_rows`, `test_rows ≥ min_test_rows`, and
`test_symbols ≥ min_test_symbols`. In the agent the effective embargo is
`max(wf.embargo_days, label_manifest.recommended_embargo_days, horizon)` and the effective
purge is `wf.purge_days or horizon` — both are widened to at least the horizon regardless of
config.

### Primary quality metrics — Rank IC by date

`summarize_predictions` (`models/walk_forward.py`) computes, **per test date**, the Spearman
rank correlation between `prediction` and `actual_forward_return` (`rank_ic_by_date`, needs
≥3 valid symbols per date), then aggregates across dates:
- `rank_ic_mean` — mean cross-sectional Rank IC (the primary signal-quality metric).
- `rank_ic_std`, and `rank_ic_tstat = rank_ic_mean / (rank_ic_std / sqrt(n_dates))` — the
  Newey-naïve t-stat over the IC time series (significance of the mean IC).
- `rank_ic_hit_rate` — fraction of dates with positive IC.
- Top-k metrics (`compute_topk_metrics`): `top_5/10/20_mean_actual_return`,
  `bottom_10_mean_actual_return`, `top_bottom_10_spread`, and `top_10_hit_rate` (fraction of
  top-10 names beating zero or the date's median realized return).
- Error metrics (`compute_error_metrics`): `rmse`, `mae`, `r2`.

Ranking, not point accuracy, is the contract: cross-sectional Rank IC is what a long-only
top-k portfolio actually monetizes.

### Train-median imputation (leakage-safe)

In `_train_combination`, features are `replace([inf,-inf], nan)`, then imputed with the
**train-fold median only** (`medians = X_train_raw.median(...)`), applied to both train and
test (`fillna(medians).fillna(0.0)`). Test statistics never inform imputation. The policy is
hard-pinned: any `feature_imputation` other than `train_median` raises
`unsupported_feature_imputation_policy`.

### Single-alpha-authority — the agent cannot claim alpha

- Every leaderboard row and the manifest carry `alpha_status = "not_evaluated_by_backtest"`.
- `BaselineCrossSectionalMean` (a per-symbol historical-mean diagnostic) is **never** a
  candidate by default: `_apply_selection_logic` stamps it with the failure reason
  `diagnostic_baseline_only` unless `allow_baseline_candidate` (default false) is set, and
  appends a warning that it "is a symbol historical mean diagnostic baseline, not a
  cross-sectional alpha proof." (`test_baseline_diagnostic_cannot_be_candidate_by_default`.)
- The **signal gate** (`_apply_selection_logic`, config key `signal_gate`) marks a combination
  `candidate_for_backtest` only if it clears every threshold (defaults from code):
  `rank_ic_mean ≥ min_rank_ic_mean (0.01)`, `rank_ic_tstat ≥ min_rank_ic_tstat (1.5)`,
  `top_bottom_10_spread ≥ min_top_bottom_10_spread (0.0)`,
  `prediction_coverage ≥ min_prediction_coverage (0.80)`, and
  `fold_count ≥ min_folds (3)` — and has no `failure_reason`. Among passing rows, exactly one
  is `selected_for_backtest` (highest `composite_score`, tie-broken by `rank_ic_mean`).
- When nothing passes, the manifest documents the negative state explicitly
  (`any_candidate_for_backtest=false`, `backtest_ready=false`,
  `research_status="no_candidate_signal_passed"`, `no_candidate_reason=<gate failures>`). The
  verifier **rejects** a no-selection leaderboard unless this exact no-candidate manifest
  contract is present (`test_model_no_candidate_outputs_pass_verifier_contract`,
  `test_verify_model_rejects_no_selected_without_explicit_no_candidate_manifest`). The gate is
  fail-closed: the *absence* of a signal is a first-class, auditable outcome.

### `PROHIBITED_TOKENS` leakage guard

`_prohibited_feature_columns` flags any column whose lowercased name starts with `y_` or
contains any of `target, label, future, forward, fwd, lead, next_return, ret_fwd` (except
names whitelisted in `features.feature_engineering.ALLOWED_PROHIBITED_EXACT`). Applied twice:
in `prepare()` over the loaded dataset (raises if `fail_on_leakage`), and in
`_select_feature_columns` over candidate features (silently drops). The label columns
(`label_fwd_logret_*d`) are excluded by prefix, so the genuine targets are never used as
inputs (`test_model_excludes_label_target_future_forward_columns_from_features`).

### Feature sets — `market_only` vs `market_plus_onchain` via `ONCHAIN_HINTS`

`_select_feature_columns` starts from numeric, non-metadata, non-`label_` columns; intersects
with `feature_keep_list` when `use_pruned_features` (default true); drops prohibited columns;
and drops `DIAGNOSTIC_FEATURE_COLUMNS` (e.g. `onchain_available`, `market_data_available`,
`is_forward_filled_market`, `onchain_lag_days`) unless `allow_diagnostic_features` (default
false). A column is "on-chain" if its lowercased name contains any `ONCHAIN_HINTS` substring
(`onchain, coinmetrics, defillama, missing_, adr_, tx_count, mvrv, chain_tvl, protocol_tvl,
fees_, dex_volume, current_supply, issuance, market_cap_usd, realized_cap, nvt_`). Then:
- `market_only` → non-on-chain columns; `onchain_only` → on-chain columns;
- `market_plus_onchain` (and any other value) → all candidates.

This drives the on-chain **ablation**: training both `market_only` and `market_plus_onchain`
isolates whether on-chain features add cross-sectional information
(`test_model_runs_market_only_and_full_ablation`).

### `data_content_hash` + gated MLflow logging

`_content_hash` is a deterministic **SHA-256, first 16 hex chars** over the modeling dataset's
content columns (labels + features), excluding the provenance columns (`snapshot_id`,
`run_id`, `created_at_utc`, and their `_label` variants), with `date_ts` UTC-normalized and
rows sorted by `(symbol, date_ts)`. An identical dataset yields an identical fingerprint
(`test_content_hash_deterministic_and_sensitive` checks determinism, feature-change
sensitivity, and provenance-column independence). `_log_to_mlflow` logs tags
(incl. `data_content_hash`, `research_status`, `alpha_status`), params (`selected_model`,
`selected_feature_set`, `selected_horizon_days`, `embargo_days`), metrics (`prediction_rows`,
`fold_count`, `completed_runs`, `best_rank_ic`, `best_rank_ic_tstat`), and the manifest +
leaderboard + quality artifacts. It is gated by `mlflow.log_model_run` (default true) and is
**fully non-fatal** — a missing or failing MLflow degrades to a warning.

---

## 4. Complete config surface (`configs/run_config.yaml → modeling`)

Production defaults (verbatim from the yaml):

```yaml
modeling:
  research_mode: true
  input_path: "data/labels/modeling_dataset.parquet"
  horizons: [7, 14, 30]
  model_names:
    - baseline_cross_sectional_mean          # NOTE: only the baseline is enabled by default
  feature_sets:
    - market_only
    - market_plus_onchain
  default_feature_set: "market_plus_onchain"
  default_model: "lightgbm"
  use_pruned_features: true
  fail_on_empty_output: true
  fail_on_no_valid_folds: true
  fail_on_leakage: true
  min_prediction_rows: 1000
  min_test_symbols_per_date: 10
  random_seed: 42
  walk_forward:
    initial_train_days: 504
    test_days: 30
    step_days: 30
    purge_days: null                          # null → falls back to the horizon
    embargo_days: 30
    min_train_rows: 1000
    min_test_rows: 100
    min_test_symbols: 10
  random_forest:
    n_estimators: 60
    max_depth: 5
    min_samples_leaf: 20
    max_features: 0.5
    n_jobs: -1
  lightgbm:
    n_estimators: 120
    learning_rate: 0.03
    max_depth: 5
    num_leaves: 31
    min_child_samples: 30
    subsample: 0.8
    colsample_bytree: 0.8
    reg_alpha: 0.1
    reg_lambda: 1.0
    objective: "regression"
    n_jobs: -1
    verbose: -1
```

> The RandomForest/LightGBM `n_estimators` defaults in code (`300` / `500`) are **overridden**
> by the yaml above (`60` / `120`) for fast, deterministic runs. The agent reads its
> hyperparameters from these `random_forest:` / `lightgbm:` blocks.

**Keys consumed by the agent but not present in the production `modeling` block** (so the
**code defaults apply**):

| Key | Code default | Effect |
|---|---|---|
| `feature_imputation` | `"train_median"` | Any other value raises `unsupported_feature_imputation_policy` |
| `min_assets_per_prediction_date` | `20` | Drop prediction dates with fewer distinct symbols |
| `allow_diagnostic_features` | `false` | Keep `DIAGNOSTIC_FEATURE_COLUMNS` out of features |
| `allow_baseline_candidate` | `false` | Baseline can never be a backtest candidate |
| `max_symbols` | unset | Optional symbol cap in `prepare()` |
| `signal_gate.min_rank_ic_mean` | `0.01` | Gate threshold |
| `signal_gate.min_rank_ic_tstat` | `1.5` | Gate threshold |
| `signal_gate.min_top_bottom_10_spread` | `0.0` | Gate threshold |
| `signal_gate.min_prediction_coverage` | `0.80` | Gate threshold |
| `signal_gate.min_folds` | `3` | Gate threshold |

There is also a `modeling_smoke` section (merged over `modeling` by tests / smoke runs) that
loosens the walk-forward minimums for tiny synthetic panels. CLI overrides: `ModelAgent(...,
horizon=, model_names=)` (used by `main.py models`) override `horizons` / `model_names`.

`mlflow` section: `tracking_uri: "mlruns"`, `experiment_name: "CHF_experiments"`,
`log_artifacts: true`, `log_model_run: true`.

---

## 5. Verifier (`scripts/verify_model_run.py`)

`validate_model_outputs` asserts: all four core artifacts exist and are non-empty; all 22
required prediction columns are present; no duplicate
`(model_name, feature_set, horizon_days, symbol, date_ts)` rows; `prediction` and
`actual_forward_return` are finite; **`train_end < test_start`** on every prediction row and
**`train_end_purged < test_start`** on every fold; the leaderboard carries `rank_ic_mean`,
`selected_for_backtest`, and the full signal block (`signal_status`, `signal_gate_passed`,
`candidate_for_backtest`, `signal_gate_failure_reason`, `alpha_status`). Selection consistency:
if no row is selected, the manifest **must** explicitly document the no-candidate state
(`alpha_status="not_evaluated_by_backtest"`, `any_candidate_for_backtest=False`,
`backtest_ready=False`, `research_status="no_candidate_signal_passed"`, non-empty
`no_candidate_reason`); if candidates exist, one must be selected; if a model is selected,
`backtest_ready` must be true. Run: `python scripts/verify_model_run.py --section modeling`.

---

## 6. Limitations / what work is left (honest)

- **Only the baseline is enabled in production.** `modeling.model_names` defaults to
  `['baseline_cross_sectional_mean']`. RandomForest and LightGBM are fully coded
  (`_build_model`) and hyperparameter blocks exist, but they are **not enabled by default** —
  the default run produces a diagnostic baseline that, by construction, **cannot** become a
  backtest candidate (`diagnostic_baseline_only`). A default run therefore yields a documented
  `no_candidate_signal_passed` manifest. Enabling the learners requires adding them back to
  `model_names`.
- **SHAP attribution (implemented).** For tree models (`random_forest`, `lightgbm`), the agent
  computes **mean(|SHAP value|) per feature** via `shap.TreeExplainer` and writes it to
  `feature_importance.parquet` as `mean_abs_shap` (with `importance_type` and `shap_folds`),
  alongside the native impurity `importance`. It is **deterministic** (first-N test rows per fold,
  `shap_max_samples`, no sampling RNG), **non-fatal** (degrades to native importance if `shap` is
  absent or the explainer rejects the model), and gated by `modeling.compute_shap` (default
  `true`). Remaining nicety: SHAP **beeswarm/summary plot artifacts** are not yet rendered (the
  numeric attribution is persisted, not the matplotlib figures).
- **No hyperparameter search.** Hyperparameters are **fixed** in config (no Optuna / Bayesian
  search). This is deliberate — fixed seeds (`random_seed: 42`) and fixed hyperparameters keep
  runs deterministic and avoid CV-driven overfitting, at the cost of not finding a
  better-tuned model.
- **No XGBoost / no ensembling / no stacking.** The model menu is baseline + RandomForest +
  LightGBM only; combinations are scored independently and never blended.
- **`ONCHAIN_HINTS` substring brittleness.** Feature-set assignment is purely substring
  matching on lowercased column names. A new on-chain feature whose name contains none of the
  hints is silently classified as `market_only`, and a market feature whose name happens to
  contain a hint substring is misclassified as on-chain — the ablation's market/on-chain split
  is only as correct as the naming convention.
- **Survivor-universe conditioning.** Per the manifest's own `limitations`, results are
  conditional on the latest eligible survivor universe and may overstate historical
  tradability until full point-in-time membership and delisting data are modeled.

---

## 7. How it compares to a hedge fund

**What is already fund-grade — the validation rigor.** The CV design is the part a serious
systematic desk cares most about and would recognize as correct: purged + embargoed
walk-forward with the embargo and purge both widened to at least the label horizon, so no
training label's forward window touches the test period; cross-sectional **Rank IC** (Spearman
by date) with an IC-time-series **t-stat** as the headline metric rather than R²; strict
**train-median** imputation with zero test leakage; a fail-closed **signal gate** with explicit
no-candidate documentation; a deterministic SHA-256 **content hash** plus MLflow run tracking;
and — critically — a **single alpha authority** so the modeling stage can *never* overclaim.
This discipline (leakage-safe by construction, negative results preserved honestly) is exactly
what distinguishes a credible research process from curve-fitting.

**What a real systematic desk adds on top.**
- **Hyperparameter / model search** — Optuna or Bayesian optimization *inside* a nested,
  leakage-respecting CV loop (an outer purged walk-forward for honest OOS, an inner one for
  tuning), instead of fixed config hyperparameters.
- **SHAP attribution in-loop** — per-fold `TreeExplainer` SHAP values for stable, signed,
  per-name feature attribution feeding feature selection — not just global Gini importance.
- **Ensembling / stacking** — blending diverse learners (and horizons) with a meta-learner,
  rather than scoring each combination in isolation.
- **Factor-decay & turnover monitoring** — tracking IC decay over time, signal half-life, and
  cross-sectional crowding, with scheduled re-fits as alpha erodes.
- **Regime-conditioned models** — separate or regime-gated models (vol regimes, trend vs
  mean-reversion, BTC-dominance states) instead of one stationary model across all market
  conditions.
- **Richer objectives & costs** — IC- or rank-aware / monotonic ranking losses, and turnover /
  transaction-cost penalties pushed into model selection rather than only into the downstream
  backtest.

In short: the **measurement** discipline here is at fund standard; the **search,
explainability, and adaptivity** layers a production desk runs on top are intentionally out of
scope, and their absence is the honest gap between this research pipeline and a live
systematic book.
