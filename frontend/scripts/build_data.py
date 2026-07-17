"""Bake real CHF pipeline artifacts into JSON for the web frontend.

Reads the parquet/JSON files the pipeline already produced under ``data/`` and
writes small, web-friendly JSON files into ``frontend/public/data/`` which the
React app fetches at runtime. The site is truthful (driven by real pipeline
output) while remaining a static, shareable build.

Run from anywhere:
    python3 frontend/scripts/build_data.py

Emits one dataset per pipeline agent (universe, market, onchain, features,
labels, models, portfolio, backtest) plus shared summary/equity/etc.

Research-integrity notes:
- The canonical alpha verdict comes from ``data/backtests/`` (alpha_verified
  is false), NOT the stale demo file in ``data/reports/alpha_report.json``.
- Leakage columns (e.g. ``actual_return``) are dropped from the signals feed.
- Flat placeholder benchmark series (BTC/ETH/cash w/o loaded prices) are
  dropped from the equity chart so the site never draws fake-looking lines.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = ROOT / "frontend" / "public" / "data"

STRATEGIES = [
    "score_weighted_long_only",
    "score_weighted_vol_scaled",
    "top_10_equal_weight",
    "top_10_vol_scaled",
    "top_20_equal_weight",
    "top_20_vol_scaled",
    "top_5_equal_weight",
    "top_5_vol_scaled",
    "turnover_controlled",
]


# --------------------------------------------------------------------- io ---
def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        obj = float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _write(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(_clean(payload), indent=2))
    print(f"  wrote {name}")


def _pq(rel):
    p = DATA / rel
    if not p.exists():
        print(f"  SKIP (missing): data/{rel}")
        return None
    return pd.read_parquet(p)


def _js(rel):
    p = DATA / rel
    return json.loads(p.read_text()) if p.exists() else None


def _dt(s):
    return pd.to_datetime(s, utc=True)


def _downsample(df, target=200):
    if len(df) <= target:
        return df
    step = max(1, len(df) // target)
    keep = df.iloc[::step]
    if keep.index[-1] != df.index[-1]:
        keep = pd.concat([keep, df.iloc[[-1]]])
    return keep


# ---------------------------------------------------------------- shared ---
def build_summary():
    manifest = _js("backtests/backtest_manifest.json") or {}
    alpha = _js("backtests/alpha_report.json") or {}
    lb = _pq("predictions/alpha_model_leaderboard.parquet")
    abl = _js("reports/ablation_results.json") or {}
    comp = _pq("backtests/strategy_comparison.parquet")

    s = {
        "alpha_verified": bool(manifest.get("alpha_verified", False)),
        "best_strategy_by_sharpe": alpha.get("best_strategy_by_sharpe"),
        "diagnostic_note": alpha.get("diagnostic_note"),
        "survivorship_limitation": alpha.get("survivorship_bias_limitation"),
        "n_strategies": int(len(comp)) if comp is not None else len(STRATEGIES),
        "n_experiments": int(len(lb)) if lb is not None else 0,
        "n_benchmarks": len(manifest.get("benchmark_names", []) or []),
        "transaction_cost_bps": manifest.get("transaction_cost_bps"),
        "cost_sweep_bps": manifest.get("cost_sweep_bps"),
        "benchmark_sanity_passed": manifest.get("benchmark_sanity_passed"),
        "best_rank_ic": float(lb["mean_rank_ic"].max()) if lb is not None else None,
        "onchain_features_help": abl.get("onchain_features_help"),
        "onchain_marginal_ic_lift": abl.get("onchain_marginal_ic_lift"),
    }
    eq = _pq("backtests/equity_curves.parquet")
    if eq is not None and not eq.empty:
        eq["date_ts"] = _dt(eq["date_ts"])
        s["window_start"] = eq["date_ts"].min().strftime("%Y-%m-%d")
        s["window_end"] = eq["date_ts"].max().strftime("%Y-%m-%d")
        s["n_days"] = int(eq[eq["strategy_name"] == STRATEGIES[0]]["date_ts"].nunique())
        ewu = eq[eq["strategy_name"] == "equal_weight_universe"]
        if not ewu.empty:
            s["universe_size"] = int(ewu["n_positions"].max())
    cov = _pq("raw/universe/universe_coverage_report.parquet")
    if cov is not None:
        s["universe_snapshots"] = int(len(cov))
        s["avg_eligible"] = float(cov["eligible_count"].mean())
    mk = _pq("raw/market/market_ohlcv.parquet")
    if mk is not None and "symbol" in mk.columns:
        s["market_symbols"] = int(mk["symbol"].nunique())
    _write("summary.json", s)


def build_equity():
    eq = _pq("backtests/equity_curves.parquet")
    if eq is None:
        return
    eq["date_ts"] = _dt(eq["date_ts"])
    series = []
    for name in STRATEGIES + ["equal_weight_universe"]:
        sub = eq[eq["strategy_name"] == name].sort_values("date_ts")
        if sub.empty:
            continue
        v = sub["portfolio_value"]
        if float(v.max()) - float(v.min()) < 1e-6:
            continue
        sub = _downsample(sub.reset_index(drop=True))
        series.append({
            "name": name,
            "is_benchmark": name == "equal_weight_universe",
            "points": [{"date": d.strftime("%Y-%m-%d"), "value": round(float(x) / 1000.0, 3)}
                       for d, x in zip(sub["date_ts"], sub["portfolio_value"])],
        })
    _write("equity.json", {"start_value": 100.0, "series": series})


def build_strategies():
    comp = _pq("backtests/strategy_comparison.parquet")
    if comp is None:
        return
    cols = ["strategy_name", "Sharpe", "CAGR", "total_return", "max_drawdown",
            "average_turnover", "total_cost_drag", "beats_btc", "beats_eth",
            "beats_equal_weight", "alpha_status"]
    cols = [c for c in cols if c in comp.columns]
    _write("strategies.json",
           comp[cols].sort_values("total_return", ascending=False).to_dict("records"))


def build_cost_sweep():
    cs = _pq("backtests/cost_sweep.parquet")
    if cs is None:
        return
    out = []
    for name, g in cs.groupby("strategy_name"):
        g = g.sort_values("cost_bps")
        out.append({"name": name, "points": [
            {"cost_bps": float(b), "total_return": float(r), "sharpe": float(s)}
            for b, r, s in zip(g["cost_bps"], g["total_return"], g["Sharpe"])]})
    _write("cost_sweep.json", out)


def build_signals():
    p = DATA / "predictions" / "predictions_lightgbm_h7d.parquet"
    if not p.exists():
        return
    df = pd.read_parquet(p)
    df["date_ts"] = _dt(df["date_ts"])
    snap = df[df["date_ts"] == df["date_ts"].max()].copy()
    leak = ("actual", "realized", "future", "label", "target", "fwd")
    snap = snap[[c for c in snap.columns if not any(t in c.lower() for t in leak)]]
    snap = snap.sort_values("predicted_return", ascending=False).head(15)
    _write("signals.json", {
        "as_of": df["date_ts"].max().strftime("%Y-%m-%d"),
        "model": "lightgbm", "horizon_days": 7,
        "note": "Forward-looking signal ranks only. Realized returns withheld (leakage-safe).",
        "rows": [{"symbol": r["symbol"], "predicted_return": float(r["predicted_return"])}
                 for _, r in snap.iterrows()],
    })


# -------------------------------------------------------- agent: universe ---
def build_universe():
    cov = _pq("raw/universe/universe_coverage_report.parquet")
    if cov is None:
        return
    cov = cov.copy()
    cov["d"] = _dt(cov["snapshot_date"])
    cov = cov.sort_values("d")
    coverage = [{
        "date": d.strftime("%Y-%m"),
        "candidate": int(c), "eligible": int(e), "excluded": int(x), "final": int(f),
    } for d, c, e, x, f in zip(cov["d"], cov["candidate_count"], cov["eligible_count"],
                              cov["excluded_count"], cov["final_count"])]
    reason_cols = {
        "Stablecoin": "stablecoin_excluded_count",
        "Immature (<365d)": "maturity_excluded_count",
        "No on-chain data": "onchain_coverage_excluded_count",
        "Wrapped": "wrapped_excluded_count",
        "Bridged": "bridged_excluded_count",
        "LST": "lst_excluded_count",
        "Not tradable": "tradability_excluded_count",
    }
    exclusions = [{"reason": k, "count": int(cov[v].sum())}
                  for k, v in reason_cols.items() if v in cov.columns]
    exclusions = [e for e in exclusions if e["count"] > 0]
    exclusions.sort(key=lambda r: -r["count"])
    _write("universe.json", {
        "snapshots": int(len(cov)),
        "avg_eligible": float(cov["eligible_count"].mean()),
        "avg_candidates": float(cov["candidate_count"].mean()),
        "coverage": coverage,
        "exclusions": exclusions,
        "survivorship_note": str(cov["limitations"].iloc[0]) if "limitations" in cov else "",
    })


# ---------------------------------------------------------- agent: market ---
def build_market():
    mk = _pq("raw/market/market_ohlcv.parquet")
    if mk is None:
        return
    mk = mk.copy()
    mk["date_ts"] = _dt(mk["date_ts"])
    mk["ym"] = mk["date_ts"].dt.strftime("%Y-%m")
    cov = (mk.groupby("ym")["symbol"].nunique().reset_index()
             .rename(columns={"symbol": "n_symbols"}).sort_values("ym"))
    coverage = [{"date": r.ym, "n_symbols": int(r.n_symbols)} for r in cov.itertuples()]
    vcol = "dollar_volume_usd" if "dollar_volume_usd" in mk.columns else "volume"
    latest = mk[mk["date_ts"] == mk["date_ts"].max()]
    top = (latest.groupby("symbol")[vcol].mean().sort_values(ascending=False).head(15))
    top_assets = [{"symbol": s, "dollar_volume": float(v)} for s, v in top.items()]
    _write("market.json", {
        "n_symbols": int(mk["symbol"].nunique()),
        "n_rows": int(len(mk)),
        "date_start": mk["date_ts"].min().strftime("%Y-%m-%d"),
        "date_end": mk["date_ts"].max().strftime("%Y-%m-%d"),
        "providers": sorted([str(x) for x in mk["source"].dropna().unique()])[:6]
                     if "source" in mk.columns else [],
        "coverage": coverage,
        "top_assets": top_assets,
    })


# --------------------------------------------------------- agent: onchain ---
def build_onchain():
    abl = _js("reports/ablation_results.json") or {}
    out = {
        "onchain_features_help": abl.get("onchain_features_help"),
        "onchain_marginal_ic_lift": abl.get("onchain_marginal_ic_lift"),
        "sets": [],
        "features": [],
    }
    for key in ("market_only", "market_plus_onchain"):
        s = abl.get(key)
        if s:
            out["sets"].append({"name": key, "n_features": s.get("n_features"),
                                "mean_rank_ic": s.get("mean_rank_ic"),
                                "mean_hit_rate": s.get("mean_hit_rate"),
                                "fold_ics": s.get("fold_ics")})
    cov = _pq("features/feature_coverage_report.parquet")
    if cov is not None and "feature_group" in cov.columns:
        onc = cov[cov["feature_group"].astype(str).str.contains("onchain|on_chain|chain", case=False, na=False)]
        out["n_onchain_features"] = int(len(onc))
        out["features"] = [{"feature": r.feature_name, "null_pct": float(r.null_pct)}
                           for r in onc.sort_values("null_pct").head(14).itertuples()]
    _write("onchain.json", out)
    _write("ablation.json", {
        "onchain_features_help": out["onchain_features_help"],
        "onchain_marginal_ic_lift": out["onchain_marginal_ic_lift"],
        "sets": out["sets"],
    })


# -------------------------------------------------------- agent: features ---
def build_features():
    cov = _pq("features/feature_coverage_report.parquet")
    if cov is None:
        return
    by_group = (cov.groupby("feature_group").size().reset_index(name="count")
                  .sort_values("count", ascending=False))
    groups = [{"group": str(r.feature_group), "count": int(r.count)} for r in by_group.itertuples()]
    worst = cov.sort_values("null_pct", ascending=False).head(12)
    worst_cov = [{"feature": r.feature_name, "group": str(r.feature_group),
                  "null_pct": float(r.null_pct)} for r in worst.itertuples()]
    qa_pass = int(cov["passed_qa"].sum()) if "passed_qa" in cov.columns else None
    _write("features.json", {
        "n_features": int(len(cov)),
        "n_groups": int(cov["feature_group"].nunique()),
        "qa_pass": qa_pass,
        "by_group": groups,
        "worst_coverage": worst_cov,
        "avg_null_pct": float(cov["null_pct"].mean()),
    })


# ---------------------------------------------------------- agent: labels ---
def build_labels():
    rep = _pq("labels/label_coverage_report.parquet")
    horizons = []
    if rep is not None:
        for r in rep.itertuples():
            horizons.append({
                "horizon": int(r.horizon_days),
                "valid_rows": int(r.valid_label_rows),
                "positive": int(r.positive_label_count),
                "negative": int(r.negative_label_count),
                "mean": float(r.mean_label), "std": float(r.std_label),
                "p01": float(r.p01_label), "p99": float(r.p99_label),
            })
        horizons.sort(key=lambda h: h["horizon"])
    hist = []
    lm = _pq("labels/labels_14d.parquet")
    if lm is not None and "label_value" in lm.columns:
        vals = lm["label_value"].dropna().clip(-1, 1).to_numpy()
        counts, edges = np.histogram(vals, bins=40, range=(-1, 1))
        hist = [{"x": round(float((edges[i] + edges[i + 1]) / 2), 3), "count": int(counts[i])}
                for i in range(len(counts))]
    _write("labels.json", {"horizons": horizons, "histogram": hist,
                           "histogram_horizon": 14})


# ---------------------------------------------------------- agent: models ---
def build_models():
    lb = _pq("predictions/alpha_model_leaderboard.parquet")
    if lb is None:
        return
    cols = ["model_name", "feature_set", "label_target", "horizon_days", "mean_rank_ic",
            "rank_ic_tstat", "hit_rate", "stability_score", "signal_gate_passed",
            "final_alpha_status"]
    cols = [c for c in cols if c in lb.columns]
    top = lb.sort_values("rank_ic_tstat", ascending=False).head(15)[cols]
    rows = top.to_dict("records")
    by_model = (lb.groupby("model_name")["mean_rank_ic"].mean().reset_index()
                  .sort_values("mean_rank_ic", ascending=False))
    by_horizon = (lb.groupby("horizon_days")["mean_rank_ic"].mean().reset_index()
                    .sort_values("horizon_days"))
    scatter = [{"ic": float(r.mean_rank_ic), "tstat": float(r.rank_ic_tstat),
                "model": r.model_name,
                "passed": bool(r.signal_gate_passed) if "signal_gate_passed" in lb.columns else False}
               for r in lb.itertuples()]
    _write("models.json", {
        "n_experiments": int(len(lb)),
        "n_passed_gate": int(lb["signal_gate_passed"].sum()) if "signal_gate_passed" in lb.columns else 0,
        "leaderboard": rows,
        "by_model": [{"model": r.model_name, "ic": float(r.mean_rank_ic)} for r in by_model.itertuples()],
        "by_horizon": [{"horizon": int(r.horizon_days), "ic": float(r.mean_rank_ic)} for r in by_horizon.itertuples()],
        "scatter": scatter,
    })
    _write("leaderboard.json", rows[:12])


# ------------------------------------------------------- agent: portfolio ---
def build_portfolio():
    al = _pq("allocations/allocations_from_predictions.parquet")
    if al is None:
        return
    al = al.copy()
    al["date_ts"] = _dt(al["date_ts"])
    strat = "score_weighted_long_only"
    sub = al[al["strategy_name"] == strat] if "strategy_name" in al.columns else al
    if sub.empty:
        sub = al
    latest = sub[sub["date_ts"] == sub["date_ts"].max()]
    latest = latest.sort_values("weight", ascending=False).head(15)
    weights = [{"symbol": r.symbol, "weight": float(r.weight)} for r in latest.itertuples()]
    g = sub.groupby("date_ts")
    turn = g["turnover_contribution"].sum() if "turnover_contribution" in sub.columns else g["weight"].sum() * 0
    pos = sub[sub["weight"] > 0].groupby("date_ts")["symbol"].nunique()
    ts = pd.DataFrame({"turnover": turn})
    ts["positions"] = pos
    ts = ts.reset_index().sort_values("date_ts")
    ts = _downsample(ts.reset_index(drop=True))
    series = [{"date": d.strftime("%Y-%m-%d"),
               "turnover": float(t) if pd.notna(t) else None,
               "positions": int(p) if pd.notna(p) else None}
              for d, t, p in zip(ts["date_ts"], ts["turnover"], ts["positions"])]
    _write("portfolio.json", {
        "strategy": strat,
        "as_of": sub["date_ts"].max().strftime("%Y-%m-%d"),
        "latest_weights": weights,
        "series": series,
        "n_strategies": int(al["strategy_name"].nunique()) if "strategy_name" in al.columns else 1,
        "allocation_mode": "diagnostic_not_live_trading",
    })


# -------------------------------------------------------- agent: backtest ---
def build_backtest():
    dd = _pq("backtests/drawdown_series.parquet")
    out = {"drawdown": [], "subperiods": []}
    if dd is not None:
        dd = dd.copy()
        dd["date_ts"] = _dt(dd["date_ts"])
        strat = "top_20_vol_scaled"
        sub = dd[dd["strategy_name"] == strat].sort_values("date_ts")
        if sub.empty:
            strat = dd["strategy_name"].iloc[0]
            sub = dd[dd["strategy_name"] == strat].sort_values("date_ts")
        sub = _downsample(sub.reset_index(drop=True))
        out["drawdown_strategy"] = strat
        out["drawdown"] = [{"date": d.strftime("%Y-%m-%d"), "dd": round(float(x), 4)}
                           for d, x in zip(sub["date_ts"], sub["drawdown"])]
    sp = _pq("backtests/subperiod_performance.parquet")
    if sp is not None:
        strat = "top_20_vol_scaled"
        sps = sp[sp["strategy_name"] == strat] if "strategy_name" in sp.columns else sp
        if sps.empty:
            sps = sp
        out["subperiods"] = [{
            "subperiod": str(r.subperiod), "total_return": float(r.total_return),
            "sharpe": float(r.sharpe) if pd.notna(r.sharpe) else None,
            "max_drawdown": float(r.max_drawdown),
        } for r in sps.itertuples()]
    _write("backtest.json", out)


# ------------------------------------------------------------ papertrade ---
def build_papertrade():
    """Bake the virtual paper-trading books (research validation, not live)."""
    manifest = _js("papertrade/papertrade_manifest.json")
    if manifest is None:
        print("  SKIP (missing): data/papertrade/papertrade_manifest.json")
        return
    books = []
    for name, info in (manifest.get("books") or {}).items():
        bdir = DATA / "papertrade" / name
        book = {
            "name": name,
            "status": info.get("status"),
            "skip_reason": info.get("skip_reason"),
            "nav": info.get("nav"),
            "n_fills": info.get("n_fills"),
            "cum_return": None,
            "equity": [],
            "recent_fills": [],
            "positions": {},
            "cash": None,
            "last_rebalance": None,
        }
        eq = _pq(f"papertrade/{name}/equity.parquet")
        if eq is not None and not eq.empty:
            eq = eq.copy().sort_values("date")
            book["cum_return"] = float(eq["cum_return"].iloc[-1]) if "cum_return" in eq.columns else None
            eq = _downsample(eq.reset_index(drop=True), target=400)
            book["equity"] = [{
                "date": str(r.date)[:10],
                "nav": round(float(r.nav), 2),
                "cash": round(float(r.cash), 2) if pd.notna(r.cash) else None,
                "positions_value": round(float(r.positions_value), 2) if pd.notna(r.positions_value) else None,
                "daily_return": float(r.daily_return) if pd.notna(r.daily_return) else None,
                "cum_return": float(r.cum_return) if pd.notna(r.cum_return) else None,
            } for r in eq.itertuples()]
        fl = _pq(f"papertrade/{name}/fills.parquet")
        if fl is not None and not fl.empty:
            fl = fl.copy().sort_values("date").tail(50)
            book["recent_fills"] = [{
                "date": str(r.date)[:10], "symbol": r.symbol, "side": r.side,
                "qty": float(r.qty), "price": float(r.price),
                "notional": float(r.notional), "cost_usd": float(r.cost_usd),
                "reason": str(r.reason),
            } for r in fl.itertuples()]
        state = _js(f"papertrade/{name}/state.json")
        if state:
            book["positions"] = state.get("positions") or {}
            book["cash"] = state.get("cash")
            book["last_rebalance"] = state.get("last_rebalance")
        books.append(book)
    _write("papertrade.json", {
        "as_of": manifest.get("as_of"),
        "last_run_utc": manifest.get("last_run_utc"),
        "note": "Virtual paper trading for research validation only. Not live holdings. Not financial advice.",
        "books": books,
    })


# --------------------------------------------------------------- fund ops ---
MONITORING_MODULES = [
    "data_quality", "signal_health", "execution_quality", "model_decay",
    "risk_report", "shadow_nav", "watchdog", "champion_challenger",
]


def build_fundops():
    """Bake the institutional fund-operations layers (all inputs optional).

    Everything here describes VIRTUAL paper books: accounting reconciliation,
    the risk-governance pipeline, regime/sleeve strategy artifacts and the
    monitoring suite. Missing artifacts bake as null/empty, never as errors.
    """
    out = {
        "note": ("Virtual paper trading operations. Simulated cash and simulated fees. "
                 "Not live holdings, not financial advice. alpha_verified remains false."),
        "reconciliation": None,
        "risk_audits": [],
        "drawdown_states": [],
        "regime": None,
        "sleeve_allocation": None,
        "monitoring": None,
        "nav": [],
    }

    # Dual-book reconciliation (engine NAV vs independent shadow NAV).
    rec = _js("accounting/reconciliation_report.json")
    if rec:
        meta = rec.get("_meta") or {}
        books = []
        for name, r in sorted(rec.items()):
            if name == "_meta" or not isinstance(r, dict):
                continue
            books.append({
                "book": name,
                "status": r.get("status"),
                "divergence_bps": r.get("divergence_bps"),
                "max_divergence_bps": r.get("max_divergence_bps"),
                "days_ok": r.get("days_ok"),
                "days_checked": r.get("days_checked"),
                "break_classification": r.get("break_classification"),
                "date": r.get("date"),
            })
        out["reconciliation"] = {
            "tolerance_bps": meta.get("tolerance_bps"),
            "generated_utc": meta.get("generated_utc"),
            "books": books,
        }

    # Risk pipeline audits + drawdown controller states (per book).
    risk_dir = DATA / "risk"
    if risk_dir.exists():
        for p in sorted(risk_dir.glob("risk_audit_*.json")):
            a = _js(f"risk/{p.name}") or {}
            mult = a.get("multipliers") or {}
            stages = a.get("stages") or {}
            dd = stages.get("drawdown") or {}
            vt = stages.get("vol_target") or {}
            out["risk_audits"].append({
                "book": a.get("book") or p.stem.replace("risk_audit_", ""),
                "as_of": a.get("as_of"),
                "final_gross_exposure": a.get("final_gross_exposure"),
                "final_cash_weight": a.get("final_cash_weight"),
                "n_final_positions": len(a.get("final_weights") or {}),
                "vol_target_multiplier": mult.get("vol_target"),
                "drawdown_multiplier": mult.get("drawdown"),
                "combined_multiplier": mult.get("combined_min"),
                "forecast_vol_annual": vt.get("forecast_vol_annual"),
                "drawdown_state": dd.get("state"),
            })
        for p in sorted(risk_dir.glob("drawdown_state_*.json")):
            d = _js(f"risk/{p.name}") or {}
            out["drawdown_states"].append({
                "book": p.stem.replace("drawdown_state_", ""),
                "state": d.get("state"),
                "state_entered_date": d.get("state_entered_date"),
                "last_date": d.get("last_date"),
                "last_nav": d.get("last_nav"),
                "peak_nav": d.get("peak_nav"),
                "drawdown": (float(d["last_nav"]) / float(d["peak_nav"]) - 1.0)
                            if d.get("last_nav") and d.get("peak_nav") else None,
            })

    # Regime (latest + days-in-regime from the daily history).
    latest = _js("strategies/regime_latest.json")
    if latest:
        out["regime"] = {
            "date": latest.get("date"),
            "regime": latest.get("regime"),
            "btc_trend_up": latest.get("btc_trend_up"),
            "breadth_pct_above_50d_ma": latest.get("breadth_pct_above_50d_ma"),
            "avg_pairwise_corr_60d_top20": latest.get("avg_pairwise_corr_60d_top20"),
            "btc_dominance_trend": latest.get("btc_dominance_trend"),
            "fear_greed_value": latest.get("fear_greed_value"),
            "fear_greed_bucket": latest.get("fear_greed_bucket"),
            "days_in_regime": None,
        }
        daily = _pq("strategies/regime_daily.parquet")
        if daily is not None and not daily.empty and "regime" in daily.columns:
            regimes = daily.sort_values("date_ts")["regime"].astype(str).tolist()
            days = 0
            for r in reversed(regimes):
                if r != regimes[-1]:
                    break
                days += 1
            out["regime"]["days_in_regime"] = int(days)
            out["regime"]["regime"] = regimes[-1]

    # Sleeve allocation proposal (approval-gated — never auto-executed).
    prop = _js("strategies/sleeve_allocation_proposal.json")
    if prop:
        out["sleeve_allocation"] = {
            "as_of": prop.get("as_of"),
            "regime": prop.get("regime"),
            "cash_weight": prop.get("cash_weight"),
            "artifact_type": prop.get("artifact_type"),
            "approved": bool(prop.get("approved", False)),
            "note": prop.get("note"),
            "sleeves": [
                {"sleeve": name,
                 "base_weight": info.get("base_weight"),
                 "proposed_weight": info.get("proposed_weight"),
                 "sortino_90d": info.get("sortino_90d"),
                 "return_source": info.get("return_source")}
                for name, info in sorted((prop.get("sleeves") or {}).items())
            ],
        }

    # Monitoring alert summary.
    modules, alerts = [], []
    for name in MONITORING_MODULES:
        rep = _js(f"reports/monitoring/{name}.json")
        if rep is None:
            continue
        status = str(rep.get("status", "unknown"))
        msgs = [str(a) for a in (rep.get("alerts") or [])]
        modules.append({"module": name, "status": status, "n_alerts": len(msgs),
                        "generated_utc": rep.get("generated_utc")})
        severity = "alert" if status == "alert" else ("warn" if status == "warn" else "info")
        for m in msgs:
            alerts.append({"module": name, "message": m, "severity": severity})
    verdict = _js("readiness/daily_quality_verdict.json") or {}
    if modules or verdict:
        out["monitoring"] = {
            "modules": modules,
            "alerts": alerts,
            "daily_quality_verdict": verdict.get("status"),
        }

    # Net-vs-gross NAV per book from the independent accounting ledger.
    acc_dir = DATA / "accounting"
    if acc_dir.exists():
        for p in sorted(acc_dir.glob("nav_*.parquet")):
            nav = _pq(f"accounting/{p.name}")
            if nav is None or nav.empty or "nav" not in nav.columns:
                continue
            nav = nav.copy().sort_values("date")
            nav = _downsample(nav.reset_index(drop=True), target=400)
            out["nav"].append({
                "book": p.stem.replace("nav_", ""),
                "latest": {
                    "date": str(nav["date"].iloc[-1])[:10],
                    "gross": float(nav["nav"].iloc[-1]),
                    "net": float(nav["net_nav"].iloc[-1]) if "net_nav" in nav.columns else None,
                    "mgmt_fee_cum": float(nav["mgmt_fee_cum"].iloc[-1]) if "mgmt_fee_cum" in nav.columns else None,
                    "perf_fee_cum": float(nav["perf_fee_cum"].iloc[-1]) if "perf_fee_cum" in nav.columns else None,
                },
                "points": [{
                    "date": str(r.date)[:10],
                    "gross": round(float(r.nav), 2),
                    "net": round(float(r.net_nav), 2) if "net_nav" in nav.columns else None,
                } for r in nav.itertuples()],
            })

    _write("fundops.json", out)


def build_meta():
    """Freshness metadata: when the bake ran and how fresh each source is."""
    from datetime import datetime, timezone

    def _mtime(rel):
        p = DATA / rel
        if not p.exists():
            return None
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()

    _write("meta.json", {
        "baked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_mtimes": {
            "backtests": _mtime("backtests/backtest_manifest.json"),
            "predictions": _mtime("predictions/alpha_model_leaderboard.parquet"),
            "allocations": _mtime("allocations/allocation_manifest.json"),
            "papertrade": _mtime("papertrade/papertrade_manifest.json"),
        },
    })


def main():
    print(f"Baking CHF artifacts -> {OUT.relative_to(ROOT)}")
    build_summary()
    build_equity()
    build_strategies()
    build_cost_sweep()
    build_signals()
    build_universe()
    build_market()
    build_onchain()
    build_features()
    build_labels()
    build_models()
    build_portfolio()
    build_backtest()
    build_papertrade()
    build_fundops()
    build_meta()
    print("Done.")


if __name__ == "__main__":
    main()
