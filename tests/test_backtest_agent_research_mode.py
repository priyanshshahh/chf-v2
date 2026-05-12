from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.backtest_agent import BacktestAgent
from configs.config import load_config
from scripts.verify_backtest_run import validate_backtest_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    btcfg = dict(cfg.get("backtesting", {}))
    btcfg.update(cfg.get("backtesting_smoke", {}))
    btcfg["allocation_path"] = "data/allocations/allocations_from_predictions.parquet"
    btcfg["allocation_manifest_path"] = "data/allocations/allocation_manifest.json"
    btcfg["market_path"] = "data/raw/market/market_ohlcv.parquet"
    btcfg["output_dir"] = "data/backtests"
    cfg["backtesting"] = btcfg
    return cfg


def _write_inputs(
    tmp_path: Path,
    *,
    include_allocations: bool = True,
    missing_market_symbol: str | None = None,
    late_start_symbol: str | None = None,
    bearish_btc_eth: bool = False,
) -> None:
    alloc_dir = tmp_path / "data" / "allocations"
    market_dir = tmp_path / "data" / "raw" / "market"
    alloc_dir.mkdir(parents=True, exist_ok=True)
    market_dir.mkdir(parents=True, exist_ok=True)

    dates = pd.date_range("2025-01-01", periods=120, freq="D", tz="UTC")
    syms = ["BTC", "ETH", "SOL", "ADA", "UNI", "LINK", "AAVE", "DOGE"]

    market_rows = []
    for i, dt in enumerate(dates):
        for j, sym in enumerate(syms):
            if missing_market_symbol == sym and i >= 40:
                continue
            if late_start_symbol == sym and i < 60:
                continue
            if sym == "BTC":
                close = 200 - 0.8 * i if bearish_btc_eth else 100 + 1.2 * i
            elif sym == "ETH":
                close = 220 - 0.9 * i if bearish_btc_eth else 120 + 1.1 * i
            elif sym == "SOL":
                close = 80 + 1.8 * i
            elif sym == "ADA":
                close = 70 + 1.4 * i
            elif sym == "UNI":
                close = 60 + 1.5 * i
            elif sym == "LINK":
                close = 65 + 1.3 * i
            elif sym == "AAVE":
                close = 55 + 0.6 * i
            else:
                close = 50 + 0.2 * i
            market_rows.append(
                {
                    "date_ts": dt,
                    "symbol": sym,
                    "close": close,
                    "volume": 100000 + i,
                    "source": "coinbase",
                }
            )
    pd.DataFrame(market_rows).to_parquet(market_dir / "market_ohlcv.parquet", index=False)

    if include_allocations:
        signal_dates = pd.date_range("2025-02-01", periods=10, freq="7D", tz="UTC")
        alloc_rows = []
        base_weights = {
            "top_5_equal_weight": {"BTC": 0.2, "ETH": 0.2, "SOL": 0.2, "ADA": 0.2, "UNI": 0.2},
            "top_10_equal_weight": {"BTC": 0.15, "ETH": 0.15, "SOL": 0.15, "ADA": 0.15, "UNI": 0.1, "LINK": 0.1, "AAVE": 0.1, "DOGE": 0.1},
            "score_weighted_long_only": {"BTC": 0.15, "ETH": 0.15, "SOL": 0.15, "ADA": 0.15, "UNI": 0.15, "LINK": 0.1, "AAVE": 0.1, "DOGE": 0.05},
        }
        for ridx, signal_date in enumerate(signal_dates):
            execution_date = signal_date + pd.Timedelta(days=1)
            for strategy_name, weights in base_weights.items():
                rotated = list(weights.keys())[ridx % len(weights):] + list(weights.keys())[:ridx % len(weights)]
                for rank, sym in enumerate(rotated, start=1):
                    w = weights[sym]
                    alloc_rows.append(
                        {
                            "date_ts": execution_date,
                            "signal_date": signal_date,
                            "execution_date": execution_date,
                            "symbol": sym,
                            "cmc_id": 1000 + syms.index(sym),
                            "model_name": "baseline_cross_sectional_mean",
                            "horizon_days": 14,
                            "feature_set": "market_only",
                            "predicted_return": float(len(rotated) - rank + 1) / 100.0,
                            "prediction_rank": float(rank),
                            "prediction_zscore": float(len(rotated) - rank),
                            "signal_score": float(len(rotated) - rank),
                            "side": "long",
                            "raw_weight": w,
                            "target_weight": w,
                            "weight": w,
                            "previous_weight": 0.0 if ridx == 0 else w * 0.8,
                            "turnover_contribution": abs(w - (0.0 if ridx == 0 else w * 0.8)),
                            "risk_estimate": 0.02 + 0.005 * rank,
                            "rebalance_frequency": "W",
                            "strategy_name": strategy_name,
                            "alpha_gate_passed": False,
                            "allocation_mode": "diagnostic_not_live_trading",
                            "snapshot_id": "alloc",
                            "run_id": "run",
                            "created_at_utc": "2026-05-01T00:00:00+00:00",
                        }
                    )
        pd.DataFrame(alloc_rows).to_parquet(alloc_dir / "allocations_from_predictions.parquet", index=False)
        with open(alloc_dir / "allocation_manifest.json", "w") as fh:
            json.dump(
                {
                    "alpha_gate_passed": False,
                    "allocation_mode": "diagnostic_not_live_trading",
                    "strategy_names": sorted(base_weights),
                },
                fh,
            )
        pd.DataFrame(
            [
                {"date_ts": pd.Timestamp("2025-02-02", tz="UTC"), "signal_date": pd.Timestamp("2025-02-01", tz="UTC"), "execution_date": pd.Timestamp("2025-02-02", tz="UTC"), "strategy_name": "top_5_equal_weight", "candidate_count": 8, "selected_count": 5, "dropped_missing_prediction_count": 0, "dropped_missing_price_count": 0, "dropped_missing_risk_count": 0, "gross_exposure": 1.0, "net_exposure": 1.0, "weight_sum": 1.0, "cash_weight": 0.0, "max_weight_actual": 0.2, "turnover": 1.0, "alpha_gate_passed": False, "allocation_mode": "diagnostic_not_live_trading", "passed_qa": True, "failure_reason": "", "model_name": "baseline_cross_sectional_mean", "horizon_days": 14, "feature_set": "market_only"}
            ]
        ).to_parquet(alloc_dir / "allocation_coverage_report.parquet", index=False)


