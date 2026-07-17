"""Offline tests for the advanced ML sophistication layer in ModelAgent.

Reuses the synthetic fixtures from tests/test_model_agent_research_mode.py. Every
test asserts the leakage-safety contract explicitly: the advanced stages
(stacking OOF meta-learner, regime-conditional per-regime models, meta-labeling
second stage) are fit strictly on train-window rows, never on test-window rows.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from agents.label_agent import LabelAgent
from agents.model_agent import ModelAgent
from tests.test_model_agent_research_mode import _cfg, _write_model_inputs
from tests.test_label_agent_research_mode import _cfg as _label_cfg, _write_upstream


def _single_combo_cfg(root: Path, *, model_names, feature_sets=("market_only",), horizons=(7,)):
    cfg = _cfg(root)
    cfg["modeling"]["model_names"] = list(model_names)
    cfg["modeling"]["feature_sets"] = list(feature_sets)
    cfg["modeling"]["horizons"] = list(horizons)
    cfg["modeling"]["compute_shap"] = False  # speed; SHAP tested elsewhere
    return cfg


def _run(cfg) -> bool:
    return ModelAgent(cfg).execute(max_retries=1)


def _load(root: Path):
    preds = pd.read_parquet(root / "data" / "predictions" / "model_predictions.parquet")
    manifest = json.load(open(root / "data" / "predictions" / "model_manifest.json"))
    return preds, manifest


def _write_regime_daily(root: Path, *, start="2025-01-01", periods=260):
    """Two alternating 20-day regime blocks so both regimes are well-populated."""
    dates = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    regimes = ["risk_on" if (i // 20) % 2 == 0 else "risk_off" for i in range(periods)]
    out = root / "data" / "strategies"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date_ts": dates, "regime": regimes}).to_parquet(out / "regime_daily.parquet", index=False)
    return dict(zip(dates.normalize(), regimes))


# --------------------------------------------------------------------------- #
# 1. New base learners: xgboost / catboost build + run
# --------------------------------------------------------------------------- #
def test_xgboost_builds_and_runs(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["xgboost"])
    assert _run(cfg)
    preds, board = _load(tmp_path)
    assert (preds["model_name"] == "xgboost").any()
    lb = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    assert "xgboost" in set(lb["model_name"])
    fi = pd.read_parquet(tmp_path / "data" / "predictions" / "feature_importance.parquet")
    assert (fi["model_name"] == "xgboost").any()  # native importances emitted


def test_catboost_builds_and_runs(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["catboost"])
    assert _run(cfg)
    preds, _ = _load(tmp_path)
    assert (preds["model_name"] == "catboost").any()
    assert preds["prediction"].notna().any()


def test_unknown_model_still_raises_clearly(tmp_path):
    from agents.model_agent import ModelAgent as MA, ModelAgentError
    agent = MA(_single_combo_cfg(tmp_path, model_names=["xgboost"]))
    try:
        agent._build_model("not_a_real_model")
        assert False, "expected ModelAgentError"
    except ModelAgentError as exc:
        assert "unknown_model" in str(exc)


# --------------------------------------------------------------------------- #
# 2. Walk-forward stacking ensemble
# --------------------------------------------------------------------------- #
def test_stacking_produces_predictions_and_train_only_meta_weights(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["stacked_ensemble"])
    cfg["modeling"]["stacking"] = {
        "base_models": ["linear_ridge", "random_forest", "lightgbm"],
        "meta_learner": "ridge",
    }
    assert _run(cfg)
    preds, manifest = _load(tmp_path)
    assert (preds["model_name"] == "stacked_ensemble").any()
    assert preds["prediction"].notna().any()

    combo = "stacked_ensemble|market_only|7"
    assert combo in manifest["stacking"]
    folds = manifest["stacking"][combo]["folds"]
    assert len(folds) >= 1
    non_fallback = [f for f in folds if not f["fallback_equal_weight"]]
    assert non_fallback, "expected at least one fold with a real OOF-fit meta-learner"
    for f in non_fallback:
        # LEAKAGE: OOF base predictions (which the meta-learner is fit on) come only
        # from inside the train slice, so their max date precedes the test window.
        assert pd.Timestamp(f["oof_max_date"]) < pd.Timestamp(f["test_start"])
        assert set(f["meta_weights"]).issubset({"linear_ridge", "random_forest", "lightgbm"})

    # Global leakage guard on the persisted predictions: train_end < test_start.
    assert (pd.to_datetime(preds["train_end"], utc=True) < pd.to_datetime(preds["test_start"], utc=True)).all()


def test_stacking_nnls_meta_learner_runs(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["stacked_ensemble"])
    cfg["modeling"]["stacking"] = {
        "base_models": ["linear_ridge", "random_forest"],
        "meta_learner": "nnls",
    }
    assert _run(cfg)
    _, manifest = _load(tmp_path)
    combo = "stacked_ensemble|market_only|7"
    non_fallback = [f for f in manifest["stacking"][combo]["folds"] if not f["fallback_equal_weight"]]
    assert non_fallback
    for f in non_fallback:
        # NNLS coefficients are non-negative by construction.
        assert all(w >= -1e-9 for w in f["meta_weights"].values())


# --------------------------------------------------------------------------- #
# 3. Regime-conditional models
# --------------------------------------------------------------------------- #
def test_regime_conditional_splits_train_by_regime_no_future_peers(tmp_path):
    _write_model_inputs(tmp_path)
    reg_map = _write_regime_daily(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["random_forest"])
    cfg["modeling"]["regime_conditional"] = {
        "enabled": True,
        "regime_path": "data/strategies/regime_daily.parquet",
        "min_regime_train_rows": 5,
    }
    assert _run(cfg)
    preds, manifest = _load(tmp_path)

    assert "regime_model" in preds.columns
    # Each prediction was produced either by the all-data fallback or by the model
    # for THAT row's own-date regime — never by peers from a different/future regime.
    dates = pd.to_datetime(preds["date_ts"], utc=True).dt.normalize()
    expected_regime = dates.map(reg_map)
    conditional = preds["regime_model"] != "_all_data"
    assert conditional.any(), "expected at least some rows served by a per-regime model"
    assert (preds.loc[conditional, "regime_model"].values == expected_regime[conditional].values).all()

    combo = "random_forest|market_only|7"
    folds = manifest["regime_conditional"][combo]["folds"]
    assert folds and any(f["regime_models_trained"] for f in folds)
    # Per-regime train counts are derived from train rows only; a regime model is
    # trained only when its train-row count clears the threshold.
    for f in folds:
        for reg in f["regime_models_trained"]:
            assert f["train_regime_counts"].get(reg, 0) >= f["min_regime_train_rows"]


# --------------------------------------------------------------------------- #
# 4. Meta-labeling second stage
# --------------------------------------------------------------------------- #
def test_meta_labeling_runs_end_to_end_train_only(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["random_forest"])
    cfg["modeling"]["meta_labeling"] = {"enabled": True, "meta_model": "logistic"}
    assert _run(cfg)
    preds, manifest = _load(tmp_path)

    assert {"meta_prob", "primary_prediction"}.issubset(preds.columns)
    p = pd.to_numeric(preds["meta_prob"], errors="coerce").dropna()
    assert len(p) and ((p >= 0.0) & (p <= 1.0)).all()

    combo = "random_forest|market_only|7"
    folds = manifest["meta_labeling"][combo]["folds"]
    assert folds
    for f in folds:
        # LEAKAGE: the meta-model is trained only on train-window rows; its max train
        # date precedes the test window, so it never sees a test-window label.
        assert pd.Timestamp(f["meta_train_max_date"]) < pd.Timestamp(f["test_start"])
        assert 0.0 <= f["meta_positive_rate_train"] <= 1.0


def test_meta_labeling_sizes_the_primary_signal(tmp_path):
    _write_model_inputs(tmp_path)
    cfg = _single_combo_cfg(tmp_path, model_names=["lightgbm"])
    cfg["modeling"]["meta_labeling"] = {"enabled": True, "meta_model": "logistic"}
    assert _run(cfg)
    preds, _ = _load(tmp_path)
    # prediction == primary_prediction * meta_prob (pure sizing at threshold 0).
    sub = preds.dropna(subset=["primary_prediction", "meta_prob"]).copy()
    expected = sub["primary_prediction"].to_numpy() * sub["meta_prob"].to_numpy()
    assert np.allclose(sub["prediction"].to_numpy(), expected, atol=1e-9)


# --------------------------------------------------------------------------- #
# 4a. LabelAgent triple-barrier label_type (docs/LABELING.md §3a)
# --------------------------------------------------------------------------- #
def test_label_agent_triple_barrier_emits_label_prefixed_columns(tmp_path):
    symbols = ["BTC", "ETH", "SOL", "ADA", "UNI"]
    _write_upstream(tmp_path, symbols=symbols, periods=90, start="2026-01-01")
    cfg = _label_cfg(tmp_path)
    cfg["labels"]["horizons"] = [7, 14]
    cfg["labels"]["label_type"] = "triple_barrier"
    cfg["labels"]["triple_barrier"] = {"pt_sl": [1.0, 1.0], "vertical_barrier_days": 14, "vol_lookback": 20}
    # Remove explicit overrides so the triple-barrier default (= vertical_barrier_days) applies.
    cfg["labels"].pop("recommended_embargo_days", None)
    cfg["labels"].pop("purge_train_test_overlap_days", None)
    assert LabelAgent(cfg).execute(max_retries=1)

    out = tmp_path / "data" / "labels_smoke"
    matrix = pd.read_parquet(out / "label_matrix.parquet")
    modeling = pd.read_parquet(out / "modeling_dataset.parquet")
    manifest = json.load(open(out / "label_manifest.json"))

    # Triple-barrier columns are emitted at event t0, horizon-suffixed, label_-prefixed.
    tb_cols = [c for c in matrix.columns if c.startswith("label_tb_")]
    assert {"label_tb_7d", "label_tb_ret_7d", "label_tb_t1_7d", "label_tb_barrier_7d"}.issubset(set(tb_cols))
    assert any(c.startswith("label_tb_") for c in modeling.columns)
    # Every tb column is label_-prefixed, so ModelAgent keeps them OFF the feature side.
    assert all(c.startswith("label_") for c in modeling.columns if c.startswith("label_tb_"))
    # {-1,0,+1} label domain and a valid barrier type.
    lab = modeling["label_tb_7d"].dropna().unique()
    assert set(int(x) for x in lab).issubset({-1, 0, 1})
    # Embargo/purge cover the vertical barrier so the WF splitter purges the horizon.
    assert manifest["recommended_embargo_days"] == 14
    assert manifest["purge_train_test_overlap_days"] == 14
    assert manifest["label_type"] == "triple_barrier"
