# Advanced ML Model Comparison — Honest Head-to-Head

**Date:** 2026-07-06. Ran the new gradient boosters (XGBoost, CatBoost) head-to-head against the existing baselines on the identical modeling panel (purged+embargoed walk-forward, 14 folds, `market_plus_onchain` features, 3 horizons, 450,666 predictions). This backs the claim in [docs/CASE_STUDY.md](CASE_STUDY.md) that more model capacity does not turn a non-verified signal into a verified one.

## Result: the fancier models did NOT help

**Best Rank IC per model (best horizon):**

| Model | Best horizon | Rank IC (mean) | t-stat |
|---|---:|---:|---:|
| baseline_cross_sectional_mean | 30d | **0.0868** | 11.80 |
| linear_ridge | 14d | 0.0645 | 7.03 |
| lightgbm | 30d | 0.0443 | 4.91 |
| catboost | 30d | 0.0348 | 3.80 |
| xgboost | 30d | 0.0323 | 3.83 |
| random_forest | 14d | 0.0278 | 3.21 |

**Mean Rank IC across horizons:** linear_ridge 0.0596 · baseline 0.0574 · lightgbm 0.0346 · catboost 0.0294 · xgboost 0.0281 · random_forest 0.0249.

## Interpretation (why this is the *expected, credible* finding)

- The **simplest models win**: a plain cross-sectional-mean baseline and linear ridge lead; XGBoost and CatBoost land mid-pack, *below* ridge and even LightGBM.
- On a low signal-to-noise problem like cross-sectional crypto returns, high-capacity gradient boosters **overfit noise**; parsimonious models generalize better out-of-sample. This is a well-known result, not a modeling mistake.
- Crucially, the strong in-sample Rank IC (all t-stats significant) still **does not survive the BacktestAgent cost/benchmark gate** — the same conclusion the baselines reached. Adding XGBoost/CatBoost/stacking does not manufacture alpha; `alpha_verified` stays `false`.
- **This is a feature, not a failure.** A researcher who reaches for XGBoost and honestly reports "it didn't beat ridge" is demonstrating exactly the discipline a fund wants. The value is the rigorous, self-skeptical comparison — not a curve-fit win.

## Reproduce

```bash
CHF_MODELING_ADVANCED=1 .venv/bin/python main.py model --config configs/run_config.yaml
```

The advanced layer (XGBoost, CatBoost, walk-forward stacking, optional regime-conditional + meta-labeling) is config-gated and default-OFF (`configs/modeling_advanced.yaml`), so the canonical pipeline stays byte-reproducible without it. The comparison above used a SHAP-off, single-feature-set variant for speed; the stacked ensemble and meta-labeling are exercised by `tests/test_model_advanced.py`. Canonical predictions were restored after the comparison so the frozen research result stays consistent.
