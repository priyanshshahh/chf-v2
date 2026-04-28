from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd

from agents.backtest_agent import BacktestAgent
from agents.feature_agent import FeatureAgentV1, FeatureAgentV2
from agents.label_agent import LabelAgent
from agents.model_agent import ModelAgent
from agents.portfolio_agent import PortfolioAgent
from configs.config import load_config


def _temp_cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    cfg["labels"]["horizons"] = [7]
    cfg["modeling"]["models"] = ["random_forest"]
    cfg["modeling"]["default_horizon"] = 7
    cfg["portfolio"]["strategies"] = ["top_k_equal_weight"]
    cfg["portfolio"]["top_k_values"] = [5]
    cfg["portfolio"]["default_top_k"] = 5
    return cfg


def _write_synthetic_market(tmp_path: Path, symbols=None, n_days: int = 520) -> None:
    if symbols is None:
        symbols = ["BTC", "ETH", "SOL", "BNB", "ADA"]

    rng = np.random.default_rng(42)
    dates = pd.date_range("2022-01-01", periods=n_days, freq="D", tz="UTC")
    raw_dir = tmp_path / "data" / "raw" / "market"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for i, sym in enumerate(symbols):
        base = 100 + (i * 10)
        returns = rng.normal(0.001, 0.02, n_days)
        prices = base * np.cumprod(1 + returns)
        df = pd.DataFrame({
            "symbol": sym,
            "date_ts": dates,
            "open": prices * 0.99,
            "high": prices * 1.02,
            "low": prices * 0.98,
            "close": prices,
            "volume": rng.uniform(1e6, 5e7, n_days),
            "snapshot_id": "itest",
        })
        df.to_parquet(raw_dir / f"{sym}_ohlcv.parquet", index=False)


def test_pipeline_agents_run_end_to_end_from_market_data(tmp_path):
    cfg = _temp_cfg(tmp_path)
    _write_synthetic_market(tmp_path)

    assert FeatureAgentV1(cfg).execute()
    assert FeatureAgentV2(cfg).execute()
    assert LabelAgent(cfg).execute()
    assert ModelAgent(cfg, horizon=7, model_names=["random_forest"]).execute()
    assert PortfolioAgent(cfg, model_name="random_forest", horizon=7).execute()
    assert BacktestAgent(cfg).execute()

    expected_outputs = [
        tmp_path / "data" / "features" / "market_features.parquet",
        tmp_path / "data" / "features" / "full_features.parquet",
        tmp_path / "data" / "labels" / "labels_7d.parquet",
        tmp_path / "data" / "predictions" / "predictions_random_forest_h7d.parquet",
        tmp_path / "data" / "allocations" / "allocations_top_k_equal_weight.parquet",
        tmp_path / "data" / "allocations" / "latest_allocation.parquet",
        tmp_path / "data" / "backtests" / "backtest_summary.parquet",
        tmp_path / "data" / "backtests" / "equity_curves.parquet",
        tmp_path / "data" / "reports" / "alpha_report.json",
        tmp_path / "data" / "reports" / "alpha_report.md",
    ]

    for expected in expected_outputs:
        assert expected.exists(), f"missing expected pipeline output: {expected}"

    pred_df = pd.read_parquet(tmp_path / "data" / "predictions" / "predictions_random_forest_h7d.parquet")
    assert {"predicted_return", "actual_return", "model_name", "horizon_days"}.issubset(pred_df.columns)
    assert not pred_df.empty

    alloc_df = pd.read_parquet(tmp_path / "data" / "allocations" / "allocations_top_k_equal_weight.parquet")
    assert {"symbol", "date_ts", "weight", "strategy", "top_k"}.issubset(alloc_df.columns)
    assert not alloc_df.empty

    summary_df = pd.read_parquet(tmp_path / "data" / "backtests" / "backtest_summary.parquet")
    assert {"backtest_name", "cagr", "sharpe", "max_drawdown"}.issubset(summary_df.columns)
    assert "main" in set(summary_df["backtest_name"])
    assert {"benchmark_BTC", "benchmark_ETH", "benchmark_EW_top100"}.issubset(
        set(summary_df["backtest_name"])
    )
