#!/usr/bin/env python3
"""
CHF — Crypto Hedge Fund Portfolio System
==========================================
Single CLI entrypoint for all pipeline stages.

Usage
-----
  python main.py universe          # Run UniverseAgent
  python main.py market            # Run MarketDataAgent
  python main.py onchain           # Run OnChainAgent
  python main.py features          # Run FeatureAgent
  python main.py labels            # Run LabelAgent
  python main.py models            # Run ModelAgent (RF + LightGBM)
  python main.py portfolio         # Run PortfolioAgent
  python main.py backtest          # Run BacktestAgent (vectorbt)
  python main.py ablation          # Run ablation study
  python main.py full              # Run entire pipeline end-to-end
  python main.py serve             # Start FastAPI server
  python main.py schedule          # Start APScheduler daemon
  python main.py demo              # Generate demo data for dashboard
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _get_cfg():
    from configs.config import load_config
    return load_config()


def _feature_store_candidates(feat_dir: Path):
    """Return feature-store candidates in preferred order."""
    return [
        feat_dir / "full_features.parquet",
        feat_dir / "market_features.parquet",
        *sorted(feat_dir.glob("feature_store*.parquet")),
    ]


def generate_demo_artifacts(cfg):
    """Generate canonical synthetic artifacts for dashboard/tests."""
    import numpy as np
    import pandas as pd
    from configs.config import resolve_path

    print("[demo] Generating synthetic demo data...")

    symbols = ["BTC", "ETH", "SOL", "BNB", "ADA"]
    dates = pd.date_range("2023-01-01", periods=550, freq="D", tz="UTC")
    rng = np.random.default_rng(42)
    snapshot_id = "demo"
    run_id = "demo"

    # Market data
    market_dir = resolve_path(cfg, "raw") / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    price_map = {}
    for sym in symbols:
        prices = 100 * np.cumprod(1 + rng.normal(0.001, 0.03, len(dates)))
        price_map[sym] = prices
        df = pd.DataFrame({
            "symbol": sym,
            "date_ts": dates,
            "open": prices * 0.99,
            "high": prices * 1.02,
            "low": prices * 0.98,
            "close": prices,
            "volume": rng.uniform(1e6, 1e8, len(dates)),
            "snapshot_id": snapshot_id,
        })
        df.to_parquet(market_dir / f"{sym}_ohlcv.parquet", index=False)
    print(f"  [demo] Market data: {len(symbols)} symbols × {len(dates)} days")

    # Features
    feat_dir = resolve_path(cfg, "features")
    feat_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for sym in symbols:
        for idx, d in enumerate(dates):
            rows.append({
                "symbol": sym,
                "date_ts": d,
                "ret_7d": rng.normal(0, 0.2),
                "ret_30d": rng.normal(0, 0.3),
                "vol_30d": abs(rng.normal(0.5, 0.2)),
                "skew_30d": rng.normal(0, 0.5),
                "beta_btc_60d": rng.normal(1, 0.3),
                "vol_ratio_30d": abs(rng.normal(1, 0.2)),
                "reversal_3_30": rng.normal(0, 0.2),
                "atr_14d": abs(rng.normal(2.0, 0.8)),
                "nvt_ratio": abs(rng.normal(10, 3)),
                "mvrv_proxy": abs(rng.normal(1.5, 0.4)),
                "tvl_ratio": abs(rng.normal(0.2, 0.05)),
                "feature_version": "demo",
                "snapshot_id": snapshot_id,
                "run_id": run_id,
            })
    feat_df = pd.DataFrame(rows)
    feat_df.to_parquet(feat_dir / "market_features.parquet", index=False)
    feat_df.to_parquet(feat_dir / "full_features.parquet", index=False)
    feat_df.to_parquet(feat_dir / "feature_store_demo.parquet", index=False)
    print(f"  [demo] Features: {feat_df.shape}")

    # Labels
    label_dir = resolve_path(cfg, "labels")
    label_dir.mkdir(parents=True, exist_ok=True)
    for horizon in (7, 14, 30):
        label_rows = []
        for sym in symbols:
            prices = price_map[sym]
            for i, d in enumerate(dates[:-horizon]):
                fwd_ret = float(np.log(prices[i + horizon] / max(prices[i], 1e-10)))
                label_rows.append({
                    "symbol": sym,
                    "date_ts": d,
                    "horizon_days": horizon,
                    "label_value": fwd_ret,
                    "label_type": "log_return",
                    "is_complete": True,
                    "snapshot_id": snapshot_id,
                    "run_id": run_id,
                })
        label_df = pd.DataFrame(label_rows)
        label_df.to_parquet(label_dir / f"labels_{horizon}d.parquet", index=False)
    print(f"  [demo] Labels: {(len(symbols) * (len(dates) - 7), 8)}")

    # Predictions
    pred_dir = resolve_path(cfg, "predictions")
    pred_dir.mkdir(parents=True, exist_ok=True)
    pred_rows = []
    latest_dates = dates[-90:]
    for sym in symbols:
        prices = price_map[sym]
        for d in latest_dates:
            day_idx = int((d - dates[0]).days)
            actual = float(np.log(prices[min(day_idx + 7, len(prices) - 1)] / prices[day_idx]))
            predicted = actual + float(rng.normal(0, 0.02))
            pred_rows.append({
                "symbol": sym,
                "date_ts": d,
                "predicted_return": predicted,
                "actual_return": actual,
                "fold_id": 0,
                "model_name": "lightgbm",
                "horizon_days": 7,
                "model_version": "demo",
                "feature_version": "demo",
                "snapshot_id": snapshot_id,
                "run_id": run_id,
            })
    pred_df = pd.DataFrame(pred_rows)
    pred_df.to_parquet(pred_dir / "predictions_lightgbm_h7d.parquet", index=False)
    metrics_df = {
        "model_name": "lightgbm",
        "horizon_days": 7,
        "rank_ic_mean": 0.08,
        "rank_ic_std": 0.03,
        "hit_rate_mean": 0.57,
        "n_folds": 1,
        "feature_version": "demo",
        "snapshot_id": snapshot_id,
        "run_id": run_id,
    }
    import json
    with open(pred_dir / "metrics_lightgbm_h7d.json", "w") as f:
        json.dump(metrics_df, f, indent=2)
    print(f"  [demo] Predictions: {pred_df.shape}")

    # Allocations
    alloc_dir = resolve_path(cfg, "allocations")
    alloc_dir.mkdir(parents=True, exist_ok=True)
    alloc_rows = []
    tx_rows = []
    rebal_dates = dates[-90::7]
    prev_weights = {}
    for d in rebal_dates:
        day_preds = pred_df[pred_df["date_ts"] == d].nlargest(3, "predicted_return")
        new_weights = {}
        for rank, (_, row) in enumerate(day_preds.iterrows(), start=1):
            weight = 1 / 3
            alloc_rows.append({
                "symbol": row["symbol"],
                "date_ts": d,
                "weight": weight,
                "rank": rank,
                "signal_score": row["predicted_return"],
                "strategy": "top_k_equal_weight",
                "top_k": 3,
                "run_id": run_id,
                "snapshot_id": snapshot_id,
            })
            new_weights[row["symbol"]] = weight
        all_symbols = set(prev_weights) | set(new_weights)
        for sym in all_symbols:
            before = prev_weights.get(sym, 0.0)
            after = new_weights.get(sym, 0.0)
            if abs(after - before) > 1e-8:
                tx_rows.append({
                    "date_ts": d,
                    "symbol": sym,
                    "action": "BUY" if after >= before else "SELL",
                    "weight_before": before,
                    "weight_after": after,
                    "turnover": abs(after - before),
                    "cost_bps": 20,
                    "run_id": run_id,
                })
        prev_weights = new_weights
    alloc_df = pd.DataFrame(alloc_rows)
    alloc_df.to_parquet(alloc_dir / "allocations_top_k_equal_weight.parquet", index=False)
    latest = alloc_df[alloc_df["date_ts"] == alloc_df["date_ts"].max()].copy()
    latest.to_parquet(alloc_dir / "latest_allocation.parquet", index=False)
    pd.DataFrame(tx_rows).to_parquet(alloc_dir / "allocations_transaction_log.parquet", index=False)
    print(f"  [demo] Allocations: {alloc_df.shape}")

    # Backtest summary
    bt_dir = resolve_path(cfg, "backtests")
    bt_dir.mkdir(parents=True, exist_ok=True)
    bt_summary = pd.DataFrame([
        {"strategy": "main", "cagr": 0.45, "sharpe": 1.8,
         "sortino": 2.1, "calmar": 3.2, "max_drawdown": -0.14,
         "annualized_vol": 0.25, "total_return": 0.42, "n_days": 90,
         "cost_bps": 20, "backtest_name": "main"},
        {"strategy": "benchmark_BTC", "cagr": 0.30, "sharpe": 1.2,
         "sortino": 1.5, "calmar": 2.0, "max_drawdown": -0.20,
         "annualized_vol": 0.35, "total_return": 0.28, "n_days": 90,
         "cost_bps": 0, "backtest_name": "benchmark_BTC"},
        {"strategy": "benchmark_ETH", "cagr": 0.34, "sharpe": 1.35,
         "sortino": 1.65, "calmar": 2.2, "max_drawdown": -0.19,
         "annualized_vol": 0.33, "total_return": 0.31, "n_days": 90,
         "cost_bps": 0, "backtest_name": "benchmark_ETH"},
        {"strategy": "benchmark_EW_top100", "cagr": 0.25, "sharpe": 1.0,
         "sortino": 1.2, "calmar": 1.8, "max_drawdown": -0.18,
         "annualized_vol": 0.30, "total_return": 0.23, "n_days": 90,
         "cost_bps": 20, "backtest_name": "benchmark_EW_top100"},
    ])
    bt_summary.to_parquet(bt_dir / "backtest_summary.parquet", index=False)
    eq_rows = []
    return_profiles = {
        "main": (0.0014, 0.018),
        "benchmark_BTC": (0.0010, 0.026),
        "benchmark_ETH": (0.0011, 0.024),
        "benchmark_EW_top100": (0.0009, 0.022),
    }
    for backtest_name, (mu, sigma) in return_profiles.items():
        pv = 100_000.0
        for d in dates[-90:]:
            daily_return = float(rng.normal(mu, sigma))
            pv *= (1 + daily_return)
            eq_rows.append({
                "date_ts": d,
                "portfolio_value": pv,
                "daily_return": daily_return,
                "backtest_name": backtest_name,
            })
    eq_df = pd.DataFrame(eq_rows)
    eq_df.to_parquet(bt_dir / "equity_curves.parquet", index=False)
    with open(bt_dir / "vbt_stats.json", "w") as f:
        json.dump({"main": {"total_return": 0.42, "sharpe_ratio": 1.8}}, f, indent=2)
    from reports import evaluate_risk_adjusted_alpha, render_alpha_report_markdown
    reports_dir = resolve_path(cfg, "reports")
    alpha_report = evaluate_risk_adjusted_alpha(eq_df, bt_summary)
    (reports_dir / "alpha_report.json").write_text(json.dumps(alpha_report, indent=2))
    (reports_dir / "alpha_report.md").write_text(render_alpha_report_markdown(alpha_report))
    print("  [demo] Backtest data written")

    print("[demo] Demo data generation complete. Launch dashboard with:")
    print("  streamlit run app/dashboard.py")


def cmd_universe(args):
    from agents.universe_agent import UniverseAgent
    cfg = _get_cfg()
    agent = UniverseAgent(cfg)
    agent.execute()
    print(f"[universe] Done. Output: {agent.output_paths}")


def cmd_market(args):
    from agents.market_data_agent import MarketDataAgent
    cfg = _get_cfg()
    agent = MarketDataAgent(cfg)
    agent.execute()
    print(f"[market] Done. Symbols fetched: {agent.metrics.get('symbols_fetched', 0)}")


def cmd_onchain(args):
    from agents.onchain_agent import OnChainAgent
    cfg = _get_cfg()
    agent = OnChainAgent(cfg)
    agent.execute()
    print(f"[onchain] Done. Output: {agent.output_paths}")


def cmd_features(args):
    from agents.feature_agent import FeatureAgentV1, FeatureAgentV2
    cfg = _get_cfg()
    v1 = FeatureAgentV1(cfg)
    success_v1 = v1.execute()
    if not success_v1:
        print("[features] ERROR: FeatureAgentV1 failed.")
        sys.exit(1)

    v2 = FeatureAgentV2(cfg)
    success_v2 = v2.execute()
    if not success_v2:
        print("[features] ERROR: FeatureAgentV2 failed.")
        sys.exit(1)

    outputs = {**v1.output_paths, **v2.output_paths}
    print(f"[features] Done. Output: {outputs}")


def cmd_labels(args):
    from agents.label_agent import LabelAgent
    cfg = _get_cfg()
    agent = LabelAgent(cfg)
    agent.execute()
    print(f"[labels] Done. Output: {agent.output_paths}")


def cmd_models(args):
    from agents.model_agent import ModelAgent
    cfg = _get_cfg()
    agent = ModelAgent(cfg)
    agent.execute()
    print(f"[models] Done. Metrics: {agent.metrics}")


def cmd_portfolio(args):
    from agents.portfolio_agent import PortfolioAgent
    cfg = _get_cfg()
    agent = PortfolioAgent(cfg)
    agent.execute()
    print(f"[portfolio] Done. Output: {agent.output_paths}")


def cmd_backtest(args):
    from agents.backtest_agent import BacktestAgent, _VBT_AVAILABLE
    cfg = _get_cfg()
    agent = BacktestAgent(cfg)
    agent.execute()
    print(f"[backtest] Done. VectorBT used: {_VBT_AVAILABLE}")
    print(f"[backtest] Output: {agent.output_paths}")


def cmd_ablation(args):
    import pandas as pd
    from models.ablation import run_ablation, print_ablation_summary
    cfg = _get_cfg()
    from configs.config import resolve_path
    feat_dir = resolve_path(cfg, "features")
    label_dir = resolve_path(cfg, "labels")
    report_dir = resolve_path(cfg, "reports")

    feat_files = [p for p in _feature_store_candidates(feat_dir) if p.exists()]
    default_horizon = cfg.get("modeling", {}).get("default_horizon", 7)
    preferred_labels = [
        label_dir / f"labels_{default_horizon}d.parquet",
        *sorted(label_dir.glob("labels_*.parquet")),
    ]
    label_files = [p for p in preferred_labels if p.exists()]

    if not feat_files:
        print("[ablation] ERROR: No feature store found. Run 'python main.py features' first.")
        sys.exit(1)
    if not label_files:
        print("[ablation] ERROR: No labels found. Run 'python main.py labels' first.")
        sys.exit(1)

    feat_df = pd.read_parquet(feat_files[0])
    label_df = pd.read_parquet(label_files[0])

    results = run_ablation(feat_df, label_df, cfg, output_dir=report_dir)
    print_ablation_summary(results)
    print(f"[ablation] Results saved to {report_dir / 'ablation_results.json'}")


def cmd_full(args):
    from pipelines.pipeline_runner import PipelineRunner
    cfg = _get_cfg()
    runner = PipelineRunner(cfg)
    results = runner.run_full_pipeline()
    success = all(results.values()) if isinstance(results, dict) and results else False
    if success:
        print("[full] Pipeline completed successfully.")
    else:
        print("[full] Pipeline completed with errors. Check logs.")
        sys.exit(1)


def cmd_serve(args):
    import uvicorn
    print("[serve] Starting FastAPI server on http://0.0.0.0:8000")
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=False)


def cmd_schedule(args):
    from jobs.scheduler import start_scheduler
    print("[schedule] Starting APScheduler daemon...")
    start_scheduler()


def cmd_demo(args):
    """Generate canonical synthetic demo data so the dashboard and tests can load."""
    cfg = _get_cfg()
    generate_demo_artifacts(cfg)


def main():
    parser = argparse.ArgumentParser(
        description="CHF — Crypto Hedge Fund Portfolio System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Pipeline stage to run")

    subparsers.add_parser("universe", help="Run UniverseAgent")
    subparsers.add_parser("market", help="Run MarketDataAgent")
    subparsers.add_parser("onchain", help="Run OnChainAgent")
    subparsers.add_parser("features", help="Run FeatureAgent")
    subparsers.add_parser("labels", help="Run LabelAgent")
    subparsers.add_parser("models", help="Run ModelAgent")
    subparsers.add_parser("portfolio", help="Run PortfolioAgent")
    subparsers.add_parser("backtest", help="Run BacktestAgent (vectorbt)")
    subparsers.add_parser("ablation", help="Run ablation study")
    subparsers.add_parser("full", help="Run full pipeline end-to-end")
    subparsers.add_parser("serve", help="Start FastAPI server")
    subparsers.add_parser("schedule", help="Start APScheduler daemon")
    subparsers.add_parser("demo", help="Generate demo data for dashboard")

    args = parser.parse_args()

    commands = {
        "universe": cmd_universe,
        "market": cmd_market,
        "onchain": cmd_onchain,
        "features": cmd_features,
        "labels": cmd_labels,
        "models": cmd_models,
        "portfolio": cmd_portfolio,
        "backtest": cmd_backtest,
        "ablation": cmd_ablation,
        "full": cmd_full,
        "serve": cmd_serve,
        "schedule": cmd_schedule,
        "demo": cmd_demo,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    fn = commands.get(args.command)
    if fn is None:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        sys.exit(1)

    try:
        fn(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] {args.command} failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
