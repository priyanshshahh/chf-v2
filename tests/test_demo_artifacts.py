from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from configs.config import load_config
from main import generate_demo_artifacts


def _temp_cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    return cfg


def test_demo_artifacts_match_dashboard_and_api_contracts(tmp_path):
    cfg = _temp_cfg(tmp_path)

    generate_demo_artifacts(cfg)

    expected_files = [
        tmp_path / "data" / "raw" / "market" / "BTC_ohlcv.parquet",
        tmp_path / "data" / "features" / "market_features.parquet",
        tmp_path / "data" / "features" / "full_features.parquet",
        tmp_path / "data" / "labels" / "labels_7d.parquet",
        tmp_path / "data" / "predictions" / "predictions_lightgbm_h7d.parquet",
        tmp_path / "data" / "allocations" / "allocations_top_k_equal_weight.parquet",
        tmp_path / "data" / "allocations" / "latest_allocation.parquet",
        tmp_path / "data" / "allocations" / "allocations_transaction_log.parquet",
        tmp_path / "data" / "backtests" / "backtest_summary.parquet",
        tmp_path / "data" / "backtests" / "equity_curves.parquet",
        tmp_path / "data" / "reports" / "alpha_report.json",
        tmp_path / "data" / "reports" / "alpha_report.md",
    ]

    for expected in expected_files:
        assert expected.exists(), f"missing expected demo artifact: {expected}"

    pred_df = pd.read_parquet(tmp_path / "data" / "predictions" / "predictions_lightgbm_h7d.parquet")
    assert {"symbol", "date_ts", "predicted_return", "actual_return", "model_name", "horizon_days"}.issubset(pred_df.columns)
    assert not pred_df.empty

    alloc_df = pd.read_parquet(tmp_path / "data" / "allocations" / "latest_allocation.parquet")
    assert {"symbol", "date_ts", "weight", "strategy", "signal_score"}.issubset(alloc_df.columns)
    assert alloc_df["weight"].sum() > 0.99

    summary_df = pd.read_parquet(tmp_path / "data" / "backtests" / "backtest_summary.parquet")
    assert {"backtest_name", "cagr", "sharpe", "max_drawdown", "total_return"}.issubset(summary_df.columns)
    assert {"main", "benchmark_BTC", "benchmark_ETH", "benchmark_EW_top100"}.issubset(
        set(summary_df["backtest_name"])
    )
