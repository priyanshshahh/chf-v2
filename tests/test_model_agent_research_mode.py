from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from agents.model_agent import ModelAgent
from configs.config import load_config
from scripts.verify_model_run import validate_model_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    modeling = dict(cfg.get("modeling", {}))
    modeling.update(cfg.get("modeling_smoke", {}))
    modeling["input_path"] = "data/labels/modeling_dataset.parquet"
    modeling["min_prediction_rows"] = 10
    modeling["walk_forward"] = dict(modeling.get("walk_forward", {}))
    modeling["walk_forward"]["min_train_rows"] = 20
    modeling["walk_forward"]["min_test_rows"] = 6
    modeling["walk_forward"]["min_test_symbols"] = 3
    modeling["min_assets_per_prediction_date"] = 3
    cfg["modeling"] = modeling
    return cfg


def _write_model_inputs(tmp_path: Path, *, symbols=None, periods=260):
    symbols = symbols or ["BTC", "ETH", "SOL", "ADA", "UNI"]
    dates = pd.date_range("2025-01-01", periods=periods, freq="D", tz="UTC")
    rows = []
    for s_idx, symbol in enumerate(symbols):
        for i, dt in enumerate(dates):
            base = 0.01 * np.sin(i / 20) + s_idx * 0.001
            rows.append(
                {
                    "date_ts": dt,
                    "symbol": symbol,
                    "snapshot_id": "feat-snap",
                    "run_id": "label-run",
                    "created_at_utc": "2026-04-30T00:00:00+00:00",
                    "log_ret_1d": base,
                    "realized_vol_7d": 0.1 + abs(base),
                    "is_forward_filled_market": 0,
                    "market_data_available": 1,
                    "market_history_days_available": i + 1,
                    "onchain_available": int(symbol in {"BTC", "ETH", "SOL"}),
                    "coinmetrics_available": int(symbol in {"BTC", "ETH"}),
                    "defillama_available": int(symbol in {"ETH", "UNI"}),
                    "tx_count": 100 + i + s_idx,
                    "market_cap_usd": 1000 + 10 * i + s_idx,
                    "feature_set": "full",
                    "feature_version": "full_v1",
                    "onchain_lag_days": 1,
                    "label_fwd_logret_7d": base + 0.01,
                    "label_fwd_logret_14d": base + 0.02,
                    "label_fwd_logret_30d": base + 0.03,
                }
            )
    df = pd.DataFrame(rows)
    out = tmp_path / "data" / "labels"
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "modeling_dataset.parquet", index=False)
    with open(out / "label_manifest.json", "w") as fh:
        json.dump({"recommended_embargo_days": 30}, fh)
    feat = tmp_path / "data" / "features"
    feat.mkdir(parents=True, exist_ok=True)
    with open(feat / "feature_manifest.json", "w") as fh:
        json.dump({"final_kept_feature_count": 5}, fh)
    with open(feat / "feature_keep_list.json", "w") as fh:
        json.dump({"kept_features": ["log_ret_1d", "realized_vol_7d", "is_forward_filled_market", "market_data_available", "market_history_days_available", "onchain_available", "coinmetrics_available", "defillama_available", "tx_count", "market_cap_usd"]}, fh)


def test_model_loads_canonical_modeling_dataset(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)


def test_model_does_not_load_legacy_feature_label_globs(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    legacy = tmp_path / "data" / "cleaned"
    legacy.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"bad": 1}]).to_parquet(legacy / "BTC_ohlcv_clean.parquet", index=False)
    assert ModelAgent(cfg).execute(max_retries=1)


