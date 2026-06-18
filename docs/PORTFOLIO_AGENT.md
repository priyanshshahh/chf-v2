# Portfolio Agent — Complete Reference

The Portfolio Agent (`agents/portfolio_agent.py`) is the **portfolio-construction node** of
the CHF pipeline. It consumes the model stage's cross-sectional return forecasts and turns
them into **deterministic, long-only, leakage-safe allocations** that the BacktestAgent can
later evaluate. It selects a model/horizon/feature-set combo (leaderboard-driven), groups
prediction dates into rebalance periods, applies liquidity and positive-signal filters,
builds several weighting schemes (equal-weight, inverse-vol, score-weighted, turnover-
controlled), and writes one canonical allocation panel plus a per-rebalance coverage report
and a provenance manifest.

It is a **transform, not an alpha authority**: every output row carries `alpha_verified=False`,
and the agent stamps an `allocation_mode` and gate flags that tell the BacktestAgent whether
these allocations are a real signal candidate or a diagnostic-only research artifact. Only
`BacktestAgent` may report verified alpha; the PortfolioAgent never does.

---

## 1. Output contract

All artifacts are written under `portfolio.output_dir` (default `data/allocations/`).

| Artifact | Contents |
|---|---|
| `allocations_from_predictions.parquet` | **Canonical** flat allocation panel — the file the backtest and the verifier read |
| `allocations_<strategy_name>.parquet` | Per-strategy copies (one file per distinct `strategy_name`, e.g. `allocations_top_5_equal_weight.parquet`) |
| `allocation_coverage_report.parquet` | Per-rebalance × per-strategy QA: candidate/selected counts, drop reasons, exposures, turnover, QA pass/fail and failure reason |
| `allocation_manifest.json` | Provenance: run/snapshot ids, inputs, selected combo, gate flags, constraints, warnings, limitations, output paths |
| `data_quality_allocations.md` | Human-readable QA tear-sheet (selected combo, mode, per-strategy summary, warnings, limitations) |

### `allocations_from_predictions.parquet` columns

Each row is one asset held in one strategy at one rebalance (built in `_build_rebalances`):

```
date_ts, signal_date, execution_date, symbol, cmc_id,
model_name, horizon_days, feature_set,
predicted_return, prediction_rank, prediction_zscore, signal_score,
side, raw_weight, target_weight, weight, previous_weight,
turnover_contribution, risk_estimate,
rebalance_frequency, strategy_name,
alpha_gate_passed, signal_gate_passed, candidate_for_backtest, alpha_verified,
allocation_mode, snapshot_id, run_id, created_at_utc
```

- `date_ts` is set **equal to `execution_date`** (the tradable date), not the signal date —
  the verifier asserts `date_ts == execution_date`.
- `side` is always `"long"`. `weight` is the final post-cap, post-turnover-control weight;
  `target_weight` is the pre-turnover-control target; `raw_weight` is the unnormalized score
  (1.0 for equal-weight, `1/risk` for vol-scaled, clipped z-score for score strategies).
- `alpha_verified` is **always `False`** in this stage.

### `allocation_coverage_report.parquet` columns

One row per (`signal_date`, `strategy_name`) rebalance, including rebalances that produced no
allocations (with `failure_reason` populated). Built in `_coverage_row`:

```
date_ts, signal_date, execution_date, strategy_name, model_name, horizon_days, feature_set,
candidate_count, selected_count,
dropped_missing_prediction_count, dropped_missing_price_count, dropped_missing_risk_count,
gross_exposure, net_exposure, weight_sum, cash_weight, max_weight_actual, turnover,
alpha_gate_passed, signal_gate_passed, candidate_for_backtest, alpha_verified,
allocation_mode, passed_qa, failure_reason
```

Known `failure_reason` values: `no_next_market_date`, `below_min_assets_per_rebalance`,
`no_selected_assets`, `weight_sum_above_target`, `lookahead_execution` (empty string when
the rebalance passed QA).

### `allocation_manifest.json` keys

