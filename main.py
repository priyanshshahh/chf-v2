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
  python main.py nav               # Accounting: reconcile + independent NAV (gross/net)
  python main.py monitor           # Run all monitoring modules (exit 1 on alert)
  python main.py letter            # Generate the institutional monthly letter
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


def _merge_config_section(cfg, section: str | None):
    if not section:
        return cfg
    if section not in cfg:
        raise KeyError(f"Config section not found: {section}")
    merged = dict(cfg)
    if section.startswith("universe"):
        base_key = "universe"
    elif section.startswith("market_data"):
        base_key = "market_data"
    elif section.startswith("features"):
        base_key = "features"
    elif section.startswith("onchain"):
        base_key = "onchain"
    elif section.startswith("labels"):
        base_key = "labels"
    elif section.startswith("modeling"):
        base_key = "modeling"
    elif section.startswith("backtesting"):
        base_key = "backtesting"
    elif section.startswith("portfolio"):
        base_key = "portfolio"
    elif section.startswith("alpha_research"):
        base_key = "alpha_research"
    else:
        base_key = section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def _get_cfg(args=None):
    from configs.config import load_config
    cfg_path = Path(args.config) if args is not None and getattr(args, "config", None) else None
    cfg = load_config(cfg_path)
    section = getattr(args, "section", None) if args is not None else None
    return _merge_config_section(cfg, section)


def _command_cfg(args=None):
    """Load config while remaining compatible with older monkeypatched tests."""
    try:
        return _get_cfg(args)
    except TypeError:
        return _get_cfg()


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
    cfg = _command_cfg(args)
    agent = UniverseAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[universe] ERROR: UniverseAgent failed.")
        sys.exit(1)
    print(f"[universe] Done. Output: {agent.output_paths}")


def cmd_membership(args):
    """Build the daily point-in-time membership mask from universe_monthly.parquet."""
    from configs.config import resolve_path
    from scripts.build_membership_daily import write_membership_daily
    cfg = _command_cfg(args)
    uni = resolve_path(cfg, "raw") / "universe" / "universe_monthly.parquet"
    out = resolve_path(cfg, "raw") / "universe" / "universe_membership_daily.parquet"
    if not uni.exists():
        print("[membership] ERROR: universe_monthly.parquet missing. Run 'python main.py universe' first.")
        sys.exit(1)
    end_date = cfg.get("universe", {}).get("end_date") or None
    manifest = write_membership_daily(uni, out, end_date=end_date)
    print(f"[membership] Done. rows={manifest['rows']} cmc_ids={manifest['unique_cmc_id']} "
          f"span={manifest['date_min']}..{manifest['date_max']} -> {out}")


def cmd_market(args):
    from agents.market_data_agent import MarketDataAgent
    cfg = _command_cfg(args)
    agent = MarketDataAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[market] ERROR: MarketDataAgent failed.")
        sys.exit(1)
    fetched_assets = int(agent.metrics.get("fetched_assets", 0))
    requested_assets = int(agent.metrics.get("requested_assets", 0))
    full_ohlcv_assets = int(agent.metrics.get("full_ohlcv_assets", 0))
    if fetched_assets <= 0:
        print("[market] ERROR: MarketDataAgent reported zero fetched assets.")
        sys.exit(1)
    print(f"[market] Done. Requested={requested_assets} Fetched={fetched_assets} FullOHLCV={full_ohlcv_assets}")


def cmd_onchain(args):
    from agents.onchain_agent import OnChainAgent
    cfg = _command_cfg(args)
    agent = OnChainAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[onchain] ERROR: OnChainAgent failed.")
        sys.exit(1)
    requested_assets = int(agent.metrics.get("requested_assets", 0))
    assets_with_any = int(agent.metrics.get("assets_with_any_onchain", 0))
    total_observations = int(agent.metrics.get("total_observations", 0))
    if total_observations <= 0:
        print("[onchain] ERROR: OnChainAgent reported zero observations.")
        sys.exit(1)
    print(
        f"[onchain] Done. Requested={requested_assets} "
        f"AssetsWithAny={assets_with_any} Observations={total_observations}"
    )


