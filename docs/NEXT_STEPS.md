# What To Do Next — CHF Pipeline Owner Backlog

This is an actionable, prioritized backlog grounded in the current code and
`configs/run_config.yaml`. Every recommendation cites the real config key, file
path, or command it touches. No performance numbers are invented here — the
headline result is the deliberate **negative** result `alpha_verified=false`,
and nothing below should be done in a way that softens or overstates it.

---

## 1. Current state

The CHF pipeline is leakage-safe by construction (purged + embargoed walk-forward
CV, prediction-only portfolio inputs, single alpha authority in `BacktestAgent`)
and reproducible (deterministic seeds, content-hashed runs). The headline
research result is a **deliberate negative**: `BacktestAgent` reported
`alpha_verified=false` for every individually tested candidate
(`docs/RESEARCH_RESULTS_SUMMARY.md`, `docs/LIMITATIONS_AND_NEXT_STEPS.md`). The
Model and Backtest agents were recently hardened: both now emit a deterministic
`data_content_hash` (`agents/model_agent.py::_content_hash`,
`agents/backtest_agent.py::_content_hash`), MLflow logging is gated and non-fatal
(`mlflow.log_model_run` / `log_backtest_run` in `configs/run_config.yaml`, guarded
in `_log_to_mlflow`), and `BacktestAgent` now runs subperiod robustness
(`backtesting.subperiod_analysis: true`, `subperiod_count: 3`). Preserving the
integrity of the negative result is the central constraint for everything below.

---

## 2. Do this next (ordered backlog)

### [1] Enable real ML models for a production model run — RESULT-AFFECTING
- **What:** `modeling.model_names` in `configs/run_config.yaml` currently defaults
  to **only** `['baseline_cross_sectional_mean']`. Add `random_forest` and
  `lightgbm` so the canonical `python main.py model` run actually tests ML signal
  rather than just the symbol-mean baseline.
- **Why:** The frozen pipeline run only fits the baseline in the `modeling`
  section. The ML models exist (`agents/model_agent.py::_build_model`, hyperparams
  already defined under `modeling.random_forest` / `modeling.lightgbm`) and are
  exercised in `alpha_research`, but the `model` stage proper does not run them by
  default. Until they are added, `data/predictions/model_predictions.parquet` is
  baseline-only.
- **Where:** `configs/run_config.yaml` → `modeling.model_names`. The model build
  switch is `agents/model_agent.py` lines ~314–. `modeling_smoke.model_names`
  already includes `random_forest` as a reference.
- **Effort:** ~15 min config change + one full re-run of `model → portfolio →
  backtest`. Compute: minutes-to-low-tens-of-minutes on the current dataset.
- **INTEGRITY FLAG:** This changes what the frozen result represents. If you do
  this, re-run *all* downstream stages and re-verify with `BacktestAgent` — do
  **not** hand-edit `docs/RESEARCH_RESULTS_SUMMARY.md`. The negative result must
  still come out of `BacktestAgent`, not from narrative.

### [2] SHAP TreeExplainer attribution — ✅ DONE
- **Status:** Implemented. For tree models (`random_forest` / `lightgbm`) the
  ModelAgent computes **mean(|SHAP value|) per feature** via `shap.TreeExplainer`
  and persists it to `data/predictions/feature_importance.parquet` as
  `mean_abs_shap` (plus `importance_type` and `shap_folds`), alongside the native
  `importance`. Deterministic (first-N test rows per fold via
  `modeling.shap_max_samples`), non-fatal (degrades to native importance if `shap`
  is absent), gated by `modeling.compute_shap` (default `true`). Covered by
  `test_random_forest_emits_shap_feature_importance` and `test_shap_can_be_disabled`.
- **Remaining nicety (optional):** render SHAP **beeswarm/summary plot** image
  artifacts (matplotlib) in addition to the persisted numeric attribution.

### [3] Add Optuna hyperparameter search
- **What:** Replace the fixed deterministic hyperparameters under
  `modeling.random_forest` / `modeling.lightgbm` with an optional Optuna study
  that searches within the leakage-safe walk-forward CV.
- **Why:** `optuna>=3.0.0` is already in `requirements.txt` (installed, unused).
  Current hyperparams are hand-fixed (e.g. `lightgbm.n_estimators: 120`,
  `learning_rate: 0.03`, `max_depth: 5`). A bounded study would test whether the
  negative result is sensitive to tuning.