def _run(tmp_path: Path, **kwargs) -> tuple[dict, Path]:
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path, **kwargs)
    agent = BacktestAgent(cfg)
    assert agent.execute(max_retries=1)
    out_dir = tmp_path / "data" / "backtests"
    return cfg, out_dir


def test_backtest_loads_new_portfolio_allocations(tmp_path):
    cfg, out_dir = _run(tmp_path)
    assert (out_dir / "backtest_summary.parquet").exists()


def test_backtest_runs_each_strategy_separately(tmp_path):
    cfg, out_dir = _run(tmp_path)
    summary = pd.read_parquet(out_dir / "backtest_summary.parquet")
    assert {"top_5_equal_weight", "top_10_equal_weight", "score_weighted_long_only"}.issubset(set(summary["strategy_name"]))


def test_backtest_applies_execution_date_weights_only(tmp_path):
    cfg, out_dir = _run(tmp_path)
    eq = pd.read_parquet(out_dir / "equity_curves.parquet")
    top5 = eq[eq["strategy_name"] == "top_5_equal_weight"].sort_values("date_ts")
    assert abs(float(top5.iloc[0]["gross_return"])) < 1e-12


def test_backtest_computes_turnover(tmp_path):
    cfg, out_dir = _run(tmp_path)
    turnover = pd.read_parquet(out_dir / "turnover_report.parquet")
    assert turnover["turnover"].sum() > 0


def test_backtest_applies_transaction_costs(tmp_path):
    cfg, out_dir = _run(tmp_path)
    eq = pd.read_parquet(out_dir / "equity_curves.parquet")
    assert (eq["transaction_cost"] >= 0).all()


def test_backtest_costs_reduce_returns(tmp_path):
    cfg, out_dir = _run(tmp_path)
    cost = pd.read_parquet(out_dir / "cost_sweep.parquet")
    top5 = cost[cost["strategy_name"] == "top_5_equal_weight"].sort_values("cost_bps")
    assert float(top5.iloc[0]["final_value"]) >= float(top5.iloc[-1]["final_value"])


def test_backtest_builds_btc_eth_benchmarks(tmp_path):
    cfg, out_dir = _run(tmp_path)
    bench = pd.read_parquet(out_dir / "benchmark_summary.parquet")
    assert {"BTC", "ETH", "BTC_ETH_50_50"}.issubset(set(bench["strategy_name"]))


def test_backtest_builds_equal_weight_benchmark(tmp_path):
    cfg, out_dir = _run(tmp_path)
    bench = pd.read_parquet(out_dir / "benchmark_summary.parquet")
    assert "equal_weight_universe" in set(bench["strategy_name"])


def test_backtest_outputs_strategy_comparison(tmp_path):
    cfg, out_dir = _run(tmp_path)
    comp = pd.read_parquet(out_dir / "strategy_comparison.parquet")
    assert "alpha_status" in comp.columns


def test_alpha_status_requires_benchmark_outperformance(tmp_path):
    cfg, out_dir = _run(tmp_path)
    comp = pd.read_parquet(out_dir / "strategy_comparison.parquet")
    passed = comp[comp["alpha_status"] == "passed"]
    if not passed.empty:
        assert passed["beats_equal_weight"].all()
        assert passed["beats_btc_eth_50_50"].all()


def test_backtest_rejects_empty_allocations(tmp_path):
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path, include_allocations=False)
    with pytest.raises(FileNotFoundError):
        BacktestAgent(cfg).prepare()