`run_id`, `snapshot_id`, `created_at_utc`, `input_prediction_path`, `input_leaderboard_path`,
`input_market_path`, `selected_model_name`, `selected_horizon_days`, `selected_feature_set`,
`strategy_names`, `rebalance_frequency`, `execution_lag_days`, `allocation_rows`,
`rebalance_count`, `unique_symbols_allocated`, `alpha_gate_passed`, `signal_gate_passed`,
`candidate_for_backtest`, `alpha_verified` (always `False`), `allocation_mode`, `max_weight`,
`target_gross_exposure`, `allow_short`, `warnings`, `limitations`, and an `output_files` map.

---

## 2. Research-integrity guards (do not violate)

### Prediction-only inputs — realized returns / labels are rejected

- The agent consumes only the model **predictions** panel
  (`predicted_return` / `prediction` column). On `prepare()`,
  `_validate_prediction_input_columns()` scans every input column name and raises
  `PortfolioAgentError` if any contains a `FORBIDDEN_INPUT_TERMS` substring:
  `actual`, `actual_return`, `actual_forward_return`, `actual_rank`, `label`, `future`,
  `future_return`, `realized`, `realized_return`, `target`, `y_`.
- This is only bypassed if `allow_realized_columns_in_predictions_for_diagnostics=True` is set
  explicitly (a diagnostics escape hatch; off by default). Tests
  `test_no_actual_return_used_for_allocation` (rejection) and
  `test_realized_prediction_columns_allowed_only_for_diagnostics` (opt-in) lock this in.
- The output verifier independently scans for `FORBIDDEN_OUTPUT_COLUMNS`
  (`actual_return`, `label_value`, `future_return`, `realized_return`,
  `actual_forward_return`, `target`, `y`, `y_`) so no leakage column can survive into the
  allocation panel.

### Execution strictly after signal (no same-day look-ahead)

- For each `signal_date`, `execution_date = _next_market_date(signal_ts, execution_lag_days)`
  picks the first market date `>= signal_date + lag` and, when `lag > 0`, strictly greater
  than the signal date. If no such date exists the rebalance is skipped with
  `failure_reason="no_next_market_date"`.
- Execution prices are read at `execution_date` (`_attach_execution_price`); risk/volatility
  is read as-of `signal_date` (`_attach_risk`). Weights are never set from the execution-date
  forward.
- A QA gate (`fail_on_lookahead`, default true) marks any rebalance with
  `execution_date <= signal_date` as `passed_qa=False` (`lookahead_execution`), and `persist()`
  + the verifier both **hard-raise / hard-fail** if any output row has
  `execution_date <= signal_date`. Test: `test_execution_lag_prevents_same_day_lookahead`.

### Liquidity / data-availability + positive-signal filters; long-only

- **Liquidity / availability filters** per rebalance: assets missing an execution-date close
  price are dropped (`dropped_missing_price_count`); for risk-using strategies, assets with no
  valid volatility estimate are dropped (`dropped_missing_risk_count`). A rebalance with fewer
  than `min_assets_per_rebalance` candidates is skipped (`below_min_assets_per_rebalance`).
- **Positive-signal filter**: the `score_*` and `turnover` strategies select only assets with
  `prediction_zscore > min_signal_zscore`, and `normalize_with_cap` clips all raw weights to
  be non-negative.
- **Long-only**: `side` is always `"long"`. When `allow_short=False`, both `persist()` and the
  verifier reject any negative weight. Test:
  `test_score_weighted_long_only_has_no_negative_weights`.

### Gross-exposure / max-weight constraints

- `normalize_with_cap(raw, max_weight, target_sum)` enforces both: total weight cannot exceed
  `target_gross_exposure` and no single weight may exceed `max_weight`. It uses an iterative
  cap-and-redistribute loop and returns any unallocated remainder as `cash_weight`.
- QA gate `fail_on_weight_sum_error` (default true) fails a rebalance whose `weight_sum`
  exceeds `target_gross_exposure`. `persist()` raises if any weight exceeds `max_weight`, and
  the verifier additionally checks per-(date, strategy) gross exposure and that
  `weight_sum + cash_weight` reconciles to `target_gross_exposure` within `1e-4`.