- **Where:** New search hook in `agents/model_agent.py::_build_model` / training
  loop, driven by a new `modeling.optuna` config block (e.g. `enabled`,
  `n_trials`, search ranges). **Critical:** the objective must score on the
  **purged + embargoed walk-forward folds** (`models/walk_forward.py`), never on a
  naive split, or you reintroduce leakage. Keep `random_seed: 42` honored for
  reproducibility.
- **Effort:** 1–2 days incl. leakage-safety review. Compute scales with `n_trials`
  — budget decision needed (see §3).

### [4] Resolve the `ONCHAIN_HINTS` substring brittleness
- **What:** The `market_only` vs `market_plus_onchain` feature split is decided by
  substring matching against the `ONCHAIN_HINTS` tuple
  (`agents/model_agent.py` lines 29–46, used in `is_onchain` at ~line 299). Hints
  like `"fees_"`, `"missing_"`, `"market_cap_usd"` are fragile: a renamed or new
  feature can silently land in the wrong bucket, mislabeling a `market_only` run as
  containing on-chain inputs (or vice-versa).
- **Why:** This directly affects which feature set a candidate belongs to, and the
  negative result is partly framed as "market-only signals led this run"
  (`docs/LIMITATIONS_AND_NEXT_STEPS.md`). A misclassification corrupts that claim.
- **Where:** Replace substring heuristics with an explicit source tag carried from
  the feature store. The feature versions already exist in config
  (`features.feature_versions.market: market_v1`, `onchain: onchain_v1`); plumb a
  per-column `source` / origin label from `features/` through the modeling dataset
  so `_select_feature_columns` filters on metadata, not name substrings.
- **Effort:** ~1 day (touches feature-store schema + model agent selection). Add a
  test asserting no column is ambiguously classified.

### [5] Point-in-time / survivorship: full historical membership + delisting
- **What:** Move the production universe from latest-survivor baseline to full
  point-in-time membership with delisting modeling, then re-run the canonical
  pipeline and compare before/after.
- **Why:** Current production results are "conditional on the latest eligible
  survivor universe and may overstate historical tradability"
  (`docs/RESEARCH_RESULTS_SUMMARY.md`, `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`).
  This is the single biggest validity caveat.
- **Concrete unblock path (already partially built):**
  1. The CMC Pro `/v1/cryptocurrency/listings/historical` endpoint remains
     **400-blocked** on the Hobbyist plan (1-month window only) — see
     `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`. Do **not** fake PIT listings from
     current rankings.
  2. The **keyless** public data-API
     (`api.coinmarketcap.com/data-api/v3/.../listings/historical`) returns true
     top-N as of any date incl. delisted coins, and is already ingested by
     `scripts/build_cmc_web_history.py`, consumed via `universe.source:
     cmc_web_pit` (default `auto` prefers it when present at
     `universe.cmc_web_dataset_path`).
  3. Build the daily membership mask:
     `python scripts/build_membership_daily.py --end 2026-06-30`.
  4. Run the survivorship-free handoff sections end-to-end:
     `python main.py market --section market_data_pit`,
     `python main.py onchain --section onchain_pit`,
     `python main.py features --section features_pit`,
     `python main.py labels --section labels_pit`. These sections set
     `universe_membership_mode: union_full_history` and `require_pit_membership:
     true`, with PIT-appropriate (lower) coverage floors.
  5. **Documented residual caveat:** free-data coverage tops out at ~235/320 union
     coins (`market_data_pit.maximum_failed_assets_allowed: 95`) — truly-dead and
     exchange-native tokens lack free OHLCV. Keep that disclosed; membership is
     survivorship-free even where price data is not.
- **Effort:** Large (multi-hour ingest, re-run of all stages, before/after
  writeup). Network/rate-limit bound, not CPU bound.

### [6] Live API keys — keyless vs key-required, and how to verify
- **Keyless (work without keys, per `docs/API_KEYS_AND_DATA_SOURCES.md`):**
  CoinGecko, CoinPaprika, CryptoCompare (market/universe waterfall),
  Coinbase/Kraken/KuCoin/Gemini public OHLCV, **CoinMetrics Community**
  (`community-api.coinmetrics.io/v4`), **DeFiLlama** (`api.llama.fi`). The
  survivorship-free PIT universe source (`cmc_web_pit`) is also keyless.
