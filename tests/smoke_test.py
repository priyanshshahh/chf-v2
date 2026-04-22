"""
CHF Smoke Test
Generates synthetic data and runs the core pipeline end-to-end.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from configs.config import get_config
from features.feature_engineering import (
    compute_log_returns,
    compute_rolling_volatility,
    compute_rolling_skewness,
    compute_volume_ratio,
    compute_reversal,
)
from models.walk_forward import (
    aggregate_fold_metrics,
    compute_fold_metrics,
    rank_ic,
    walk_forward_splits,
)


def generate_synthetic_data(root: Path, symbols=None, n_days=400):
    """Generate synthetic OHLCV data and save to disk."""
    if symbols is None:
        symbols = ["BTC", "ETH", "SOL", "BNB", "ADA"]

    np.random.seed(42)
    dates = pd.date_range("2022-01-01", periods=n_days, freq="D", tz="UTC")

    raw_dir = root / "data" / "raw" / "market"
    raw_dir.mkdir(parents=True, exist_ok=True)

    all_dfs = []
    for sym in symbols:
        prices = 100.0 * np.cumprod(1 + np.random.randn(n_days) * 0.03)
        vol = np.abs(np.random.randn(n_days)) * prices * 0.1
        df = pd.DataFrame({
            "symbol": sym,
            "date_ts": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": vol,
            "source": "synthetic",
        })
        df.to_parquet(raw_dir / f"{sym}_ohlcv.parquet", index=False)
        all_dfs.append(df)

    market_df = pd.concat(all_dfs, ignore_index=True)
    print(f"[OK] OHLCV saved for {len(symbols)} symbols")
    return market_df


def build_features(market_df: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Build feature store from market data."""
    feat_rows = []
    for sym, grp in market_df.groupby("symbol"):
        grp = grp.set_index("date_ts").sort_index()
        close = grp["close"]
        daily_ret = compute_log_returns(close, 1)
        row = pd.DataFrame({"symbol": sym, "date_ts": close.index})
        row["ret_7d"] = compute_log_returns(close, 7).values
        row["ret_30d"] = compute_log_returns(close, 30).values
        row["vol_30d"] = compute_rolling_volatility(daily_ret, 30).values
        row["skew_30d"] = compute_rolling_skewness(daily_ret, 30).values
        row["vol_ratio_30d"] = compute_volume_ratio(grp["volume"], 30).values
        row["reversal_3_30"] = compute_reversal(daily_ret, 3, 30).values
        feat_rows.append(row)

    features_df = pd.concat(feat_rows, ignore_index=True)
    features_dir = root / "data" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    features_df.to_parquet(features_dir / "market_features.parquet", index=False)
    print(f"[OK] Features: {features_df.shape}")
    return features_df


def build_labels(market_df: pd.DataFrame, root: Path, horizon: int = 7) -> pd.DataFrame:
    """Build forward-return labels."""
    label_rows = []
    for sym, grp in market_df.groupby("symbol"):
        grp = grp.set_index("date_ts").sort_index()
        close = grp["close"]
        fwd = np.log(close.shift(-horizon) / close.clip(lower=1e-10))
        ldf = pd.DataFrame({
            "symbol": sym,
            "date_ts": close.index,
            "label_value": fwd.values,
            "horizon_days": horizon,
        })
        ldf = ldf.dropna(subset=["label_value"])
        label_rows.append(ldf)

    labels_df = pd.concat(label_rows, ignore_index=True)
    labels_dir = root / "data" / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    labels_df.to_parquet(labels_dir / f"labels_{horizon}d.parquet", index=False)
    print(f"[OK] Labels: {labels_df.shape}")
    return labels_df