### Allocation mode and gate flags

The agent never claims alpha; it forwards a verdict from the model leaderboard. Set in
`_select_combo()`:

| Situation | `allocation_mode` | `alpha_gate_passed` / `signal_gate_passed` / `candidate_for_backtest` |
|---|---|---|
| Leaderboard model passes `signal_gate_passed & candidate_for_backtest` | `signal_candidate_for_backtest` | `True` |
| Leaderboard exists but no model passes the gate | `diagnostic_not_live_trading` | `False` |
| No leaderboard file present | `leaderboard_missing_diagnostic` | `False` |
| `model_name` override passed to the constructor | `override_diagnostic` | `False` |

All three gate flags are kept identical (all driven by `self._alpha_gate_passed`) and stamped
onto every allocation row, coverage row, and the manifest, alongside the constant
`alpha_verified=False`. A legacy `selected_for_backtest` column (without `signal_gate_passed`)
is downgraded to a diagnostic warning, never treated as an alpha pass.

---

## 3. Lifecycle (`prepare` → `run` → `persist`)

The base `AgentBase.execute()` wraps these three with retries, status tracking, logging, and
registry/snapshot updates — subclasses do not reimplement that lifecycle.

**`prepare()`** — resolve `output_dir`; load the predictions panel (required) and market OHLCV
(required); raise `FileNotFoundError` if either is missing and `PortfolioAgentError` if
predictions are empty. Validate input columns for leakage. Normalize `date_ts` to UTC,
midnight-aligned. Pick the prediction column (`predicted_return`, else `prediction`). Build the
`close` pivot matrix and a rolling-std volatility matrix (`volatility_lookback_days`, min
periods `max(5, min(lookback,10))`). Reject non-positive close prices. Build the symbol→`cmc_id`
map. Load the optional leaderboard and model manifest. Call `_select_combo()` to choose the
model/horizon/feature-set and set `allocation_mode` + gate flags.

**`run()`** — generate a snapshot id; filter predictions to the selected combo
(`_filtered_predictions`, which also drops non-finite predictions, applies `max_symbols`, and
raises on duplicate `date_ts+symbol`). For each configured strategy in `strategy_names`,
`_build_strategy` dispatches to `_build_rebalances`, iterating period-grouped signal dates
(`rebalance_frequency`) and producing weights via `_strategy_weights`. Concatenate per-strategy
allocations and coverage, compute per-strategy summaries (avg turnover, avg cash weight),
populate `metrics` (`allocation_rows`, `rebalance_count`, `unique_symbols_allocated`, gate
flags, `allocation_mode`), build the manifest and the quality markdown. Raises
`PortfolioAgentError` if allocations are empty and `fail_on_empty_allocations` is true.

**`persist(result)`** — re-validate before writing: no duplicate `date_ts+symbol+strategy_name`,
no non-finite weights, no negative weights (long-only), no `execution_date <= signal_date`
(when `fail_on_lookahead`), and no weight above `max_weight`. Write the canonical panel, each
per-strategy panel, the coverage report, the manifest (JSON), and the quality markdown; record
all paths in `output_paths`.

---

## 4. Strategies

Configured via `strategy_names`; the `top_k_*` families expand over `top_k_values`.

| `strategy_name` (config) | Produced files / `strategy_name` values | Weighting (`strategy_type`) |
|---|---|---|
| `top_k_equal_weight` | `top_{k}_equal_weight` for each k | `top_equal` — equal raw weight on the top-k by predicted return |
| `top_k_vol_scaled` | `top_{k}_vol_scaled` for each k | `top_vol` — raw weight `1/risk_estimate` (inverse volatility) on top-k |
| `score_weighted_long_only` | `score_weighted_long_only` | `score` — raw weight = clipped positive `prediction_zscore` |
| `score_weighted_vol_scaled` | `score_weighted_vol_scaled` | `score_vol` — raw weight = positive z-score ÷ risk |
| `turnover_controlled` | `turnover_controlled` | `turnover` — `score_vol` weights with a turnover buffer + cap |

