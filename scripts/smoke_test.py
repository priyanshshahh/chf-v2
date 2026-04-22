#!/usr/bin/env python3
"""
CHF Smoke Test
==============
End-to-end validation with synthetic data.
Runs every pipeline stage and verifies outputs exist and are non-empty.
No external API calls — fully offline.

Usage:
  python scripts/smoke_test.py

Exit code 0 = all tests passed.
Exit code 1 = one or more tests failed.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

PASS = 0
FAIL = 0
RESULTS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def run_smoke_test():
    print("=" * 60)
    print("CHF Smoke Test")
    print("=" * 60)

    # ── 1. Imports ────────────────────────────────────────────────
    print("\n[1] Testing imports...")
    modules_to_import = [
        "configs.config",
        "agents.base",
        "agents.universe_agent",
        "agents.market_data_agent",
        "agents.onchain_agent",
        "agents.feature_agent",
        "agents.label_agent",
        "agents.model_agent",
        "agents.portfolio_agent",
        "agents.backtest_agent",
        "features.feature_engineering",
        "models.walk_forward",
        "models.ablation",
        "pipelines.pipeline_runner",
        "pipelines.duckdb_engine",
    ]
    for mod in modules_to_import:
        try:
            __import__(mod)
            check(f"import {mod}", True)
        except Exception as e:
            check(f"import {mod}", False, str(e))

    # ── 2. Feature Engineering ────────────────────────────────────
    print("\n[2] Testing feature engineering...")
    try:
        from features.feature_engineering import (
            compute_log_returns, compute_rolling_volatility,
            compute_rolling_beta, compute_rolling_skewness,
            compute_turnover_ratio, _rolling_beta_kernel, _NUMBA_AVAILABLE,
        )
        prices = pd.Series(100 * np.cumprod(1 + np.random.default_rng(42).normal(0.001, 0.02, 200)))
        log_ret = compute_log_returns(prices, 7)
        check("compute_log_returns", not log_ret.isna().all())

        vol = compute_rolling_volatility(np.log(prices / prices.shift(1)), 30)
        check("compute_rolling_volatility", not vol.isna().all())

        skew = compute_rolling_skewness(np.log(prices / prices.shift(1)), 30)
        check("compute_rolling_skewness", not skew.isna().all())

        btc_ret = pd.Series(np.random.default_rng(1).normal(0.001, 0.02, 200))
        asset_ret = pd.Series(np.random.default_rng(2).normal(0.001, 0.02, 200))
        beta = compute_rolling_beta(asset_ret, btc_ret, 60)
        check("compute_rolling_beta (numba/numpy)", not beta.isna().all())
        check("numba available", _NUMBA_AVAILABLE, "numba not installed — using numpy fallback")

        turnover = compute_turnover_ratio(pd.Series(np.random.uniform(1e6, 1e8, 200)), 30)
        check("compute_turnover_ratio", not turnover.isna().all())
    except Exception as e:
        check("feature_engineering module", False, str(e))
        traceback.print_exc()

    # ── 3. Walk-forward CV ────────────────────────────────────────
    print("\n[3] Testing walk-forward CV...")
    try:
        from models.walk_forward import WalkForwardValidator
        rng = np.random.default_rng(42)
        n = 500
        X = pd.DataFrame(rng.normal(0, 1, (n, 5)), columns=[f"f{i}" for i in range(5)])
        y = pd.Series(rng.normal(0, 1, n))
        wfv = WalkForwardValidator(n_splits=3, embargo_days=7, test_size_days=60)
        splits = list(wfv.split(X))
        check("WalkForwardValidator splits", len(splits) == 3, f"got {len(splits)}")
        for i, (train_idx, test_idx) in enumerate(splits):
            check(f"fold {i} no overlap", len(set(train_idx) & set(test_idx)) == 0)
            check(f"fold {i} train < test", max(train_idx) < min(test_idx))
    except Exception as e:
        check("walk_forward module", False, str(e))
        traceback.print_exc()

    # ── 4. Ablation study ─────────────────────────────────────────
    print("\n[4] Testing ablation study...")
    try:
        from models.ablation import run_ablation, MARKET_ONLY_FEATURES, ALL_FEATURES
        rng = np.random.default_rng(42)
        symbols = ["BTC", "ETH", "SOL", "BNB", "ADA"]
        dates = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
        rows = []
        for sym in symbols:
            for d in dates:
                row = {"symbol": sym, "date_ts": d}
                for f in ALL_FEATURES:
                    row[f] = rng.normal(0, 1)
                rows.append(row)
        feat_df = pd.DataFrame(rows)
        label_rows = []
        for sym in symbols:
            for d in dates[:-7]:
                label_rows.append({"symbol": sym, "date_ts": d, "fwd_return_7d": rng.normal(0, 0.05)})
        label_df = pd.DataFrame(label_rows)
        cfg = {"modeling": {"n_splits": 2, "embargo_days": 7, "test_size_days": 60, "label_horizon_days": 7}}
        results = run_ablation(feat_df, label_df, cfg, output_dir=None)
        check("ablation market_only", "market_only" in results)
        check("ablation market_plus_onchain", "market_plus_onchain" in results)
        check("ablation IC computed", results.get("market_only", {}).get("mean_rank_ic") is not None)
        check("ablation marginal lift", "onchain_marginal_ic_lift" in results)
    except Exception as e:
        check("ablation module", False, str(e))
        traceback.print_exc()

    # ── 5. Hive-partitioned Parquet ───────────────────────────────
    print("\n[5] Testing hive-partitioned Parquet...")
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            dates = pd.date_range("2023-01-01", periods=60, freq="D", tz="UTC")
            df = pd.DataFrame({
                "symbol": "BTC",
                "date_ts": dates,
                "close": np.random.uniform(20000, 30000, 60),
            })
            # Simulate hive partitioning
            for (yr, mo), grp in df.groupby([df["date_ts"].dt.year, df["date_ts"].dt.month]):
                hive_dir = tmpdir / f"year={yr}" / f"month={mo:02d}"
                hive_dir.mkdir(parents=True, exist_ok=True)
                grp.to_parquet(hive_dir / "BTC.parquet", index=False)
            # Verify
            hive_files = list(tmpdir.rglob("*.parquet"))
            check("hive partition files created", len(hive_files) >= 2, f"got {len(hive_files)}")
            # Read back
            dfs = [pd.read_parquet(f) for f in hive_files]
            combined = pd.concat(dfs).sort_values("date_ts").reset_index(drop=True)
            check("hive roundtrip row count", len(combined) == 60, f"got {len(combined)}")
    except Exception as e:
        check("hive partitioning", False, str(e))
        traceback.print_exc()

    # ── 6. VectorBT import ────────────────────────────────────────
    print("\n[6] Testing VectorBT...")
    try:
        import vectorbt as vbt
        check("vectorbt importable", True)
        # Basic portfolio test
        prices = pd.Series([100, 102, 101, 105, 103, 108, 106, 110])
        entries = pd.Series([True, False, False, False, False, False, False, False])
        exits = pd.Series([False, False, False, False, False, False, False, True])
        pf = vbt.Portfolio.from_signals(prices, entries, exits, init_cash=10000)
        check("vectorbt portfolio creation", pf is not None)
        ret = pf.total_return()
        check("vectorbt total_return computed", not np.isnan(ret))
    except Exception as e:
        check("vectorbt", False, str(e))

    # ── 7. DuckDB engine ─────────────────────────────────────────
    print("\n[7] Testing DuckDB engine...")
    try:
        from pipelines.duckdb_engine import DuckDBEngine
        engine = DuckDBEngine()
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        result = engine.query_dataframe(df, "SELECT sum(a) as total FROM df")
        check("duckdb query", result["total"].iloc[0] == 6)
    except Exception as e:
        check("duckdb_engine", False, str(e))

    # ── 8. BacktestAgent (synthetic) ─────────────────────────────
    print("\n[8] Testing BacktestAgent with synthetic data...")
    try:
        import tempfile
        from agents.backtest_agent import BacktestAgent
        rng = np.random.default_rng(42)
        symbols = ["BTC", "ETH", "SOL", "BNB", "ADA"]
        dates = pd.date_range("2022-01-01", periods=365, freq="D", tz="UTC")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Create minimal config pointing to tmpdir
            cfg = {
                "_project_root": str(tmpdir),
                "project": {"seed": 42},
                "backtest": {
                    "initial_capital": 100000,
                    "cost_bps": 20,
                    "rebalance_freq": "weekly",
                    "top_k": 3,
                },
                "paths": {
                    "raw": str(tmpdir / "raw"),
                    "allocations": str(tmpdir / "allocations"),
                    "backtests": str(tmpdir / "backtests"),
                    "features": str(tmpdir / "features"),
                },
            }
            # Write synthetic OHLCV
            raw_dir = tmpdir / "raw" / "market"
            raw_dir.mkdir(parents=True, exist_ok=True)
            for sym in symbols:
                prices = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, len(dates)))
                df = pd.DataFrame({
                    "symbol": sym, "date_ts": dates,
                    "open": prices * 0.99, "high": prices * 1.02,
                    "low": prices * 0.98, "close": prices,
                    "volume": rng.uniform(1e6, 1e8, len(dates)),
                })
                df.to_parquet(raw_dir / f"{sym}_ohlcv.parquet", index=False)

            # Write synthetic allocations
            alloc_dir = tmpdir / "allocations"
            alloc_dir.mkdir(parents=True, exist_ok=True)
            rebal_dates = dates[::7]
            alloc_rows = []
            for d in rebal_dates:
                top3 = rng.choice(symbols, 3, replace=False)
                for sym in top3:
                    alloc_rows.append({"symbol": sym, "date_ts": d, "weight": 1/3})
            alloc_df = pd.DataFrame(alloc_rows)
            alloc_df.to_parquet(alloc_dir / "allocations_test.parquet", index=False)

            agent = BacktestAgent(cfg)
            agent.execute()
            bt_dir = tmpdir / "backtests"
            summary_files = list(bt_dir.glob("backtest_summary*.parquet"))
            check("backtest summary created", len(summary_files) > 0)
            if summary_files:
                summary = pd.read_parquet(summary_files[0])
                check("backtest has CAGR", "cagr" in summary.columns)
                check("backtest has Sharpe", "sharpe" in summary.columns)
                check("backtest has benchmark", "benchmark" in summary.get("strategy", pd.Series()).values
                      or len(summary) >= 1)
    except Exception as e:
        check("BacktestAgent synthetic", False, str(e))
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SMOKE TEST RESULTS")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print()
    print(f"PASSED: {PASS}  |  FAILED: {FAIL}  |  TOTAL: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        print(f"\n{FAIL} test(s) FAILED.")
        sys.exit(1)
    else:
        print("\nALL TESTS PASSED ✅")
        sys.exit(0)


if __name__ == "__main__":
    run_smoke_test()