def cmd_features(args):
    from agents.feature_agent import FeatureAgent
    cfg = _command_cfg(args)
    agent = FeatureAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[features] ERROR: FeatureAgent failed.")
        sys.exit(1)
    market_rows = int(agent.metrics.get("market_rows", 0))
    onchain_rows = int(agent.metrics.get("onchain_rows", 0))
    full_rows = int(agent.metrics.get("full_rows", 0))
    kept = int(agent.metrics.get("final_kept_feature_count", 0))
    if full_rows <= 0:
        print("[features] ERROR: FeatureAgent reported zero full feature rows.")
        sys.exit(1)
    print(
        f"[features] Done. MarketRows={market_rows} "
        f"OnchainRows={onchain_rows} FullRows={full_rows} Features={kept}"
    )


def cmd_labels(args):
    from agents.label_agent import LabelAgent
    cfg = _command_cfg(args)
    agent = LabelAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[labels] ERROR: LabelAgent failed.")
        sys.exit(1)
    horizons = cfg.get("labels", {}).get("horizons", [7, 14, 30])
    matrix_rows = int(agent.metrics.get("label_matrix_rows", 0))
    modeling_rows = int(agent.metrics.get("modeling_dataset_rows", 0))
    symbols = int(agent.metrics.get("modeling_dataset_symbols", 0))
    if matrix_rows <= 0 or modeling_rows <= 0:
        print("[labels] ERROR: LabelAgent reported empty canonical outputs.")
        sys.exit(1)
    print(
        f"[labels] Done. Horizons={horizons} "
        f"LabelMatrixRows={matrix_rows} ModelingRows={modeling_rows} Symbols={symbols}"
    )


def cmd_models(args):
    from agents.model_agent import ModelAgent
    cfg = _command_cfg(args)
    agent = ModelAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[models] ERROR: ModelAgent failed.")
        sys.exit(1)
    rows = int(agent.metrics.get("prediction_rows", 0))
    folds = int(agent.metrics.get("fold_count", 0))
    best_ic = agent.metrics.get("best_rank_ic")
    if rows <= 0 or folds <= 0:
        print("[models] ERROR: ModelAgent reported empty outputs.")
        sys.exit(1)
    print(f"[models] Done. PredictionRows={rows} Folds={folds} BestRankIC={best_ic}")


def cmd_model(args):
    return cmd_models(args)


def cmd_portfolio(args):
    from agents.portfolio_agent import PortfolioAgent
    cfg = _command_cfg(args)
    agent = PortfolioAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[portfolio] ERROR: PortfolioAgent failed.")
        sys.exit(1)
    rows = int(agent.metrics.get("allocation_rows", 0))
    rebalances = int(agent.metrics.get("rebalance_count", 0))
    if rows <= 0:
        print("[portfolio] ERROR: PortfolioAgent reported empty allocations.")
        sys.exit(1)
    print(f"[portfolio] Done. AllocationRows={rows} Rebalances={rebalances}")


def cmd_backtest(args):
    from agents.backtest_agent import BacktestAgent, _VBT_AVAILABLE
    cfg = _command_cfg(args)
    agent = BacktestAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[backtest] ERROR: BacktestAgent failed.")
        sys.exit(1)
    cagr = agent.metrics.get("strategy_cagr")
    sharpe = agent.metrics.get("strategy_sharpe")
    print(f"[backtest] Done. VectorBT used: {_VBT_AVAILABLE} CAGR={cagr} Sharpe={sharpe}")


def cmd_alpha_research(args):
    from agents.alpha_research_agent import AlphaResearchAgent
    cfg = _command_cfg(args)
    agent = AlphaResearchAgent(cfg)
    success = agent.execute(max_retries=1)
    if not success:
        print("[alpha_research] ERROR: AlphaResearchAgent failed.")
        sys.exit(1)
    run = int(agent.metrics.get("experiments_run", 0))
    skipped = int(agent.metrics.get("experiments_skipped", 0))
    passed = bool(agent.metrics.get("any_final_alpha_passed", False))
    if run <= 0:
        print("[alpha_research] ERROR: no experiments completed.")
        sys.exit(1)
    print(f"[alpha_research] Done. Experiments={run} Skipped={skipped} AnyFinalAlphaPassed={passed}")


def cmd_ablation(args):
    import pandas as pd
    from models.ablation import run_ablation, print_ablation_summary
    cfg = _command_cfg(args)
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
    cfg = _command_cfg(args)
    runner = PipelineRunner(cfg)
    results = runner.run_full_pipeline()
    success = all(results.values()) if isinstance(results, dict) and results else False
    if success:
        print("[full] Pipeline completed successfully.")
    else:
        print("[full] Pipeline completed with errors. Check logs.")
        sys.exit(1)


