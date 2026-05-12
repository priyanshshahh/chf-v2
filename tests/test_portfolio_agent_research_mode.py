from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.portfolio_agent import PortfolioAgent
from configs.config import load_config
from scripts.verify_portfolio_run import validate_portfolio_outputs


def _cfg(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    pcfg = dict(cfg.get("portfolio", {}))
    pcfg.update(cfg.get("portfolio_smoke", {}))
    pcfg["output_dir"] = "data/allocations"
    cfg["portfolio"] = pcfg
    return cfg


def _prediction_value(sym: str, date_idx: int) -> float:
    base = {
        "BTC": 0.08,
        "ETH": 0.07,
        "SOL": 0.06,
        "ADA": 0.05,
        "UNI": 0.04,
        "LINK": 0.03,
        "AAVE": 0.02,
        "DOGE": 0.01,
    }[sym]
    wobble = ((date_idx % 7) - 3) * 0.001
    return base + wobble


def _write_inputs(
    tmp_path: Path,
    *,
    include_leaderboard: bool = True,
    alpha_passed: bool = True,
    two_models: bool = False,
    add_actuals: bool = False,
    missing_execution_price_symbol: str | None = None,
    short_history_symbol: str | None = None,
) -> None:
    preds_dir = tmp_path / "data" / "predictions"
    market_dir = tmp_path / "data" / "raw" / "market"
    preds_dir.mkdir(parents=True, exist_ok=True)
    market_dir.mkdir(parents=True, exist_ok=True)

    dates = pd.date_range("2025-01-01", periods=120, freq="D", tz="UTC")
    syms = ["BTC", "ETH", "SOL", "ADA", "UNI", "LINK", "AAVE", "DOGE"]
    pred_rows = []
    market_rows = []

    for i, dt in enumerate(dates):
        for sym in syms:
            row = {
                "date_ts": dt,
                "symbol": sym,
                "model_name": "baseline_cross_sectional_mean",
                "feature_set": "market_only",
                "horizon_days": 14,
                "fold_id": i // 30,
                "prediction": _prediction_value(sym, i),
                "snapshot_id": "pred",
                "run_id": "run",
            }
            if add_actuals:
                row["actual_forward_return"] = -_prediction_value(sym, i) * 10
            pred_rows.append(row)
            if two_models:
                row2 = row.copy()
                row2["model_name"] = "random_forest"
                row2["feature_set"] = "market_plus_onchain"
                row2["prediction"] = row["prediction"] * 1.25
                pred_rows.append(row2)

            if short_history_symbol and sym == short_history_symbol and i < 115:
                continue

            vol_scale = {
                "BTC": 0.003,
                "ETH": 0.005,
                "SOL": 0.020,
                "ADA": 0.015,
                "UNI": 0.010,
                "LINK": 0.008,
                "AAVE": 0.012,
                "DOGE": 0.025,
            }[sym]
            close = 100.0 + i + 15.0 * np.sin(i / 6.0) * vol_scale * 100
            if missing_execution_price_symbol == sym and dt in set(dates[1::7]):
                continue
            market_rows.append(
                {
                    "date_ts": dt,
                    "symbol": sym,
                    "cmc_id": 1000 + syms.index(sym),
                    "exchange": "coinbase",
                    "exchange_symbol": f"{sym}/USD",
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 100000 + i,
                    "source": "coinmarketcap",
                    "snapshot_id": "mkt",
                    "fetched_at_utc": "2026-04-30T00:00:00+00:00",
                    "is_forward_filled": False,
                    "is_incomplete_dropped": False,
                    "data_type": "aggregate_ohlcv",
                    "is_full_ohlcv": True,
                    "quote_currency": "USD",
                }
            )

    pd.DataFrame(pred_rows).to_parquet(preds_dir / "model_predictions.parquet", index=False)
    if include_leaderboard:
        rows = [
            {
                "model_name": "baseline_cross_sectional_mean",
                "feature_set": "market_only",
                "horizon_days": 14,
                "alpha_status": "passed" if alpha_passed and not two_models else "failed",
                "selected_for_backtest": bool(alpha_passed and not two_models),
                "signal_gate_passed": bool(alpha_passed and not two_models),
                "candidate_for_backtest": bool(alpha_passed and not two_models),
                "rank_ic_mean": 0.01,
                "rank_ic_tstat": 1.0,
                "top_bottom_10_spread": 0.02,
                "prediction_rows": 1000,
                "fold_count": 4,
            }
        ]
        if two_models:
            rows.append(
                {
                    "model_name": "random_forest",
                    "feature_set": "market_plus_onchain",
                    "horizon_days": 14,
                    "alpha_status": "passed" if alpha_passed else "failed",
                    "selected_for_backtest": bool(alpha_passed),
                    "signal_gate_passed": bool(alpha_passed),
                    "candidate_for_backtest": bool(alpha_passed),
                    "rank_ic_mean": 0.03,
                    "rank_ic_tstat": 2.0,
                    "top_bottom_10_spread": 0.04,
                    "prediction_rows": 1200,
                    "fold_count": 5,
                }
            )
        pd.DataFrame(rows).to_parquet(preds_dir / "model_leaderboard.parquet", index=False)
    with open(preds_dir / "model_manifest.json", "w") as fh:
        json.dump({"selected_model": "baseline_cross_sectional_mean"}, fh)
    pd.DataFrame(market_rows).to_parquet(market_dir / "market_ohlcv.parquet", index=False)


def _run_agent(tmp_path: Path, **kwargs) -> tuple[dict, Path]:
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path, **kwargs)
    agent = PortfolioAgent(cfg)
    assert agent.execute(max_retries=1)
    out_dir = tmp_path / "data" / "allocations"
    return cfg, out_dir


