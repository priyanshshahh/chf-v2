"""
CHF Streamlit Dashboard
Main entry point for the CHF Mini Hedge Fund analytics dashboard.
Run: streamlit run dashboard.py

Views:
1. Universe Explorer
2. Signal Monitor
3. Portfolio Weights
4. Backtest Analytics
5. Model Diagnostics
6. Pipeline Control
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add project root to path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Page Config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CHF — Crypto Hedge Fund",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Config & Paths
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def load_config():
    try:
        from configs.config import get_config
        return get_config()
    except Exception:
        return {"_project_root": str(_ROOT)}


cfg = load_config()
DATA_ROOT = Path(cfg["_project_root"]) / "data"


# ─────────────────────────────────────────────────────────────────────────────
# Data Loaders (cached)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_parquet_safe(path: str) -> pd.DataFrame:
    """Load a Parquet file safely, returning empty DataFrame on failure."""
    try:
        p = Path(path)
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_parquet(p)
        # Convert date columns
        for col in df.columns:
            if "date" in col.lower() or "ts" in col.lower():
                try:
                    df[col] = pd.to_datetime(df[col], utc=True)
                except Exception:
                    pass
        return df
    except Exception as e:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_json_safe(path: str) -> Dict:
    """Load a JSON file safely."""
    try:
        p = Path(path)
        if not p.exists():
            return {}
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def load_universe() -> pd.DataFrame:
    """Load latest universe snapshot."""
    universe_dir = DATA_ROOT / "raw" / "universe"
    files = sorted(universe_dir.glob("universe_*.parquet"), reverse=True)
    if not files:
        return pd.DataFrame()
    return load_parquet_safe(str(files[0]))


def load_market_data(symbol: str = None) -> pd.DataFrame:
    """Load market OHLCV data."""
    if symbol:
        paths = [
            DATA_ROOT / "cleaned" / f"{symbol}_ohlcv_clean.parquet",
            DATA_ROOT / "raw" / "market" / f"{symbol}_ohlcv.parquet",
        ]
        for p in paths:
            if p.exists():
                return load_parquet_safe(str(p))
        return pd.DataFrame()
    else:
        # Load all symbols
        cleaned_dir = DATA_ROOT / "cleaned"
        raw_dir = DATA_ROOT / "raw" / "market"
        files = list(cleaned_dir.glob("*_ohlcv_clean.parquet"))
        if not files:
            files = list(raw_dir.glob("*_ohlcv.parquet"))
        if not files:
            return pd.DataFrame()
        dfs = [load_parquet_safe(str(f)) for f in files]
        return pd.concat([d for d in dfs if not d.empty], ignore_index=True)


def load_features() -> pd.DataFrame:
    """Load feature store."""
    for fname in ["full_features.parquet", "market_features.parquet"]:
        path = DATA_ROOT / "features" / fname
        if path.exists():
            return load_parquet_safe(str(path))
    return pd.DataFrame()


def load_predictions(model: str = "lightgbm", horizon: int = 7) -> pd.DataFrame:
    """Load model predictions."""
    path = DATA_ROOT / "predictions" / f"predictions_{model}_h{horizon}d.parquet"
    if not path.exists():
        # Try any available
        files = list((DATA_ROOT / "predictions").glob("predictions_*.parquet"))
        if files:
            return load_parquet_safe(str(files[0]))
    return load_parquet_safe(str(path))


def load_allocations(strategy: str = "top_k_equal_weight") -> pd.DataFrame:
    """Load portfolio allocations."""
    path = DATA_ROOT / "allocations" / f"allocations_{strategy}.parquet"
    return load_parquet_safe(str(path))


def load_backtest_summary() -> pd.DataFrame:
    """Load backtest summary."""
    path = DATA_ROOT / "backtests" / "backtest_summary.parquet"
    return load_parquet_safe(str(path))


def load_equity_curves() -> pd.DataFrame:
    """Load equity curves."""
    path = DATA_ROOT / "backtests" / "equity_curves.parquet"
    return load_parquet_safe(str(path))


def load_labels(horizon: int = 7) -> pd.DataFrame:
    """Load labels for a horizon."""
    path = DATA_ROOT / "labels" / f"labels_{horizon}d.parquet"
    return load_parquet_safe(str(path))


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar Navigation
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/combo-chart.png", width=60)
    st.title("CHF Dashboard")
    st.caption("Crypto Hedge Fund Analytics")
    st.divider()
    page = st.radio(
        "Navigation",
        [
            "🌐 Universe Explorer",
            "📡 Signal Monitor",
            "⚖️ Portfolio Weights",
            "📊 Backtest Analytics",
            "🤖 Model Diagnostics",
            "⚙️ Pipeline Control",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    # Demo mode toggle — uses synthetic data when no live data exists
    _demo_mode = st.toggle(
        "🎭 Demo Mode",
        value=False,
        help="Show synthetic demo data. Run 'python main.py demo' to generate it.",
    )
    st.session_state["demo_mode"] = _demo_mode
    if _demo_mode:
        st.info("Demo mode active. Showing synthetic data.", icon="🎭")
    st.divider()
    st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: No Data Banner
# ─────────────────────────────────────────────────────────────────────────────

def no_data_banner(message: str = "No data available yet."):
    """Show a helpful empty-state banner with actionable commands."""
    st.info(f"ℹ️ {message}", icon="ℹ️")
    st.markdown("**Quick start options:**")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Option A — Demo data (no API keys):**")
        st.code("python main.py demo", language="bash")
    with col2:
        st.markdown("**Option B — Live pipeline (API keys needed):**")
        st.code("python main.py full", language="bash")
    st.markdown("Then launch the dashboard: `streamlit run app/dashboard.py`")


# ─────────────────────────────────────────────────────────────────────────────
# View 1: Universe Explorer
# ─────────────────────────────────────────────────────────────────────────────

if page == "🌐 Universe Explorer":
    st.title("🌐 Universe Explorer")
    st.caption("Current crypto universe with market cap, volume, and category filters.")

    universe_df = load_universe()

    if universe_df.empty:
        no_data_banner("Run the Universe Agent to populate the universe.")
    else:
        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Symbols", len(universe_df))
        if "market_cap_usd" in universe_df.columns:
            total_mcap = universe_df["market_cap_usd"].sum()
            col2.metric("Total Market Cap", f"${total_mcap/1e9:.1f}B")
        if "category" in universe_df.columns:
            col3.metric("Categories", universe_df["category"].nunique())
        if "rank" in universe_df.columns:
            col4.metric("Rank Range", f"1–{universe_df['rank'].max():.0f}")

        st.divider()

        # Filters
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            if "category" in universe_df.columns:
                cats = ["All"] + sorted(universe_df["category"].dropna().unique().tolist())
                sel_cat = st.selectbox("Category", cats)
            else:
                sel_cat = "All"
        with col_f2:
            if "market_cap_usd" in universe_df.columns:
                min_mcap = st.number_input(
                    "Min Market Cap ($M)", min_value=0, value=100, step=50
                )
            else:
                min_mcap = 0
        with col_f3:
            search = st.text_input("Search Symbol", "")

        # Apply filters
        filtered = universe_df.copy()
        if sel_cat != "All" and "category" in filtered.columns:
            filtered = filtered[filtered["category"] == sel_cat]
        if min_mcap > 0 and "market_cap_usd" in filtered.columns:
            filtered = filtered[filtered["market_cap_usd"] >= min_mcap * 1e6]
        if search and "symbol" in filtered.columns:
            filtered = filtered[
                filtered["symbol"].str.upper().str.contains(search.upper())
            ]

        # Market cap bar chart
        if "market_cap_usd" in filtered.columns and not filtered.empty:
            top_n = filtered.nlargest(30, "market_cap_usd")
            fig = px.bar(
                top_n,
                x="symbol",
                y="market_cap_usd",
                color="category" if "category" in top_n.columns else None,
                title="Top 30 by Market Cap",
                labels={"market_cap_usd": "Market Cap (USD)", "symbol": "Symbol"},
                template="plotly_dark",
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

        # Scatter: market cap vs volume
        if all(c in filtered.columns for c in ["market_cap_usd", "volume_24h_usd"]):
            fig2 = px.scatter(
                filtered,
                x="market_cap_usd",
                y="volume_24h_usd",
                text="symbol",
                color="category" if "category" in filtered.columns else None,
                title="Market Cap vs 24h Volume",
                log_x=True,
                log_y=True,
                template="plotly_dark",
            )
            fig2.update_traces(textposition="top center", textfont_size=8)
            fig2.update_layout(height=450)
            st.plotly_chart(fig2, use_container_width=True)

        # Data table
        st.subheader(f"Universe Table ({len(filtered)} symbols)")
        display_cols = [
            c for c in ["rank", "symbol", "name", "category",
                         "market_cap_usd", "volume_24h_usd", "price_usd"]
            if c in filtered.columns
        ]
        st.dataframe(
            filtered[display_cols].sort_values("rank" if "rank" in display_cols else display_cols[0]),
            use_container_width=True,
            height=400,
        )


# ─────────────────────────────────────────────────────────────────────────────
# View 2: Signal Monitor
# ─────────────────────────────────────────────────────────────────────────────

elif page == "📡 Signal Monitor":
    st.title("📡 Signal Monitor")
    st.caption("Latest model signals and feature heatmaps.")

    # Controls
    col1, col2 = st.columns(2)
    with col1:
        model_choice = st.selectbox("Model", ["lightgbm", "random_forest", "xgboost"])
    with col2:
        horizon_choice = st.selectbox("Horizon", [7, 14, 30])

    preds_df = load_predictions(model_choice, horizon_choice)

    if preds_df.empty:
        no_data_banner(f"No predictions found for {model_choice} h={horizon_choice}d.")
    else:
        # Latest date signals
        if "date_ts" in preds_df.columns:
            latest_date = preds_df["date_ts"].max()
            latest_preds = preds_df[preds_df["date_ts"] == latest_date].copy()
        else:
            latest_preds = preds_df.copy()

        latest_preds = latest_preds.sort_values("predicted_return", ascending=False)

        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Signals", len(latest_preds))
        col2.metric(
            "Top Signal",
            f"{latest_preds['predicted_return'].max():.4f}" if not latest_preds.empty else "N/A"
        )
        col3.metric(
            "Positive Signals",
            f"{(latest_preds['predicted_return'] > 0).sum()}"
        )
        if "actual_return" in latest_preds.columns:
            from scipy import stats as scipy_stats
            ic, _ = scipy_stats.spearmanr(
                latest_preds["predicted_return"],
                latest_preds["actual_return"],
                nan_policy="omit"
            )
            col4.metric("Latest Rank IC", f"{ic:.4f}")

        st.divider()

        # Bar chart: top signals
        top_signals = latest_preds.head(20)
        fig = px.bar(
            top_signals,
            x="symbol",
            y="predicted_return",
            color="predicted_return",
            color_continuous_scale="RdYlGn",
            title=f"Top 20 Signals — {model_choice} h={horizon_choice}d",
            template="plotly_dark",
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

        # IC over time
        if "date_ts" in preds_df.columns and "actual_return" in preds_df.columns:
            ic_series = (
                preds_df.groupby("date_ts")
                .apply(
                    lambda g: g["predicted_return"].corr(
                        g["actual_return"], method="spearman"
                    )
                )
                .reset_index()
                .rename(columns={0: "rank_ic"})
            )
            ic_series = ic_series.dropna()
            if not ic_series.empty:
                fig_ic = px.line(
                    ic_series,
                    x="date_ts",
                    y="rank_ic",
                    title="Rank IC Over Time",
                    template="plotly_dark",
                )
                fig_ic.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_ic.add_hline(y=0.05, line_dash="dot", line_color="green",
                                  annotation_text="IC=0.05")
                fig_ic.update_layout(height=350)
                st.plotly_chart(fig_ic, use_container_width=True)

        # Feature heatmap
        features_df = load_features()
        if not features_df.empty:
            st.subheader("Feature Heatmap (Latest Cross-Section)")
            if "date_ts" in features_df.columns:
                latest_feat_date = features_df["date_ts"].max()
                latest_feat = features_df[features_df["date_ts"] == latest_feat_date].copy()
            else:
                latest_feat = features_df.copy()

            feat_cols = [
                c for c in latest_feat.columns
                if c not in ("symbol", "date_ts", "feature_version",
                             "snapshot_id", "run_id", "source")
                and latest_feat[c].dtype in (np.float64, np.float32, np.int64, np.int32)
            ][:20]

            if feat_cols and "symbol" in latest_feat.columns:
                heat_data = latest_feat.set_index("symbol")[feat_cols].head(30)
                fig_heat = px.imshow(
                    heat_data.T,
                    color_continuous_scale="RdBu_r",
                    title="Feature Heatmap (Top 30 Symbols × Top 20 Features)",
                    template="plotly_dark",
                    aspect="auto",
                )
                fig_heat.update_layout(height=500)
                st.plotly_chart(fig_heat, use_container_width=True)

        # Signals table
        st.subheader("Signal Table")
        display_cols = [
            c for c in ["symbol", "predicted_return", "actual_return",
                         "date_ts", "fold_id"]
            if c in latest_preds.columns
        ]
        st.dataframe(
            latest_preds[display_cols].head(50),
            use_container_width=True,
            height=350,
        )


# ─────────────────────────────────────────────────────────────────────────────
# View 3: Portfolio Weights
# ─────────────────────────────────────────────────────────────────────────────

elif page == "⚖️ Portfolio Weights":
    st.title("⚖️ Portfolio Weights")
    st.caption("Current portfolio allocations and rebalancing history.")

    strategy_choice = st.selectbox(
        "Strategy",
        ["top_k_equal_weight", "score_proportional"],
    )

    alloc_df = load_allocations(strategy_choice)

    if alloc_df.empty:
        no_data_banner("No allocation data. Run the Portfolio Agent first.")
    else:
        # Latest allocation
        if "date_ts" in alloc_df.columns:
            latest_date = alloc_df["date_ts"].max()
            latest_alloc = alloc_df[alloc_df["date_ts"] == latest_date].copy()
        else:
            latest_alloc = alloc_df.copy()

        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Positions", len(latest_alloc))
        if "weight" in latest_alloc.columns:
            col2.metric("Max Weight", f"{latest_alloc['weight'].max():.1%}")
            col3.metric("Min Weight", f"{latest_alloc['weight'].min():.1%}")
            col4.metric("Total Weight", f"{latest_alloc['weight'].sum():.1%}")

        st.divider()

        # Pie chart
        if "weight" in latest_alloc.columns and "symbol" in latest_alloc.columns:
            fig_pie = px.pie(
                latest_alloc,
                values="weight",
                names="symbol",
                title=f"Portfolio Weights — {latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else latest_date}",
                template="plotly_dark",
            )
            fig_pie.update_layout(height=450)
            st.plotly_chart(fig_pie, use_container_width=True)

        # Weight bar chart
        fig_bar = px.bar(
            latest_alloc.sort_values("weight", ascending=True),
            x="weight",
            y="symbol",
            orientation="h",
            color="signal_score" if "signal_score" in latest_alloc.columns else None,
            color_continuous_scale="Viridis",
            title="Portfolio Weights (Horizontal)",
            template="plotly_dark",
        )
        fig_bar.update_layout(height=400)
        st.plotly_chart(fig_bar, use_container_width=True)

        # Weight history heatmap
        if "date_ts" in alloc_df.columns:
            st.subheader("Weight History Heatmap")
            pivot = alloc_df.pivot_table(
                index="date_ts", columns="symbol", values="weight", fill_value=0
            )
            if not pivot.empty:
                fig_hist = px.imshow(
                    pivot.T,
                    color_continuous_scale="Blues",
                    title="Portfolio Weight History",
                    template="plotly_dark",
                    aspect="auto",
                )
                fig_hist.update_layout(height=500)
                st.plotly_chart(fig_hist, use_container_width=True)

        # Transaction log
        tx_path = DATA_ROOT / "allocations" / "allocations_transaction_log.parquet"
        tx_df = load_parquet_safe(str(tx_path))
        if not tx_df.empty:
            st.subheader("Transaction Log")
            st.dataframe(tx_df.head(100), use_container_width=True, height=300)


# ─────────────────────────────────────────────────────────────────────────────
# View 4: Backtest Analytics
# ─────────────────────────────────────────────────────────────────────────────

elif page == "📊 Backtest Analytics":
    st.title("📊 Backtest Analytics")
    st.caption("Equity curves, performance metrics, cost sweeps, and K sweeps.")

    equity_df = load_equity_curves()
    summary_df = load_backtest_summary()

    if equity_df.empty and summary_df.empty:
        no_data_banner("No backtest data. Run the Backtest Agent first.")
    else:
        # Performance summary table
        if not summary_df.empty:
            st.subheader("Performance Summary")
            perf_cols = [
                c for c in [
                    "backtest_name", "strategy", "cagr", "sharpe", "sortino",
                    "calmar", "max_drawdown", "annualized_vol", "total_return",
                    "n_days", "cost_bps"
                ]
                if c in summary_df.columns
            ]
            if perf_cols:
                disp = summary_df[perf_cols].copy()
                for col in ["cagr", "sharpe", "sortino", "calmar", "max_drawdown",
                             "annualized_vol", "total_return"]:
                    if col in disp.columns:
                        disp[col] = disp[col].apply(
                            lambda x: f"{x:.4f}" if pd.notna(x) else "N/A"
                        )
                st.dataframe(disp, use_container_width=True)

            # KPIs for main strategy
            main_row = summary_df[
                summary_df.get("backtest_name", pd.Series()).str.contains("main", na=False)
            ] if "backtest_name" in summary_df.columns else pd.DataFrame()
            if main_row.empty and not summary_df.empty:
                main_row = summary_df.head(1)

            if not main_row.empty:
                row = main_row.iloc[0]
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("CAGR", f"{row.get('cagr', 0):.2%}")
                col2.metric("Sharpe", f"{row.get('sharpe', 0):.3f}")
                col3.metric("Max Drawdown", f"{row.get('max_drawdown', 0):.2%}")
                col4.metric("Sortino", f"{row.get('sortino', 0):.3f}")

        st.divider()

        # Equity curves
        if not equity_df.empty and "date_ts" in equity_df.columns:
            st.subheader("Equity Curves")
            fig_eq = go.Figure()

            if "backtest_name" in equity_df.columns:
                for name, grp in equity_df.groupby("backtest_name"):
                    grp = grp.sort_values("date_ts")
                    fig_eq.add_trace(go.Scatter(
                        x=grp["date_ts"],
                        y=grp["portfolio_value"],
                        name=name,
                        mode="lines",
                    ))
            else:
                fig_eq.add_trace(go.Scatter(
                    x=equity_df["date_ts"],
                    y=equity_df["portfolio_value"],
                    name="Strategy",
                    mode="lines",
                ))

            fig_eq.update_layout(
                title="Portfolio Value Over Time",
                template="plotly_dark",
                height=450,
                xaxis_title="Date",
                yaxis_title="Portfolio Value ($)",
            )
            st.plotly_chart(fig_eq, use_container_width=True)

            # Drawdown chart
            if "backtest_name" in equity_df.columns:
                main_eq = equity_df[
                    equity_df["backtest_name"].str.contains("main", na=False)
                ]
                if main_eq.empty:
                    main_eq = equity_df.groupby("backtest_name").first().reset_index()
                    main_eq = equity_df[
                        equity_df["backtest_name"] == equity_df["backtest_name"].iloc[0]
                    ]
            else:
                main_eq = equity_df

            if not main_eq.empty:
                vals = main_eq["portfolio_value"].values
                running_max = np.maximum.accumulate(vals)
                drawdown = (vals - running_max) / (running_max + 1e-10)
                dd_df = main_eq[["date_ts"]].copy()
                dd_df["drawdown"] = drawdown

                fig_dd = px.area(
                    dd_df,
                    x="date_ts",
                    y="drawdown",
                    title="Drawdown",
                    template="plotly_dark",
                    color_discrete_sequence=["red"],
                )
                fig_dd.update_layout(height=300)
                st.plotly_chart(fig_dd, use_container_width=True)

        # Cost sweep
        if not summary_df.empty and "cost_bps" in summary_df.columns:
            cost_sweep = summary_df[summary_df["cost_bps"].notna()].copy()
            if len(cost_sweep) > 1:
                st.subheader("Cost Sweep")
                fig_cs = px.line(
                    cost_sweep.sort_values("cost_bps"),
                    x="cost_bps",
                    y=["sharpe", "cagr"] if "sharpe" in cost_sweep.columns else ["cagr"],
                    title="Sharpe & CAGR vs Transaction Cost",
                    template="plotly_dark",
                )
                fig_cs.update_layout(height=350)
                st.plotly_chart(fig_cs, use_container_width=True)

        # K sweep
        if not summary_df.empty and "top_k" in summary_df.columns:
            k_sweep = summary_df[summary_df["top_k"].notna()].copy()
            if not k_sweep.empty:
                st.subheader("K Sweep (Portfolio Size)")
                fig_ks = px.bar(
                    k_sweep.sort_values("top_k"),
                    x="top_k",
                    y="sharpe" if "sharpe" in k_sweep.columns else "cagr",
                    title="Sharpe vs Portfolio Size (K)",
                    template="plotly_dark",
                )
                fig_ks.update_layout(height=350)
                st.plotly_chart(fig_ks, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# View 5: Model Diagnostics
# ─────────────────────────────────────────────────────────────────────────────

elif page == "🤖 Model Diagnostics":
    st.title("🤖 Model Diagnostics")
    st.caption("Feature importance, walk-forward IC, and model metrics.")

    col1, col2 = st.columns(2)
    with col1:
        model_choice = st.selectbox("Model", ["lightgbm", "random_forest"])
    with col2:
        horizon_choice = st.selectbox("Horizon", [7, 14, 30])

    # Feature importance
    fi_path = (
        Path(cfg["_project_root"])
        / "artifacts"
        / "feature_importance"
        / f"fi_{model_choice}_h{horizon_choice}.csv"
    )

    if fi_path.exists():
        fi_df = pd.read_csv(fi_path)
        st.subheader("Feature Importance")
        top_fi = fi_df.head(20)
        fig_fi = px.bar(
            top_fi.sort_values("importance"),
            x="importance",
            y="feature",
            orientation="h",
            title=f"Top 20 Features — {model_choice} h={horizon_choice}d",
            template="plotly_dark",
            color="importance",
            color_continuous_scale="Viridis",
        )
        fig_fi.update_layout(height=500)
        st.plotly_chart(fig_fi, use_container_width=True)
    else:
        st.info("Feature importance not available. Run ModelAgent first.")

    # Walk-forward fold metrics
    fold_path = (
        Path(cfg["_project_root"])
        / "artifacts"
        / "fold_metrics"
        / f"folds_{model_choice}_h{horizon_choice}.json"
    )

    if fold_path.exists():
        with open(fold_path) as f:
            fold_metrics = json.load(f)

        if fold_metrics:
            fold_df = pd.DataFrame(fold_metrics)
            st.subheader("Walk-Forward Fold Metrics")

            # IC over folds
            if "rank_ic" in fold_df.columns:
                fig_ic = px.bar(
                    fold_df,
                    x="fold_id",
                    y="rank_ic",
                    color="rank_ic",
                    color_continuous_scale="RdYlGn",
                    title="Rank IC per Walk-Forward Fold",
                    template="plotly_dark",
                )
                fig_ic.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_ic.add_hline(y=0.05, line_dash="dot", line_color="green",
                                  annotation_text="IC=0.05")
                fig_ic.update_layout(height=350)
                st.plotly_chart(fig_ic, use_container_width=True)

            # Hit rate over folds
            if "hit_rate" in fold_df.columns:
                fig_hr = px.line(
                    fold_df,
                    x="fold_id",
                    y="hit_rate",
                    title="Hit Rate per Walk-Forward Fold",
                    template="plotly_dark",
                )
                fig_hr.add_hline(y=0.5, line_dash="dash", line_color="gray",
                                  annotation_text="50%")
                fig_hr.update_layout(height=300)
                st.plotly_chart(fig_hr, use_container_width=True)

            # Summary stats
            col1, col2, col3 = st.columns(3)
            if "rank_ic" in fold_df.columns:
                col1.metric("Mean IC", f"{fold_df['rank_ic'].mean():.4f}")
                col2.metric("IC Std", f"{fold_df['rank_ic'].std():.4f}")
                ic_t = fold_df['rank_ic'].mean() / (fold_df['rank_ic'].std() / np.sqrt(len(fold_df)) + 1e-10)
                col3.metric("IC t-stat", f"{ic_t:.2f}")

            # Fold table
            st.subheader("Fold Details")
            disp_cols = [
                c for c in ["fold_id", "rank_ic", "hit_rate", "r2",
                             "n_samples", "val_start", "val_end"]
                if c in fold_df.columns
            ]
            st.dataframe(fold_df[disp_cols], use_container_width=True)
    else:
        st.info("Fold metrics not available. Run ModelAgent first.")

    # Feature dictionary
    feat_dict_path = DATA_ROOT / "features" / "feature_dictionary.json"
    if feat_dict_path.exists():
        st.subheader("Feature Dictionary")
        feat_dict = load_json_safe(str(feat_dict_path))
        if feat_dict:
            rows = []
            for name, meta in feat_dict.items():
                rows.append({
                    "Feature": name,
                    "Family": meta.get("family", ""),
                    "Formula": meta.get("formula", ""),
                    "Description": meta.get("description", ""),
                    "Is Proxy": "✓" if meta.get("is_proxy") else "",
                    "Data Sources": ", ".join(meta.get("data_sources", [])),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)


# ─────────────────────────────────────────────────────────────────────────────
# View 6: Pipeline Control
# ─────────────────────────────────────────────────────────────────────────────

elif page == "⚙️ Pipeline Control":
    st.title("⚙️ Pipeline Control")
    st.caption("Trigger pipeline stages, view run history, and monitor status.")

    # Run history from DB
    import sqlite3
    registry_path = Path(cfg["_project_root"]) / "metadata" / "agent_registry.db"

    if registry_path.exists():
        try:
            with sqlite3.connect(registry_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT agent_name, run_id, snapshot_id, status,
                           started_at, completed_at
                    FROM agent_runs
                    ORDER BY started_at DESC
                    LIMIT 100
                    """
                ).fetchall()
            run_df = pd.DataFrame([dict(r) for r in rows])
        except Exception:
            run_df = pd.DataFrame()
    else:
        run_df = pd.DataFrame()

    # Status overview
    st.subheader("Pipeline Status")
    if not run_df.empty:
        # Latest status per agent
        latest_runs = run_df.groupby("agent_name").first().reset_index()
        cols = st.columns(min(len(latest_runs), 4))
        for i, (_, row) in enumerate(latest_runs.iterrows()):
            col = cols[i % 4]
            status_icon = "✅" if row.get("status") == "success" else "❌"
            col.metric(
                row["agent_name"],
                f"{status_icon} {row.get('status', 'unknown')}",
                delta=f"{row.get('duration_s', 0):.0f}s",
            )
    else:
        st.info("No run history yet. Run the pipeline to see status here.")

    st.divider()

    # Manual pipeline triggers
    st.subheader("Manual Pipeline Triggers")
    st.warning(
        "⚠️ These buttons trigger pipeline stages in the background. "
        "Check terminal output for progress."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🌐 Update Universe", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "universe"],
                cwd=str(_ROOT),
            )
            st.success("Universe update started!")

        if st.button("📈 Fetch Market Data", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "market_data"],
                cwd=str(_ROOT),
            )
            st.success("Market data fetch started!")

    with col2:
        if st.button("🔧 Build Features", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "features"],
                cwd=str(_ROOT),
            )
            st.success("Feature engineering started!")

        if st.button("🤖 Train Models", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "models"],
                cwd=str(_ROOT),
            )
            st.success("Model training started!")

    with col3:
        if st.button("⚖️ Generate Portfolio", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "portfolio"],
                cwd=str(_ROOT),
            )
            st.success("Portfolio generation started!")

        if st.button("📊 Run Backtest", use_container_width=True):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--stage", "backtest"],
                cwd=str(_ROOT),
            )
            st.success("Backtest started!")

    st.divider()

    # Full pipeline
    col_full, _ = st.columns([1, 2])
    with col_full:
        if st.button("🚀 Run Full Pipeline", use_container_width=True, type="primary"):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "pipelines" / "pipeline_runner.py"),
                 "--full"],
                cwd=str(_ROOT),
            )
            st.success("Full pipeline started! Check terminal for progress.")

    # Run history table
    if not run_df.empty:
        st.subheader("Run History")
        st.dataframe(run_df, use_container_width=True, height=400)

    # Config viewer
    st.subheader("Active Configuration")
    with st.expander("View run_config.yaml"):
        config_path = _ROOT / "configs" / "run_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                st.code(f.read(), language="yaml")
        else:
            st.info("Configuration file not found.")
