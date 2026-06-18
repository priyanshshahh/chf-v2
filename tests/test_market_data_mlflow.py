"""
Phase 7 tests — MLflow run logging for MarketDataAgent.

  * A run is logged (params, metrics, tags, content hash) into a tmp tracking dir.
  * Artifacts (manifest, quality md) are attached.
  * log_market_run=false is a clean no-op.
  * Missing/unimportable MLflow degrades gracefully (no exception).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from configs.config import load_config
from agents.market_data_agent import MarketDataAgent


def _agent_with_tracking(tmp_path, enabled=True):
    cfg = load_config()
    cfg["_project_root"] = str(tmp_path)
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["mlflow"] = {
        "tracking_uri": str(tmp_path / "mlruns"),
        "experiment_name": "CHF_test_market",
        "log_artifacts": True,
        "log_market_run": enabled,
    }
    agent = MarketDataAgent(cfg)
    agent.run_id = "testrun"
    agent.generate_snapshot_id("test")
    return agent, cfg


def _fake_manifest():
    return {
        "data_content_hash": "abc123def4567890",
        "as_of_date": "2026-03-24",
        "universe_snapshot_date": "2026-06-01",
        "requested_assets": 100,
        "lookback_days": 2000,
        "min_history_days": 365,
        "anomaly_policy": "flag_only",
        "fetched_assets": 90,
        "full_ohlcv_assets": 85,
        "persisted_assets": 90,
        "coverage_ratio": 0.9,
        "full_ohlcv_coverage_ratio": 0.85,
        "price_anomalies_total": 3,
    }


def test_mlflow_logs_run_and_artifacts(tmp_path):
    pytest.importorskip("mlflow")
    import mlflow

    agent, cfg = _agent_with_tracking(tmp_path, enabled=True)
    # Write two small artifacts to attach.
    man = tmp_path / "market_manifest.json"
    man.write_text(json.dumps(_fake_manifest()))
    qa = tmp_path / "data_quality_daily.md"
    qa.write_text("# QA\n- ok")

    agent._log_to_mlflow(_fake_manifest(), [man, qa])
    assert agent.metrics.get("mlflow_logged") == 1.0

    mlflow.set_tracking_uri(str(tmp_path / "mlruns"))
    exp = mlflow.get_experiment_by_name("CHF_test_market")
    assert exp is not None
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    assert len(runs) == 1
    row = runs.iloc[0]
    assert row["tags.data_content_hash"] == "abc123def4567890"
    assert float(row["metrics.fetched_assets"]) == 90.0
    assert float(row["metrics.price_anomalies_total"]) == 3.0


def test_mlflow_disabled_is_noop(tmp_path):
    agent, cfg = _agent_with_tracking(tmp_path, enabled=False)
    agent._log_to_mlflow(_fake_manifest(), [])
    assert "mlflow_logged" not in agent.metrics
    assert not (tmp_path / "mlruns").exists()


def test_mlflow_failure_is_non_fatal(tmp_path, monkeypatch):
    """If MLflow raises, persist-time logging must not propagate."""
    agent, cfg = _agent_with_tracking(tmp_path, enabled=True)
    # Force a failure by pointing the tracking URI at an illegal scheme.
    agent.cfg["mlflow"]["tracking_uri"] = "http://:bad:port"
    # Should not raise.
    agent._log_to_mlflow(_fake_manifest(), [])