def test_portfolio_loads_canonical_predictions(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_from_predictions.parquet")
    assert not alloc.empty


def test_portfolio_rejects_missing_predictions(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        PortfolioAgent(cfg).prepare()


def test_portfolio_selects_best_available_model_from_leaderboard(tmp_path):
    cfg, out_dir = _run_agent(tmp_path, two_models=True, alpha_passed=True)
    manifest = json.load(open(out_dir / "allocation_manifest.json"))
    assert manifest["selected_model_name"] == "random_forest"
    assert manifest["selected_feature_set"] == "market_plus_onchain"


def test_portfolio_falls_back_when_no_alpha_model_passes(tmp_path):
    cfg, out_dir = _run_agent(tmp_path, include_leaderboard=True, alpha_passed=False, two_models=True)
    manifest = json.load(open(out_dir / "allocation_manifest.json"))
    assert manifest["alpha_gate_passed"] is False
    assert manifest["allocation_mode"] == "diagnostic_not_live_trading"


def test_top_k_equal_weight_sums_to_one(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_top_5_equal_weight.parquet")
    sums = alloc.groupby("date_ts")["weight"].sum()
    assert np.allclose(sums.values, 1.0, atol=1e-6)


def test_top_k_equal_weight_respects_max_weight(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["portfolio"]["max_weight"] = 0.2
    _write_inputs(tmp_path)
    assert PortfolioAgent(cfg).execute(max_retries=1)
    alloc = pd.read_parquet(tmp_path / "data" / "allocations" / "allocations_top_5_equal_weight.parquet")
    assert (alloc["weight"] <= 0.2 + 1e-9).all()


def test_vol_scaled_uses_inverse_volatility(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_top_5_vol_scaled.parquet")
    one_day = alloc[alloc["date_ts"] == alloc["date_ts"].min()].sort_values("risk_estimate")
    assert one_day.iloc[0]["weight"] >= one_day.iloc[-1]["weight"]


def test_vol_scaled_respects_max_weight(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["portfolio"]["max_weight"] = 0.18
    _write_inputs(tmp_path)
    assert PortfolioAgent(cfg).execute(max_retries=1)
    alloc = pd.read_parquet(tmp_path / "data" / "allocations" / "allocations_top_10_vol_scaled.parquet")
    assert (alloc["weight"] <= 0.18 + 1e-9).all()


def test_score_weighted_long_only_has_no_negative_weights(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_score_weighted_long_only.parquet")
    assert (alloc["weight"] >= 0).all()


def test_score_weighted_vol_scaled_uses_signal_and_risk(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_score_weighted_vol_scaled.parquet")
    one_day = alloc[alloc["date_ts"] == alloc["date_ts"].min()].copy()
    assert (one_day["signal_score"] > 0).all()
    assert (one_day["risk_estimate"] > 0).all()
    one_day["ratio"] = one_day["signal_score"] / one_day["risk_estimate"]
    assert one_day.sort_values("ratio", ascending=False).iloc[0]["weight"] >= one_day.sort_values("ratio").iloc[0]["weight"]


def test_turnover_control_reduces_weight_changes(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    coverage = pd.read_parquet(out_dir / "allocation_coverage_report.parquet")
    turn = coverage[coverage["strategy_name"] == "turnover_controlled"]["turnover"].mean()
    score = coverage[coverage["strategy_name"] == "score_weighted_vol_scaled"]["turnover"].mean()
    assert turn <= score + 1e-9


def test_execution_lag_prevents_same_day_lookahead(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    alloc = pd.read_parquet(out_dir / "allocations_from_predictions.parquet")
    assert (pd.to_datetime(alloc["execution_date"], utc=True) > pd.to_datetime(alloc["signal_date"], utc=True)).all()


def test_missing_market_price_drops_asset(tmp_path):
    cfg, out_dir = _run_agent(tmp_path, missing_execution_price_symbol="BTC")
    coverage = pd.read_parquet(out_dir / "allocation_coverage_report.parquet")
    assert int(coverage["dropped_missing_price_count"].max()) > 0


def test_missing_risk_drops_or_imputes_correctly(tmp_path):
    cfg, out_dir = _run_agent(tmp_path, short_history_symbol="DOGE")
    alloc = pd.read_parquet(out_dir / "allocations_top_5_vol_scaled.parquet")
    assert alloc["risk_estimate"].notna().all()
    assert (alloc["risk_estimate"] > 0).all()


def test_no_actual_return_used_for_allocation(tmp_path):
    cfg = _cfg(tmp_path)
    _write_inputs(tmp_path, add_actuals=True)
    assert not PortfolioAgent(cfg).execute(max_retries=1)


def test_realized_prediction_columns_allowed_only_for_diagnostics(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["portfolio"]["allow_realized_columns_in_predictions_for_diagnostics"] = True
    _write_inputs(tmp_path, add_actuals=True)
    assert PortfolioAgent(cfg).execute(max_retries=1)


def test_verify_portfolio_rejects_weight_sum_error(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    path = out_dir / "allocations_from_predictions.parquet"
    alloc = pd.read_parquet(path)
    mask = (alloc["strategy_name"] == "top_5_equal_weight") & (alloc["date_ts"] == alloc["date_ts"].min())
    alloc.loc[mask, "weight"] = 0.5
    alloc.to_parquet(path, index=False)
    failures = validate_portfolio_outputs(cfg)
    assert any("weight_sum plus cash_weight" in f or "gross exposure" in f for f in failures)


def test_verify_portfolio_rejects_duplicate_symbol_date_strategy(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    path = out_dir / "allocations_from_predictions.parquet"
    alloc = pd.read_parquet(path)
    alloc = pd.concat([alloc, alloc.iloc[[0]]], ignore_index=True)
    alloc.to_parquet(path, index=False)
    failures = validate_portfolio_outputs(cfg)
    assert any("duplicate" in f.lower() for f in failures)


def test_verify_portfolio_rejects_lookahead_execution(tmp_path):
    cfg, out_dir = _run_agent(tmp_path)
    path = out_dir / "allocations_from_predictions.parquet"
    alloc = pd.read_parquet(path)
    alloc.loc[alloc.index[0], "execution_date"] = alloc.loc[alloc.index[0], "signal_date"]
    alloc.to_parquet(path, index=False)
    failures = validate_portfolio_outputs(cfg)
    assert any("lookahead execution" in f.lower() or "same-day" in f.lower() for f in failures)


def test_verify_portfolio_passes_valid_fixture(tmp_path):
    cfg, _out_dir = _run_agent(tmp_path)
    failures = validate_portfolio_outputs(cfg)
    assert failures == []