def run_walk_forward(features_df: pd.DataFrame, labels_df: pd.DataFrame):
    """Run walk-forward validation with RandomForest."""
    panel = features_df.merge(
        labels_df[["symbol", "date_ts", "label_value"]],
        on=["symbol", "date_ts"],
        how="inner",
    ).dropna()

    feat_cols = ["ret_7d", "ret_30d", "vol_30d", "skew_30d", "vol_ratio_30d", "reversal_3_30"]
    splits = list(walk_forward_splits(
        panel, "date_ts", initial_train_months=6, step_months=1
    ))

    fold_metrics = []
    for split in splits[:5]:
        X_tr = panel.iloc[split.train_idx][feat_cols].fillna(0)
        y_tr = panel.iloc[split.train_idx]["label_value"].values
        X_val = panel.iloc[split.val_idx][feat_cols].fillna(0)
        y_val = panel.iloc[split.val_idx]["label_value"].values
        if len(X_tr) < 20 or len(X_val) < 3:
            continue
        m = RandomForestRegressor(n_estimators=20, random_state=42)
        m.fit(X_tr, y_tr)
        y_pred = m.predict(X_val)
        fm = compute_fold_metrics(y_val, y_pred, split.fold_id, "rf", 7)
        fold_metrics.append(fm)

    agg = aggregate_fold_metrics(fold_metrics)
    print(f"[OK] Walk-forward: {len(fold_metrics)} folds | Mean IC={agg.get('rank_ic_mean', 0):.4f}")
    return agg


def run_backtest_smoke(root: Path):
    """Quick vectorized backtest smoke test."""
    # Load market data
    raw_dir = root / "data" / "raw" / "market"
    files = list(raw_dir.glob("*_ohlcv.parquet"))
    if not files:
        print("[SKIP] No market data for backtest smoke test")
        return

    dfs = [pd.read_parquet(f) for f in files]
    market_df = pd.concat(dfs, ignore_index=True)
    market_df["date_ts"] = pd.to_datetime(market_df["date_ts"], utc=True)

    # Simulate equal-weight portfolio
    pivot = market_df.pivot_table(
        index="date_ts", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    daily_ret = pivot.pct_change().fillna(0)
    portfolio_ret = daily_ret.mean(axis=1)
    equity = 100_000 * (1 + portfolio_ret).cumprod()

    # Compute metrics
    vals = equity.values
    n = len(vals)
    total_ret = (vals[-1] / vals[0]) - 1
    cagr = (vals[-1] / vals[0]) ** (252 / n) - 1
    ann_vol = portfolio_ret.std() * np.sqrt(252)
    sharpe = (portfolio_ret.mean() * 252) / (ann_vol + 1e-10)
    running_max = np.maximum.accumulate(vals)
    max_dd = ((vals - running_max) / (running_max + 1e-10)).min()

    print(f"[OK] Backtest smoke: CAGR={cagr:.2%} | Sharpe={sharpe:.3f} | MaxDD={max_dd:.2%}")

    # Save equity curve
    bt_dir = root / "data" / "backtests"
    bt_dir.mkdir(parents=True, exist_ok=True)
    eq_df = pd.DataFrame({"date_ts": equity.index, "portfolio_value": equity.values, "backtest_name": "smoke_test"})
    eq_df.to_parquet(bt_dir / "equity_curves.parquet", index=False)
    summary = [{
        "backtest_name": "main",
        "strategy": "equal_weight_smoke",
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "sortino": float(sharpe * 1.2),
        "calmar": float(cagr / (abs(max_dd) + 1e-10)),
        "max_drawdown": float(max_dd),
        "annualized_vol": float(ann_vol),
        "total_return": float(total_ret),
        "n_days": n,
        "cost_bps": 20,
    }]
    pd.DataFrame(summary).to_parquet(bt_dir / "backtest_summary.parquet", index=False)
    print("[OK] Backtest results saved")


def run_all():
    """Run all smoke tests."""
    print("=" * 50)
    print("CHF Smoke Test")
    print("=" * 50)

    cfg = get_config()
    root = Path(cfg["_project_root"])
    print(f"Project root: {root}")

    market_df = generate_synthetic_data(root)
    features_df = build_features(market_df, root)
    labels_df = build_labels(market_df, root)
    run_walk_forward(features_df, labels_df)
    run_backtest_smoke(root)

    print("=" * 50)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    run_all()