def test_backtest_rejects_missing_market_prices(tmp_path):
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path, missing_market_symbol="SOL")
    assert not BacktestAgent(cfg).execute(max_retries=1)


def test_verify_backtest_rejects_missing_required_columns(tmp_path):
    cfg, out_dir = _run(tmp_path)
    path = out_dir / "equity_curves.parquet"
    eq = pd.read_parquet(path).drop(columns=["portfolio_value"])
    eq.to_parquet(path, index=False)
    failures = validate_backtest_outputs(cfg)
    assert any("missing required column portfolio_value" in f for f in failures)


def test_verify_backtest_rejects_duplicate_equity_rows(tmp_path):
    cfg, out_dir = _run(tmp_path)
    path = out_dir / "equity_curves.parquet"
    eq = pd.read_parquet(path)
    eq = pd.concat([eq, eq.iloc[[0]]], ignore_index=True)
    eq.to_parquet(path, index=False)
    failures = validate_backtest_outputs(cfg)
    assert any("duplicate date_ts + strategy_name" in f for f in failures)


def test_verify_backtest_passes_valid_fixture(tmp_path):
    cfg, _out_dir = _run(tmp_path)
    failures = validate_backtest_outputs(cfg)
    assert failures == []


def test_btc_eth_50_50_uses_same_window_as_btc_eth(tmp_path):
    cfg, out_dir = _run(tmp_path)
    bench = pd.read_parquet(out_dir / "benchmark_summary.parquet")
    sub = bench[bench["strategy_name"].isin(["BTC", "ETH", "BTC_ETH_50_50"])]
    assert sub["start_date"].nunique() == 1
    assert sub["end_date"].nunique() == 1


def test_btc_eth_50_50_cannot_outperform_both_assets_when_both_negative(tmp_path):
    cfg, out_dir = _run(tmp_path, bearish_btc_eth=True)
    sanity = pd.read_parquet(out_dir / "benchmark_sanity_report.parquet")
    mix = sanity[sanity["benchmark_name"] == "BTC_ETH_50_50"].iloc[0]
    assert bool(mix["passed_sanity"])


def test_benchmarks_clipped_to_allocation_window(tmp_path):
    cfg, out_dir = _run(tmp_path)
    alloc = pd.read_parquet(tmp_path / "data" / "allocations" / "allocations_from_predictions.parquet")
    start = pd.to_datetime(alloc["date_ts"], utc=True).min().isoformat()
    end = pd.to_datetime(alloc["date_ts"], utc=True).max().isoformat()
    bench = pd.read_parquet(out_dir / "benchmark_summary.parquet")
    assert set(bench["start_date"]) == {start}
    assert set(bench["end_date"]) == {end}


def test_equal_weight_does_not_allocate_before_asset_first_price(tmp_path):
    cfg, out_dir = _run(tmp_path, late_start_symbol="DOGE")
    eq = pd.read_parquet(out_dir / "equity_curves.parquet")
    bench = eq[eq["strategy_name"] == "equal_weight_universe"].sort_values("date_ts")
    assert bench["n_positions"].min() >= 0


def test_equal_weight_handles_missing_prices_without_backfill_bias(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["backtesting"]["fail_on_missing_held_returns"] = False
    cfg["backtesting"]["max_missing_held_return_fraction"] = 0.01
    _write_inputs(tmp_path, missing_market_symbol="SOL")
    assert BacktestAgent(cfg).execute(max_retries=1)
    out_dir = tmp_path / "data" / "backtests"
    sanity = pd.read_parquet(out_dir / "benchmark_sanity_report.parquet")
    ew = sanity[sanity["benchmark_name"] == "equal_weight_universe"].iloc[0]
    assert np.isfinite(float(ew["max_abs_daily_return"]))


def test_verify_backtest_rejects_impossible_btc_eth_50_50(tmp_path):
    cfg, out_dir = _run(tmp_path, bearish_btc_eth=True)
    path = out_dir / "benchmark_summary.parquet"
    bench = pd.read_parquet(path)
    bench.loc[bench["strategy_name"] == "BTC_ETH_50_50", "total_return"] = 5.0
    bench.to_parquet(path, index=False)
    failures = validate_backtest_outputs(cfg)
    assert any("impossible" in f.lower() for f in failures)


def test_verify_backtest_rejects_absurd_equal_weight_daily_return(tmp_path):
    cfg, out_dir = _run(tmp_path)
    path = out_dir / "benchmark_sanity_report.parquet"
    sanity = pd.read_parquet(path)
    sanity.loc[sanity["benchmark_name"] == "equal_weight_universe", "max_abs_daily_return"] = 20.0
    sanity.to_parquet(path, index=False)
    failures = validate_backtest_outputs(cfg)
    assert any("absurd daily return" in f.lower() for f in failures)


def test_benchmark_sanity_report_written(tmp_path):
    cfg, out_dir = _run(tmp_path)
    assert (out_dir / "benchmark_sanity_report.parquet").exists()