`top_*` strategies may extend past `top_k` (`_extend_for_cap`) up to `max_assets_per_rebalance`
so the `max_weight` cap can be satisfied. `turnover` freezes positions whose target moves less
than `turnover_buffer` and rescales toward previous weights if realized turnover exceeds
`max_turnover_per_rebalance`. All strategies pass through `normalize_with_cap`.

---

## 5. Config surface (`portfolio:` section)

Defaults below are the **actual values** in `configs/run_config.yaml`. The verifier accepts a
`--section` (default `portfolio`); the `portfolio_smoke` and `portfolio_alpha_candidates`
sections merge over this base for those runs.

| Key | Default | Meaning |
|---|---|---|
| `research_mode` | `true` | Research-mode marker for the section |
| `prediction_path` | `data/predictions/model_predictions.parquet` | Predictions input (alias `predictions_path` accepted) |
| `leaderboard_path` | `data/predictions/model_leaderboard.parquet` | Optional model leaderboard for combo selection |
| `market_path` | `data/raw/market/market_ohlcv.parquet` | OHLCV for execution prices, risk, calendar, `cmc_id` |
| `output_dir` | `data/allocations` | Where all artifacts are written |
| `model_selection` | `best_available` | Selection strategy; only `best_available` activates leaderboard ranking |
| `fallback_model` | `baseline_cross_sectional_mean` | Model used when no leaderboard combo is eligible |
| `fallback_feature_set` | `market_only` | Feature set for fallback / override |
| `horizon_days` | `14` | Target forecast horizon to select |
| `rebalance_frequency` | `W` | Period grouping: `W` (weekly), `2W` (14-day blocks), `M` (monthly), else daily |
| `execution_lag_days` | `1` | Market-day lag from signal to execution (must be > 0 to enforce no same-day fill) |
| `strategy_names` | `[top_k_equal_weight, top_k_vol_scaled, score_weighted_long_only, score_weighted_vol_scaled, turnover_controlled]` | Strategies to build |
| `top_k_values` | `[5, 10, 20]` | k values for the `top_k_*` families |
| `max_weight` | `0.15` | Per-asset cap (alias `max_position_weight`) |
| `min_weight` | `0.0` | Declared min weight (positivity is enforced via the cap/clip) |
| `target_gross_exposure` | `1.0` | Total weight budget; remainder is `cash_weight` |
| `target_net_exposure` | `1.0` | Declared net target (long-only ⇒ net == gross) |
| `allow_short` | `false` | When false, negative weights are rejected at persist and verify |
| `long_only` | `true` | Long-only marker |
| `volatility_lookback_days` | `30` | Rolling window for the risk estimate |
| `min_assets_per_rebalance` | `5` | Minimum candidates/selected for a valid rebalance |
| `max_assets_per_rebalance` | `25` | Hard cap on positions per rebalance |
| `min_prediction_count_per_date` | `5` | Declared minimum prediction count per date |
| `turnover_buffer` | `0.02` | Freeze threshold for `turnover_controlled` |
| `max_turnover_per_rebalance` | `0.50` | Turnover cap for `turnover_controlled` |
| `min_signal_zscore` | `0.0` | Min `prediction_zscore` for score/turnover selection |
| `fail_on_empty_allocations` | `true` | Raise if no allocations are produced |
| `fail_on_weight_sum_error` | `true` | Fail QA if `weight_sum > target_gross_exposure` |
| `fail_on_lookahead` | `true` | Raise / fail QA on `execution_date <= signal_date` |

Additional keys read by the code but not present in the default `portfolio:` block: `max_symbols`
(cap on distinct symbols — set in `portfolio_smoke`),
`allow_realized_columns_in_predictions_for_diagnostics` (default false; the leakage-guard escape
hatch). Constructor overrides `model_name` and `horizon` bypass leaderboard selection and force
`override_diagnostic` mode.