- **Optional enrichment (need keys, only used `only_if_key_present`):**
  `ETHERSCAN_API_KEY` (on-chain enrichment), `GRAPH_API_KEY` / `THEGRAPH_API_KEY`
  (The Graph — also needs `onchain.thegraph.configured_subgraphs`),
  `DUNE_API_KEY` (needs `onchain.dune.query_ids`). `onchain.blockchair`/`dune` are
  `enabled: false` by default.
- **Provider-mode only:** `CMC_API_KEY` / `COINMARKETCAP_API_KEY` — needed only
  for CMC Pro provider modes; **not** required for the verified baseline or the
  keyless PIT path.
- **How to verify:**
  ```bash
  python scripts/probe_api_readiness.py --config configs/run_config.yaml
  ```
  Reports each key as present/missing/masked (never prints secrets) and probes
  CoinMetrics + DeFiLlama liveness. Outputs `docs/API_DATA_READINESS_AUDIT.md` and
  `data/readiness/api_probe_results.json`.
- **Effort:** Minutes (just to verify). Keys are local-only in `.env` (never
  commit).

### [7] Optional: QuantStats tear sheets + vectorbt cross-check
- **QuantStats tear sheets:** Generate HTML/PDF tear sheets from
  `BacktestAgent` net-return equity curves for richer reporting. Note:
  `quantstats` is **not** currently in `requirements.txt` — add it as an optional
  dependency and import it gated/non-fatal (same graceful-degradation pattern as
  vectorbt). Reporting only; must not feed back into alpha verification.
- **vectorbt `from_orders` cross-check:** `vectorbt>=0.26.0` is in
  `requirements.txt` and imported gated in `agents/backtest_agent.py`
  (`_VBT_AVAILABLE`), but the alpha verdict runs on the in-house return engine
  (`_perf_from_returns`). Add an optional `Portfolio.from_orders` reconciliation
  that asserts the two engines agree within tolerance — a correctness check on the
  net-return math, **not** a second alpha authority. `BacktestAgent` must remain
  the sole alpha authority.
- **Effort:** 0.5–1 day each, both optional/low priority.

---

## 3. What I need from you (decisions/inputs)

1. **Enable ML models in the frozen result? (blocks [1])** This re-runs and
   re-verifies the canonical result. Confirm you want the headline to reflect
   `random_forest` + `lightgbm`, not baseline-only — and accept that the verdict
   is whatever `BacktestAgent` returns (still expected negative).
2. **Compute budget for Optuna ([3]).** How many trials / how long per model? This
   sets `modeling.optuna.n_trials` and run wall-clock.
3. **API keys.** Do you want any optional enrichment keys set in `.env`
   (`ETHERSCAN_API_KEY`, `GRAPH_API_KEY`, `DUNE_API_KEY`)? Baseline + PIT paths
   need none. Confirm before I wire The Graph subgraphs / Dune query IDs.
4. **PIT re-run sign-off ([5]).** Approve running the `*_pit` sections as the new
   production universe and replacing the latest-survivor baseline, with the
   ~235/320 coverage residual disclosed as a caveat.
5. **Reporting scope ([7]).** Add `quantstats` as a dependency? Yes/no.

---

## 4. How to verify after each change

Every stage has a matching verifier under `scripts/verify_<stage>_run.py`. Run the
verifier for the stage you touched, then the offline + full suites:

- **Per-stage verifiers** (available: `verify_universe_run.py`,
  `verify_market_run.py`, `verify_onchain_run.py`, `verify_feature_run.py`,
  `verify_label_run.py`, `verify_model_run.py`, `verify_portfolio_run.py`,
  `verify_backtest_run.py`, `verify_alpha_research_run.py`):
  ```bash
  python scripts/verify_model_run.py       # after [1],[2],[3],[4]
  python scripts/verify_backtest_run.py    # after [1],[5],[7]
  python scripts/verify_feature_run.py     # after [4],[5]
  ```
- **Offline end-to-end smoke** (no API keys):
  ```bash
  make smoke
  ```
- **Full test suite** — the `*_research_mode.py` tests are the research-integrity
  guard; treat failures there as correctness failures, not flakiness:
  ```bash
  make test
  ```
- **Syntax check** after edits:
  ```bash
  python -m py_compile main.py agents/*.py models/*.py scripts/*.py
  ```
- **Re-verify the alpha verdict** after any change to models, features, universe,
  or backtest: re-run `model → portfolio → backtest` and read the verdict from
  `BacktestAgent`'s manifest (`alpha_verified`). Do not edit the result docs by
  hand.
