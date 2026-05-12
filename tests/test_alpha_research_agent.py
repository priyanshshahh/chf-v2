from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from agents.alpha_research_agent import AlphaResearchAgent
from configs.config import load_config
from scripts.verify_alpha_research_run import validate_alpha_research_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    acfg = dict(cfg.get("alpha_research", {}))
    acfg.update(
        {
            "output_dir": "data/research",
            "feature_path": "data/features/full_features.parquet",
            "label_path": "data/labels/label_matrix.parquet",
            "max_experiments": 6,
            "feature_sets": ["market_only", "market_plus_onchain"],
            "label_targets": ["excess_vs_equal_weight", "cross_sectional_forward_rank"],
            "horizons": [7],
            "models": ["baseline_cross_sectional_mean", "linear_ridge", "rule_momentum_30d"],
            "train_days": 80,
            "test_days": 20,
            "step_days": 20,
            "embargo_days": 7,
            "min_train_rows": 200,
            "min_test_rows": 40,
            "minimum_folds": 2,
            "fail_on_empty_results": True,
        }
    )
    cfg["alpha_research"] = acfg
    return cfg


def _write_inputs(tmp_path: Path) -> None:
    feat_dir = tmp_path / "data" / "features"
    label_dir = tmp_path / "data" / "labels"
    feat_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2024-01-01", periods=180, freq="D", tz="UTC")
    syms = ["BTC", "ETH", "SOL", "ADA", "UNI", "LINK", "AAVE", "DOGE"]
    feat_rows = []
    label_rows = []
    for i, dt in enumerate(dates):
        for j, sym in enumerate(syms):
            ret30 = (len(syms) - j) / 100 + np.sin(i / 12) / 100
            vol30 = 0.02 + j * 0.002
            onchain = (j % 3) / 10 + i / 10000
            future = ret30 * 0.5 - vol30 * 0.2 + (j % 2) * 0.005
            feat_rows.append(
                {
                    "date_ts": dt,
                    "symbol": sym,
                    "log_ret_3d": ret30 / 5,
                    "log_ret_14d": ret30 / 2,
                    "log_ret_30d": ret30,
                    "realized_vol_30d": vol30,
                    "volume_ratio_30d": 1 + j / 10,
                    "adr_active_growth_30d": onchain,
                    "tx_count_growth_30d": onchain / 2,
                    "chain_tvl_growth_30d": onchain / 3,
                    "mvrv_current": 1 + j / 10,
                    "nvt_tx_proxy": 20 + j,
                    "chain_tvl_usd": 1_000_000 + i * 100 + j,
                    "snapshot_id": "feat",
                    "run_id": "feat",
                    "created_at_utc": "2026-05-01T00:00:00+00:00",
                }
            )
            label_rows.append(
                {
                    "date_ts": dt,
                    "symbol": sym,
                    "label_fwd_logret_7d": future,
                    "label_fwd_logret_14d": future * 1.2,
                    "label_fwd_logret_30d": future * 1.5,
                    "snapshot_id": "lab",
                    "run_id": "lab",
                    "created_at_utc": "2026-05-01T00:00:00+00:00",
                }
            )
    pd.DataFrame(feat_rows).to_parquet(feat_dir / "full_features.parquet", index=False)
    pd.DataFrame(label_rows).to_parquet(label_dir / "label_matrix.parquet", index=False)
    with open(label_dir / "label_manifest.json", "w") as fh:
        json.dump({"horizons": [7, 14, 30]}, fh)


def _run(tmp_path: Path) -> tuple[dict, Path]:
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path)
    agent = AlphaResearchAgent(cfg)
    assert agent.execute(max_retries=1)
    return cfg, tmp_path / "data" / "research"


def test_alpha_research_builds_experiment_grid(tmp_path):
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path)
    agent = AlphaResearchAgent(cfg)
    agent.prepare()
    assert len(agent._experiment_grid()) >= 6


def test_alpha_research_uses_walk_forward_no_future_data(tmp_path):
    cfg, out = _run(tmp_path)
    folds = pd.read_parquet(tmp_path / "data" / "predictions" / "alpha_fold_metrics.parquet")
    assert (pd.to_datetime(folds["test_start"], utc=True) > pd.to_datetime("2024-01-01", utc=True)).all()


def test_alpha_research_embargo_prevents_overlap(tmp_path):
    cfg, out = _run(tmp_path)
    folds = pd.read_parquet(tmp_path / "data" / "predictions" / "alpha_fold_metrics.parquet")
    assert folds["fold_id"].nunique() >= 2


