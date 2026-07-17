from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agents.model_agent import ModelAgent
from tests.test_model_agent_research_mode import _cfg, _write_model_inputs

COMBO_KEY = "random_forest|market_only|7"


def _optuna_cfg(root: Path, *, optuna_block) -> dict:
    """Small deterministic config: single model / feature set / horizon (smoke: 7d)."""
    cfg = _cfg(root)
    cfg["modeling"]["model_names"] = ["random_forest"]
    cfg["modeling"]["feature_sets"] = ["market_only"]
    cfg["modeling"]["compute_shap"] = False
    if optuna_block is None:
        cfg["modeling"].pop("optuna", None)
    else:
        cfg["modeling"]["optuna"] = optuna_block
    return cfg


def _run(root: Path, optuna_block) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    _write_model_inputs(root)
    cfg = _optuna_cfg(root, optuna_block=optuna_block)
    assert ModelAgent(cfg).execute(max_retries=1)
    return cfg


def _load_outputs(root: Path):
    preds = pd.read_parquet(root / "data" / "predictions" / "model_predictions.parquet")
    manifest = json.load(open(root / "data" / "predictions" / "model_manifest.json"))
    return preds, manifest


def _comparable(preds: pd.DataFrame) -> pd.DataFrame:
    # snapshot_id / run_id are per-run provenance, not behavior
    return preds.drop(columns=["snapshot_id", "run_id"], errors="ignore").reset_index(drop=True)


def test_optuna_disabled_behavior_unchanged(tmp_path):
    """enabled=false must be byte-identical to a config with no optuna block at all."""
    _run(tmp_path / "no_block", None)
    _run(
        tmp_path / "disabled",
        {"enabled": False, "n_trials": 25, "models": ["random_forest"], "objective_metric": "rank_ic_mean"},
    )
    preds_a, manifest_a = _load_outputs(tmp_path / "no_block")
    preds_b, manifest_b = _load_outputs(tmp_path / "disabled")
    pd.testing.assert_frame_equal(_comparable(preds_a), _comparable(preds_b))
    assert manifest_a["optuna_best_params"] == {}
    assert manifest_b["optuna_best_params"] == {}
    assert manifest_b["optuna"]["enabled"] is False


def test_optuna_enabled_records_best_params_and_never_touches_test_window(tmp_path):
    _run(
        tmp_path,
        {"enabled": True, "n_trials": 2, "models": ["random_forest"], "objective_metric": "rank_ic_mean"},
    )
    preds, manifest = _load_outputs(tmp_path)
    assert manifest["optuna"]["enabled"] is True
    assert COMBO_KEY in manifest["optuna_best_params"]
    entry = manifest["optuna_best_params"][COMBO_KEY]
    # best params recorded with the RF search-space keys and a finite objective value
    assert {"n_estimators", "max_depth", "min_samples_leaf", "max_features"}.issubset(entry["best_params"])
    assert isinstance(entry["best_value"], float)
    assert entry["n_trials"] == 2
    assert entry["objective_metric"] == "rank_ic_mean"
    assert entry["inner_fold_count"] >= 1
    # LEAKAGE SAFETY: tuning data strictly precedes every outer test window
    tuning_max = pd.Timestamp(entry["tuning_data_max_date"])
    first_test_start = pd.Timestamp(entry["outer_first_test_start"])
    assert tuning_max < first_test_start
    assert tuning_max < pd.to_datetime(preds["test_start"], utc=True).min()


def test_optuna_enabled_is_deterministic(tmp_path):
    block = {"enabled": True, "n_trials": 2, "models": ["random_forest"], "objective_metric": "rank_ic_mean"}
    _run(tmp_path / "a", block)
    _run(tmp_path / "b", block)
    _, manifest_a = _load_outputs(tmp_path / "a")
    _, manifest_b = _load_outputs(tmp_path / "b")
    assert manifest_a["optuna_best_params"][COMBO_KEY]["best_params"] == manifest_b["optuna_best_params"][COMBO_KEY]["best_params"]
    preds_a, _ = _load_outputs(tmp_path / "a")
    preds_b, _ = _load_outputs(tmp_path / "b")
    pd.testing.assert_frame_equal(_comparable(preds_a), _comparable(preds_b))


def test_optuna_skips_models_not_in_scope(tmp_path):
    """Models outside optuna.models keep their static hyperparameters (no study run)."""
    _run(
        tmp_path,
        {"enabled": True, "n_trials": 2, "models": ["lightgbm"], "objective_metric": "rank_ic_mean"},
    )
    _, manifest = _load_outputs(tmp_path)
    assert manifest["optuna"]["enabled"] is True
    assert manifest["optuna_best_params"] == {}