def cmd_papertrade(args):
    """Run one daily paper-trading cycle over the configured books."""
    from papertrade.engine import run_papertrade
    config_path = args.config or "configs/run_config.yaml"
    books = None
    if getattr(args, "books", None):
        books = [b.strip() for b in args.books.split(",") if b.strip()]
    manifest = run_papertrade(config_path, as_of=getattr(args, "as_of", None), books=books)
    ok = 0
    for name, info in manifest.get("books", {}).items():
        line = (
            f"[papertrade] {name}: status={info['status']} "
            f"nav={info['nav']} fills={info['n_fills']} rebalanced={info.get('rebalanced')}"
        )
        if info.get("skip_reason"):
            line += f" reason={info['skip_reason']}"
        else:
            ok += 1
        print(line)
    print(f"[papertrade] Done. as_of={manifest.get('as_of')} ok={ok}/{len(manifest.get('books', {}))}")


def cmd_nav(args):
    """Run dual-book reconciliation, then the independent accounting NAV (all books)."""
    from accounting import nav as accounting_nav
    from accounting import reconcile as accounting_reconcile

    rc = accounting_reconcile.main([])
    if rc != 0:
        print("[nav] ERROR: reconciliation reported a NAV break (see data/accounting/reconciliation_report.json).")
        sys.exit(1)
    print("[nav] Reconciliation OK (data/accounting/reconciliation_report.json).")
    rc = accounting_nav.main([])
    if rc != 0:
        print("[nav] ERROR: accounting NAV build failed (empty ledger? run reconcile first).")
        sys.exit(1)
    print("[nav] Done. NAV series written to data/accounting/nav_<book>.parquet (gross + net-of-fees).")


def cmd_monitor(args):
    """Run every monitoring module in sequence; exit 1 if any module alerts."""
    import importlib
    import json as _json

    modules = [
        "data_quality",
        "signal_health",
        "execution_quality",
        "model_decay",
        "risk_report",
        "shadow_nav",
        "watchdog",
        "champion_challenger",
    ]
    reports_dir = PROJECT_ROOT / "data" / "reports" / "monitoring"
    any_alert = False
    for name in modules:
        mod = importlib.import_module(f"monitoring.{name}")
        rc = mod.main([])
        status, n_alerts = "unknown", 0
        report_path = reports_dir / f"{name}.json"
        if report_path.exists():
            try:
                report = _json.loads(report_path.read_text(encoding="utf-8"))
                status = str(report.get("status", "unknown"))
                n_alerts = len(report.get("alerts") or [])
            except (ValueError, OSError):
                pass
        alerted = rc != 0
        any_alert = any_alert or alerted
        print(f"[monitor] {name}: status={status} alerts={n_alerts} exit={rc}"
              + (" ALERT" if alerted else ""))
    if any_alert:
        print("[monitor] Done with ALERTS. Reports: data/reports/monitoring/*.json")
        sys.exit(1)
    print("[monitor] Done. All monitors clean. Reports: data/reports/monitoring/*.json")


def cmd_letter(args):
    """Generate the institutional monthly letter (virtual paper books)."""
    from reports.monthly_letter import main as letter_main

    rc = letter_main(["--month", args.month] if getattr(args, "month", None) else [])
    if rc != 0:
        print("[letter] ERROR: monthly letter generation failed.")
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
    cfg = _command_cfg(args)
    generate_demo_artifacts(cfg)