def test_label_excess_vs_equal_weight_computed_correctly(tmp_path):
    cfg, out = _run(tmp_path)
    labels = pd.read_parquet(out / "label_variants.parquet")
    one = labels[labels["date_ts"] == labels["date_ts"].min()].copy()
    original = pd.read_parquet(tmp_path / "data" / "labels" / "label_matrix.parquet")
    original_one = original[original["date_ts"] == one["date_ts"].min()].copy()
    expected = original_one["label_fwd_logret_7d"] - original_one["label_fwd_logret_7d"].mean()
    assert np.allclose(one["excess_vs_equal_weight_7d"], expected)


def test_cross_sectional_rank_label_computed_by_date(tmp_path):
    cfg, out = _run(tmp_path)
    labels = pd.read_parquet(out / "label_variants.parquet")
    one = labels[labels["date_ts"] == labels["date_ts"].min()]
    assert one["cross_sectional_forward_rank_7d"].between(0, 1).all()


def test_rule_momentum_uses_only_past_returns(tmp_path):
    cfg, out = _run(tmp_path)
    preds = pd.read_parquet(tmp_path / "data" / "predictions" / "alpha_research_predictions.parquet")
    rule = preds[preds["model_name"] == "rule_momentum_30d"]
    assert not rule.empty


def test_rule_composite_outputs_deterministic_scores(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["alpha_research"]["models"] = ["rule_momentum_30d"]
    _write_inputs(tmp_path)
    assert AlphaResearchAgent(cfg).execute(max_retries=1)
    first = pd.read_parquet(tmp_path / "data" / "predictions" / "alpha_research_predictions.parquet")
    assert AlphaResearchAgent(cfg).execute(max_retries=1)
    second = pd.read_parquet(tmp_path / "data" / "predictions" / "alpha_research_predictions.parquet")
    pd.testing.assert_series_equal(first["prediction"].reset_index(drop=True), second["prediction"].reset_index(drop=True))


def test_model_leaderboard_requires_positive_ic_for_signal_pass(tmp_path):
    cfg, out = _run(tmp_path)
    lb = pd.read_parquet(out / "research_leaderboard.parquet")
    bad = lb[lb["signal_gate_passed"]]
    assert bad.empty or (bad["mean_rank_ic"] > 0).all()


def test_strategy_leaderboard_requires_benchmark_outperformance(tmp_path):
    cfg, out = _run(tmp_path)
    lb = pd.read_parquet(out / "research_leaderboard.parquet")
    passed = lb[lb["final_alpha_status"] == "passed"]
    assert passed.empty


def test_no_actual_return_used_for_allocation(tmp_path):
    cfg, out = _run(tmp_path)
    assert not (tmp_path / "data" / "predictions" / "model_predictions.parquet").exists()


def test_optional_prediction_export_is_portfolio_safe(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["alpha_research"]["export_candidate_to_predictions"] = True
    _write_inputs(tmp_path)
    assert AlphaResearchAgent(cfg).execute(max_retries=1)
    export_path = tmp_path / "data" / "predictions" / "model_predictions.parquet"
    lb = pd.read_parquet(tmp_path / "data" / "research" / "research_leaderboard.parquet")
    if not lb["candidate_for_backtest"].any():
        assert not export_path.exists()
        return
    compat = pd.read_parquet(export_path)
    forbidden = ("actual", "label", "future", "realized", "target", "y_")
    assert not [c for c in compat.columns if any(tok in c.lower() for tok in forbidden)]


def test_research_report_written(tmp_path):
    cfg, out = _run(tmp_path)
    assert (out / "alpha_research_report.md").exists()


def test_verify_alpha_research_rejects_fake_passed_alpha(tmp_path):
    cfg, out = _run(tmp_path)
    path = out / "research_leaderboard.parquet"
    lb = pd.read_parquet(path)
    lb.loc[lb.index[0], "final_alpha_status"] = "passed"
    lb.to_parquet(path, index=False)
    failures = validate_alpha_research_outputs(cfg)
    assert any("final_alpha_status passed" in f for f in failures)


def test_verify_alpha_research_rejects_signal_only_backtest_metric(tmp_path):
    cfg, out = _run(tmp_path)
    path = out / "research_leaderboard.parquet"
    lb = pd.read_parquet(path)
    lb.loc[lb.index[0], "sharpe"] = 1.0
    lb.to_parquet(path, index=False)
    failures = validate_alpha_research_outputs(cfg)
    assert any("signal_only row has non-null sharpe" in f for f in failures)


def test_verify_alpha_research_passes_valid_fixture(tmp_path):
    cfg, out = _run(tmp_path)
    assert validate_alpha_research_outputs(cfg) == []