---

## 6. Verifier and tests

`scripts/verify_portfolio_run.py` (`validate_portfolio_outputs`) re-derives every contract from
disk: artifacts exist and are non-empty, required allocation/coverage columns present, no
duplicate `date_ts+symbol+strategy_name`, finite weights, no negative weights (long-only), no
weight above `max_weight`, per-(date, strategy) gross exposure within `target_gross_exposure`,
`weight_sum + cash_weight` reconciles to the target, `execution_date > signal_date`,
`date_ts == execution_date`, no forbidden label/target columns, every QA-passing coverage row
actually passed, `selected_count >= min_assets_per_rebalance` on passing rebalances, manifest
`allocation_rows` matches the panel, and (when market is available) every allocation lands on a
date/symbol that has a market close. The research-integrity test suite is
`tests/test_portfolio_agent_research_mode.py` (22 tests covering combo selection, the alpha-gate
fallback, each strategy's weighting/cap behavior, missing-price/missing-risk handling, the
realized-column rejection, and four verifier-rejection cases).

---

## 7. Limitations / work remaining (honest)

- **No alpha proof.** The agent only transforms forecasts into allocations. Whether those
  allocations beat benchmarks after transaction costs is decided solely by `BacktestAgent`; the
  manifest and quality doc state this explicitly. Every output carries `alpha_verified=False`.
- **Survivorship.** Allocations are conditional on the latest eligible survivor universe.
  Because full point-in-time membership and delisting data are not yet modeled, historical
  tradability may be overstated (stated in the manifest `limitations`).
- **No transaction costs at this stage.** Turnover is measured (`turnover_contribution`,
  `turnover`) and bounded in one strategy, but no cost is charged; costs are applied downstream
  in the backtest, not optimized here.
- **No covariance/risk model.** "Risk" is a single per-asset rolling return std
  (`volatility_lookback_days`); there is no correlation matrix, factor model, or portfolio-level
  risk budget. Inverse-vol weighting ignores cross-asset correlation.
- **Heuristic, non-optimizing construction.** Weights come from rank/score heuristics plus a
  cap-and-redistribute normalizer — there is no objective-function optimizer, no shrinkage, and
  no explicit turnover-vs-expected-return trade-off (turnover control is a buffer/cap, not an
  optimizer term).
- **Long-only, single-asset-class, no leverage/borrow.** Gross ≤ 1.0, no shorts, no financing,
  no cash-rate modeling. Residual budget simply becomes idle `cash_weight`.
- **Liquidity filter is availability-based.** Assets are dropped for missing price/risk, but
  there is no ADV-based participation cap or position-size-vs-liquidity constraint.

---

## 8. How this compares to a hedge-fund desk (honest)

This is a **research portfolio-construction layer**, not a production trading desk.

| Dimension | CHF PortfolioAgent | A real quant desk |
|---|---|---|
| Weighting | Rank/score heuristics + cap-and-redistribute normalizer | Mean-variance / risk-parity / Black-Litterman optimizer over an objective |
| Risk model | Single per-asset rolling vol | Full covariance / multi-factor risk model with shrinkage |
| Costs | Measured turnover, no cost charged at this stage | Transaction-cost-aware optimization (impact, spread, fees) inside the objective |
| Sides / leverage | Long-only, gross ≤ 1.0, no leverage | Long/short with borrow availability and locate, financing, leverage targets |
| Constraints | Per-name cap, gross cap, min/max names | Sector/factor/beta neutrality, ADV participation, concentration, drawdown limits |
| Execution | Next-market-day close, single fill | Scheduling/VWAP-TWAP, venue routing, slippage modeling |
| Rebalance logic | Fixed calendar (W/2W/M) + turnover buffer | Trade-vs-hold band optimization, alpha-decay-aware rebalancing |

The deliberate simplicity preserves the project's central integrity constraint: keep the
construction step transparent and leakage-safe so the backtest's **negative** headline result
(`alpha_verified=false`) cannot be confounded by optimizer overfitting or hidden look-ahead.