def cmd_orchestrate(args):
    """Run the agentic orchestration layer (plan -> risk-check -> execute -> report)."""
    from orchestration import supervisor as orch_supervisor

    config_path = args.config or "configs/run_config.yaml"

    if getattr(args, "list_pending", False):
        pending = orch_supervisor.list_pending(config_path=config_path)
        if not pending:
            print("[orchestrate] No pending approvals.")
        else:
            print(f"[orchestrate] Pending approvals ({len(pending)}):")
            for a in pending:
                print(f"  - step_id={a.step_id} stage={a.stage} goal={a.goal} "
                      f"created={a.created_utc} reason={a.reason}")
        return

    if getattr(args, "approve", None):
        approval = orch_supervisor.approve(args.approve, config_path=config_path)
        if approval is None:
            print(f"[orchestrate] ERROR: no approval record found for step_id={args.approve}. "
                  f"Run a plan (e.g. --dry-run) first to register it.")
            sys.exit(1)
        print(f"[orchestrate] Approved step_id={approval.step_id} "
              f"(approval_id={approval.approval_id}).")
        return

    if not getattr(args, "goal", None):
        print("[orchestrate] ERROR: --goal is required "
              "(one of: refresh_data, refresh_research, daily_ops, full_rebuild), "
              "or use --approve STEP_ID / --list-pending.")
        sys.exit(1)

    result = orch_supervisor.run_goal(
        args.goal,
        config_path=config_path,
        force=bool(getattr(args, "force", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    print(result["report"])
    print(f"[orchestrate] Report written to {result['report_path']}")
    if not result["dry_run"]:
        failed = [r for r in result["records"] if r.status == "failed"]
        if failed:
            print(f"[orchestrate] ERROR: {len(failed)} step(s) failed.")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="CHF — Crypto Hedge Fund Portfolio System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Pipeline stage to run")

    def add_stage_parser(name: str, help_text: str):
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("--config", default=None, help="Path to run_config.yaml")
        p.add_argument("--section", default=None, help="Merge this config section into universe")
        return p

    add_stage_parser("universe", "Run UniverseAgent")
    add_stage_parser("membership", "Build daily PIT membership mask from universe_monthly")
    add_stage_parser("market", "Run MarketDataAgent")
    add_stage_parser("onchain", "Run OnChainAgent")
    add_stage_parser("features", "Run FeatureAgent")
    add_stage_parser("labels", "Run LabelAgent")
    add_stage_parser("models", "Run ModelAgent")
    add_stage_parser("model", "Run ModelAgent")
    add_stage_parser("portfolio", "Run PortfolioAgent")
    add_stage_parser("backtest", "Run BacktestAgent (vectorbt)")
    add_stage_parser("alpha_research", "Run AlphaResearchAgent")
    add_stage_parser("ablation", "Run ablation study")
    add_stage_parser("full", "Run full pipeline end-to-end")
    papertrade_parser = add_stage_parser("papertrade", "Run daily paper-trading engine")
    papertrade_parser.add_argument("--as-of", dest="as_of", default=None, help="Run date YYYY-MM-DD (default: today UTC)")
    papertrade_parser.add_argument("--books", default=None, help="Comma-separated book names (default: all)")
    add_stage_parser("nav", "Run accounting reconciliation + independent NAV (all books)")
    add_stage_parser("monitor", "Run all monitoring modules (exit 1 on any alert)")
    letter_parser = add_stage_parser("letter", "Generate the institutional monthly letter")
    letter_parser.add_argument("--month", default=None, help="Letter month YYYY-MM (default: latest)")
    add_stage_parser("serve", "Start FastAPI server")
    add_stage_parser("schedule", "Start APScheduler daemon")
    add_stage_parser("demo", "Generate demo data for dashboard")
    orchestrate_parser = subparsers.add_parser(
        "orchestrate", help="Run the agentic orchestration layer"
    )
    orchestrate_parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    orchestrate_parser.add_argument(
        "--goal", default=None,
        choices=["refresh_data", "refresh_research", "daily_ops", "full_rebuild"],
        help="Orchestration goal",
    )
    orchestrate_parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                                    help="Print the plan + approval requirements without executing")
    orchestrate_parser.add_argument("--force", action="store_true",
                                    help="Re-run stages even when artifacts are fresh")
    orchestrate_parser.add_argument("--approve", default=None, metavar="STEP_ID",
                                    help="Approve a pending step (e.g. daily_ops:model)")
    orchestrate_parser.add_argument("--list-pending", dest="list_pending", action="store_true",
                                    help="List pending approval records")

    args = parser.parse_args()

    commands = {
        "universe": cmd_universe,
        "membership": cmd_membership,
        "market": cmd_market,
        "onchain": cmd_onchain,
        "features": cmd_features,
        "labels": cmd_labels,
        "models": cmd_models,
        "model": cmd_model,
        "portfolio": cmd_portfolio,
        "backtest": cmd_backtest,
        "alpha_research": cmd_alpha_research,
        "ablation": cmd_ablation,
        "full": cmd_full,
        "papertrade": cmd_papertrade,
        "nav": cmd_nav,
        "monitor": cmd_monitor,
        "letter": cmd_letter,
        "serve": cmd_serve,
        "schedule": cmd_schedule,
        "demo": cmd_demo,
        "orchestrate": cmd_orchestrate,
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
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] {args.command} failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