def test_model_excludes_label_target_future_forward_columns_from_features(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    path = tmp_path / "data" / "labels" / "modeling_dataset.parquet"
    df = pd.read_parquet(path)
    df["future_signal"] = 1.0
    df.to_parquet(path, index=False)
    assert not ModelAgent(cfg).execute(max_retries=1)


def test_model_generates_purged_walk_forward_splits(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    folds = pd.read_parquet(tmp_path / "data" / "predictions" / "fold_metrics.parquet")
    assert not folds.empty


def test_model_enforces_embargo(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    folds = pd.read_parquet(tmp_path / "data" / "predictions" / "fold_metrics.parquet")
    assert (pd.to_datetime(folds["train_end_purged"], utc=True) < pd.to_datetime(folds["test_start"], utc=True)).all()


def test_model_generates_oos_predictions_only(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    preds = pd.read_parquet(tmp_path / "data" / "predictions" / "model_predictions.parquet")
    assert (pd.to_datetime(preds["train_end"], utc=True) < pd.to_datetime(preds["test_start"], utc=True)).all()


def test_model_computes_rank_ic_by_date(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    board = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    assert "rank_ic_mean" in board.columns


def test_model_computes_top_bottom_spread(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    board = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    assert "top_bottom_10_spread" in board.columns


def test_model_runs_market_only_and_full_ablation(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    board = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    assert {"market_only", "market_plus_onchain"}.issubset(set(board["feature_set"]))


def test_model_writes_predictions_leaderboard_manifest(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    assert (tmp_path / "data" / "predictions" / "model_predictions.parquet").exists()
    assert (tmp_path / "data" / "predictions" / "model_leaderboard.parquet").exists()
    assert (tmp_path / "data" / "predictions" / "model_manifest.json").exists()


def test_model_no_candidate_outputs_pass_verifier_contract(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    manifest = json.load(open(tmp_path / "data" / "predictions" / "model_manifest.json"))
    board = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    assert manifest["alpha_status"] == "not_evaluated_by_backtest"
    assert manifest["any_candidate_for_backtest"] is False
    assert manifest["backtest_ready"] is False
    assert manifest["research_status"] == "no_candidate_signal_passed"
    assert not board["candidate_for_backtest"].any()
    assert validate_model_outputs(cfg) == []


def test_verify_model_rejects_no_selected_without_explicit_no_candidate_manifest(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    manifest_path = tmp_path / "data" / "predictions" / "model_manifest.json"
    manifest = json.load(open(manifest_path))
    for key in ["any_candidate_for_backtest", "backtest_ready", "research_status", "no_candidate_reason"]:
        manifest.pop(key, None)
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh)
    failures = validate_model_outputs(cfg)
    assert any("no-candidate" in f for f in failures)


def test_baseline_diagnostic_cannot_be_candidate_by_default(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    board = pd.read_parquet(tmp_path / "data" / "predictions" / "model_leaderboard.parquet")
    baseline = board[board["model_name"] == "baseline_cross_sectional_mean"]
    assert not baseline["candidate_for_backtest"].any()
    assert baseline["signal_gate_failure_reason"].astype(str).str.contains("diagnostic_baseline_only").all()


def test_verify_model_rejects_duplicate_predictions(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    path = tmp_path / "data" / "predictions" / "model_predictions.parquet"
    df = pd.read_parquet(path)
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    df.to_parquet(path, index=False)
    failures = validate_model_outputs(cfg)
    assert any("duplicate" in f.lower() for f in failures)


def test_verify_model_rejects_leakage_split(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    assert ModelAgent(cfg).execute(max_retries=1)
    path = tmp_path / "data" / "predictions" / "model_predictions.parquet"
    df = pd.read_parquet(path)
    df.loc[df.index[0], "train_end"] = df.loc[df.index[0], "test_start"]
    df.to_parquet(path, index=False)
    failures = validate_model_outputs(cfg)
    assert any("train_end" in f for f in failures)


def test_model_records_failed_combinations_without_crashing(tmp_path):
    cfg = _cfg(tmp_path)
    _write_model_inputs(tmp_path)
    cfg["modeling"]["feature_sets"] = ["onchain_only"]
    assert ModelAgent(cfg).execute(max_retries=1)
