"""Generate result charts for CHF from existing backtest/prediction outputs.

Reads the parquet files the pipeline already produced and renders PNG charts
into artifacts/plots/. No re-running of the pipeline is required.

Usage:
    python3 reports/plots.py                # plot every available backtest run
    python3 reports/plots.py --dir data/backtests_candidate_linear_ridge_30d

Charts produced per backtest run (when the data exists):
    1. equity_curve.png        strategies vs BTC/ETH/50-50/equal-weight
    2. drawdown.png            best strategy vs BTC, underwater plot
    3. benchmark_bars.png      total return of each strategy vs benchmark lines
    4. cost_sensitivity.png    return vs transaction cost (bps)
Plus one global chart from the model leaderboard:
    5. rank_ic.png             signal strength (Rank IC) with t-stats
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: write files, never open a window
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "artifacts" / "plots"

BENCHMARKS = ["BTC", "ETH", "BTC_ETH_50_50", "equal_weight_universe"]
BENCH_LABEL = {
    "BTC": "BTC",
    "ETH": "ETH",
    "BTC_ETH_50_50": "BTC/ETH 50-50",
    "equal_weight_universe": "Equal-weight universe",
    "cash": "Cash",
}
BENCH_COLOR = {
    "BTC": "#F7931A",
    "ETH": "#627EEA",
    "BTC_ETH_50_50": "#8E44AD",
    "equal_weight_universe": "#7F8C8D",
    "cash": "#BDC3C7",
}
ACCENT = "#1F3A5F"
GOOD = "#1B7A3D"
BAD = "#B02A2A"

plt.rcParams.update({
    "figure.dpi": 130,
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
})


def _pct(x, _pos=None):
    return f"{x*100:.0f}%"


def _read(path: Path):
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def _bench_total_returns(eq: pd.DataFrame) -> dict:
    """Total return per benchmark, computed from the equity curve."""
    out = {}
    for name in BENCHMARKS:
        s = eq[eq.strategy_name == name].sort_values("date_ts")
        if len(s) >= 2 and s.portfolio_value.iloc[0] > 0:
            out[name] = s.portfolio_value.iloc[-1] / s.portfolio_value.iloc[0] - 1.0
    return out


def plot_equity(eq: pd.DataFrame, label: str, out_dir: Path):
    if eq is None or eq.empty:
        return None
    eq = eq.copy()
    eq["date_ts"] = pd.to_datetime(eq["date_ts"], utc=True)
    strategies = sorted(eq[eq.benchmark_type == "strategy"].strategy_name.unique())
    if not strategies:
        return None

    # rank strategies by final value to highlight the best one
    finals = {s: eq[eq.strategy_name == s].sort_values("date_ts").portfolio_value.iloc[-1]
              for s in strategies}
    best = max(finals, key=finals.get)

    fig, ax = plt.subplots(figsize=(10, 6))

    # all strategies faint
    for s in strategies:
        d = eq[eq.strategy_name == s].sort_values("date_ts")
        ax.plot(d.date_ts, d.portfolio_value / 1000, color="#B0B7C0",
                linewidth=0.8, alpha=0.55, zorder=1)
    # best strategy bold
    d = eq[eq.strategy_name == best].sort_values("date_ts")
    ax.plot(d.date_ts, d.portfolio_value / 1000, color=GOOD, linewidth=2.4,
            label=f"Best strategy: {best}", zorder=3)
    # benchmarks
    for name in BENCHMARKS:
        d = eq[eq.strategy_name == name].sort_values("date_ts")
        if d.empty:
            continue
        ax.plot(d.date_ts, d.portfolio_value / 1000, color=BENCH_COLOR[name],
                linewidth=1.8, linestyle="--", label=BENCH_LABEL[name], zorder=2)

    ax.axhline(100, color="#333", linewidth=0.8, alpha=0.5)
    ax.set_title(f"Portfolio value: strategies vs benchmarks  ·  {label}")
    ax.set_ylabel("Portfolio value ($K, start = $100K)")
    ax.set_xlabel("")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    fig.text(0.99, 0.01, "Faint grey lines = other long-only strategies. Start $100K.",
             ha="right", va="bottom", fontsize=7, color="#777")
    fig.tight_layout()
    p = out_dir / "equity_curve.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_drawdown(eq: pd.DataFrame, label: str, out_dir: Path):
    if eq is None or eq.empty:
        return None
    eq = eq.copy()
    eq["date_ts"] = pd.to_datetime(eq["date_ts"], utc=True)
    strategies = eq[eq.benchmark_type == "strategy"].strategy_name.unique()
    if len(strategies) == 0:
        return None
    finals = {s: eq[eq.strategy_name == s].sort_values("date_ts").portfolio_value.iloc[-1]
              for s in strategies}
    best = max(finals, key=finals.get)

    fig, ax = plt.subplots(figsize=(10, 4.8))
    for name, color, lab in [(best, GOOD, f"Best strategy: {best}"),
                             ("BTC", BENCH_COLOR["BTC"], "BTC")]:
        d = eq[eq.strategy_name == name].sort_values("date_ts")
        if d.empty:
            continue
        val = d.portfolio_value.values
        dd = val / pd.Series(val).cummax().values - 1.0
        ax.fill_between(d.date_ts, dd, 0, color=color, alpha=0.25)
        ax.plot(d.date_ts, dd, color=color, linewidth=1.6, label=lab)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct))
    ax.set_title(f"Drawdown (how far underwater)  ·  {label}")
    ax.set_ylabel("Drawdown from peak")
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    p = out_dir / "drawdown.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_benchmark_bars(sc: pd.DataFrame, eq: pd.DataFrame, label: str, out_dir: Path):
    if sc is None or sc.empty:
        return None
    sc = sc.sort_values("total_return", ascending=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = [GOOD if v > 0 else BAD for v in sc.total_return]
    ax.barh(sc.strategy_name, sc.total_return, color=colors, alpha=0.85)

    # benchmark reference lines
    bench = _bench_total_returns(eq) if eq is not None else {}
    for name, val in bench.items():
        ax.axvline(val, color=BENCH_COLOR[name], linestyle="--", linewidth=1.6)
        ax.text(val, len(sc) - 0.4, f" {BENCH_LABEL[name]} {val*100:.0f}%",
                rotation=90, va="top", ha="left", fontsize=7,
                color=BENCH_COLOR[name], fontweight="bold")

    ax.axvline(0, color="#333", linewidth=0.8)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_pct))
    ax.set_title(f"Total return: each strategy vs benchmarks  ·  {label}")
    ax.set_xlabel("Total return over the test window")
    fig.text(0.99, 0.01, "Dashed lines = passive benchmarks. Bars = ML long-only strategies.",
             ha="right", va="bottom", fontsize=7, color="#777")
    fig.tight_layout()
    p = out_dir / "benchmark_bars.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_cost_sensitivity(cs: pd.DataFrame, label: str, out_dir: Path):
    if cs is None or cs.empty:
        return None
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for name, d in cs.groupby("strategy_name"):
        d = d.sort_values("cost_bps")
        ax.plot(d.cost_bps, d.total_return, marker="o", markersize=3,
                linewidth=1.3, label=name)
    ax.axhline(0, color="#333", linewidth=0.8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct))
    ax.set_title(f"Return vs transaction cost  ·  {label}")
    ax.set_xlabel("Transaction cost (basis points per trade)")
    ax.set_ylabel("Total return")
    ax.legend(loc="best", fontsize=7, ncol=2)
    fig.text(0.99, 0.01, "How fast the edge erodes as trading gets more expensive. 20 bps is the base case.",
             ha="right", va="bottom", fontsize=7, color="#777")
    fig.tight_layout()
    p = out_dir / "cost_sensitivity.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def plot_rank_ic(out_dir: Path, top_n: int = 15):
    # Prefer the full experiment leaderboard (real ML models); fall back to the
    # baseline-only leaderboard if the alpha grid is unavailable.
    lb = _read(ROOT / "data" / "predictions" / "alpha_model_leaderboard.parquet")
    ic_col = "mean_rank_ic"
    if lb is None or lb.empty or ic_col not in lb.columns:
        lb = _read(ROOT / "data" / "predictions" / "model_leaderboard.parquet")
        ic_col = "rank_ic_mean"
    if lb is None or lb.empty or ic_col not in lb.columns:
        return None

    lb = lb.copy()
    # Drop the diagnostic baseline so the chart shows learnable signal, not
    # symbol autocorrelation (the baseline is not real cross-sectional alpha).
    real = lb[lb["model_name"].astype(str) != "baseline_cross_sectional_mean"]
    if real.empty:
        real = lb
    real = real.sort_values(ic_col, ascending=False).head(top_n).iloc[::-1]

    real["combo"] = (real["model_name"].astype(str) + "  ·  " +
                     real["feature_set"].astype(str).str.replace("market_plus_onchain", "mkt+onchain") + "  ·  " +
                     real["horizon_days"].astype(str) + "d")
    passed = real.get("signal_gate_passed", pd.Series([False] * len(real)))

    fig, ax = plt.subplots(figsize=(11, 7))
    colors = [GOOD if p else (ACCENT if v > 0 else BAD)
              for v, p in zip(real[ic_col], passed)]
    bars = ax.barh(real["combo"], real[ic_col], color=colors, alpha=0.88)
    tcol = "rank_ic_tstat" if "rank_ic_tstat" in real.columns else None
    for bar, t in zip(bars, (real[tcol] if tcol else [None] * len(real))):
        if t is not None and pd.notna(t):
            ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                    f"  t={t:.1f}", va="center", fontsize=7.5, color="#333")
    ax.axvline(0, color="#333", linewidth=0.8)
    ax.axvline(0.01, color="#999", linestyle=":", linewidth=1.2)
    ax.set_title(f"Signal strength: top {len(real)} experiments by Rank IC")
    ax.set_xlabel("Mean Rank IC  (dotted line = 0.01 signal-gate threshold)")
    fig.text(0.99, 0.012,
             "Green = passed the signal gate. Rank IC ~0.03 = weak but real; t-stat > 2 means not luck. "
             "Baseline excluded (it only captures symbol autocorrelation).",
             ha="right", va="bottom", fontsize=7, color="#777")
    fig.tight_layout()
    p = out_dir / "rank_ic.png"
    fig.savefig(p)
    plt.close(fig)
    return p


def process_run(bt_dir: Path):
    label = bt_dir.name.replace("backtests_candidate_", "").replace("backtests", "default")
    out_dir = OUT_ROOT / label
    out_dir.mkdir(parents=True, exist_ok=True)
    eq = _read(bt_dir / "equity_curves.parquet")
    sc = _read(bt_dir / "strategy_comparison.parquet")
    cs = _read(bt_dir / "cost_sweep.parquet")
    made = []
    for fn in (plot_equity(eq, label, out_dir),
               plot_drawdown(eq, label, out_dir),
               plot_benchmark_bars(sc, eq, label, out_dir),
               plot_cost_sensitivity(cs, label, out_dir)):
        if fn:
            made.append(fn)
    return label, made


def discover_runs():
    runs = []
    default = ROOT / "data" / "backtests"
    if (default / "equity_curves.parquet").exists():
        runs.append(default)
    for d in sorted(ROOT.glob("data/backtests_candidate_*")):
        if (d / "equity_curves.parquet").exists():
            runs.append(d)
    return runs


def main():
    ap = argparse.ArgumentParser(description="Render CHF result charts from existing outputs.")
    ap.add_argument("--dir", help="A specific data/backtests* directory. Default: all discovered runs.")
    args = ap.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    runs = [ROOT / args.dir] if args.dir else discover_runs()
    if not runs:
        print("No backtest runs found under data/. Run the pipeline first (python main.py full or demo).")
        return

    print(f"Rendering charts for {len(runs)} backtest run(s)...\n")
    total = 0
    for bt_dir in runs:
        label, made = process_run(bt_dir)
        total += len(made)
        print(f"  [{label}]  {len(made)} chart(s) -> artifacts/plots/{label}/")
        for f in made:
            print(f"        {f.relative_to(ROOT)}")

    ric = plot_rank_ic(OUT_ROOT)
    if ric:
        total += 1
        print(f"\n  [global]  rank_ic.png -> {ric.relative_to(ROOT)}")

    print(f"\nDone. {total} chart(s) written under artifacts/plots/.")


if __name__ == "__main__":
    main()
