"""Daily risk dashboard for all paper-trade books.

For each book: current exposures from ``state.json`` marked at the latest
market close, cash share, top-position concentration, realized 30d vol
(annualized sqrt(365)), current & max drawdown, 60d OLS BTC beta/correlation,
and per-asset risk contributions from an EWMA covariance decomposition of
w^T Sigma w. Exit 1 when a drawdown/concentration alert triggers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from monitoring.common import (
    ANNUALIZATION_DAYS,
    annualized_vol,
    build_arg_parser,
    current_drawdown,
    exit_code,
    list_papertrade_books,
    load_config,
    max_drawdown,
    read_json_safe,
    read_parquet_safe,
    resolve_root,
    write_report,
)


def ewma_cov(returns: pd.DataFrame, lam: float = 0.94) -> pd.DataFrame:
    """EWMA covariance of a (date x asset) simple-return matrix."""
    clean = returns.dropna(how="all").fillna(0.0)
    n = len(clean)
    if n < 2:
        return pd.DataFrame(np.zeros((returns.shape[1], returns.shape[1])),
                            index=returns.columns, columns=returns.columns)
    weights = lam ** np.arange(n - 1, -1, -1)
    weights /= weights.sum()
    demeaned = clean - clean.mean()
    cov = (demeaned.T * weights) @ demeaned
    return pd.DataFrame(cov, index=returns.columns, columns=returns.columns)


def risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> Dict[str, Any]:
    """Decompose portfolio variance w^T Sigma w into per-asset shares."""
    w = weights.reindex(cov.index).fillna(0.0).to_numpy(dtype=float)
    sigma = cov.to_numpy(dtype=float)
    port_var = float(w @ sigma @ w)
    marginal = sigma @ w
    contrib = w * marginal  # sums to port_var
    shares = contrib / port_var if port_var > 0 else np.zeros_like(contrib)
    return {
        "portfolio_variance_daily": port_var,
        "portfolio_vol_annualized": float(np.sqrt(max(port_var, 0.0) * ANNUALIZATION_DAYS)),
        "contributions": {
            sym: {"variance_contribution": float(c), "share": float(s)}
            for sym, c, s in zip(cov.index, contrib, shares)
        },
    }


def ols_beta(portfolio: pd.Series, benchmark: pd.Series) -> Dict[str, Optional[float]]:
    joined = pd.concat([portfolio, benchmark], axis=1, keys=["p", "b"]).dropna()
    if len(joined) < 10 or float(joined["b"].var(ddof=1)) == 0.0:
        return {"beta": None, "correlation": None, "n_obs": len(joined)}
    beta = float(joined["p"].cov(joined["b"]) / joined["b"].var(ddof=1))
    corr = float(joined["p"].corr(joined["b"]))
    return {"beta": beta, "correlation": corr, "n_obs": int(len(joined))}


def book_snapshot(
    state: Dict[str, Any],
    prices: pd.Series,
    returns: pd.DataFrame,
    equity: Optional[pd.DataFrame],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Risk snapshot for one book given its state, latest prices and returns."""
    cash = float(state.get("cash", 0.0))
    positions = {str(s): float(q) for s, q in (state.get("positions") or {}).items()}
    values: Dict[str, float] = {}
    unpriced: List[str] = []
    for sym, qty in positions.items():
        px = prices.get(sym)
        if px is None or pd.isna(px):
            unpriced.append(sym)
        else:
            values[sym] = qty * float(px)
    nav = cash + sum(values.values())
    weights = pd.Series({s: v / nav for s, v in values.items()}) if nav > 0 else pd.Series(dtype=float)

    out: Dict[str, Any] = {
        "nav": nav,
        "cash": cash,
        "cash_share": cash / nav if nav > 0 else None,
        "n_positions": len(positions),
        "unpriced_symbols": unpriced,
        "exposures": {s: {"value_usd": v, "weight": v / nav if nav > 0 else None} for s, v in values.items()},
    }
    if len(weights):
        top = weights.sort_values(ascending=False)
        out["top_position"] = {"symbol": top.index[0], "weight": float(top.iloc[0])}
        out["top3_weight"] = float(top.head(3).sum())
    else:
        out["top_position"] = None
        out["top3_weight"] = 0.0

    held = [s for s in weights.index if s in returns.columns]
    if held:
        sub = returns[held].tail(int(cfg.get("returns_lookback_days", 180)))
        cov = ewma_cov(sub, lam=float(cfg.get("ewma_lambda", 0.94)))
        out["risk_decomposition"] = risk_contributions(weights[held], cov)
        # synthetic portfolio returns at current weights for beta/vol backfill
        port_ret = (sub.fillna(0.0) @ weights[held].reindex(held).fillna(0.0)).astype(float)
        beta_window = int(cfg.get("beta_window", 60))
        if "BTC" in returns.columns:
            out["btc_beta_60d"] = ols_beta(port_ret.tail(beta_window), returns["BTC"].tail(beta_window))
        vol_window = int(cfg.get("vol_window", 30))
        out["synthetic_vol_30d_annualized"] = annualized_vol(port_ret.tail(vol_window))
    else:
        out["risk_decomposition"] = None
        out["btc_beta_60d"] = None
        out["synthetic_vol_30d_annualized"] = None

    out["realized_vol_30d_annualized"] = None
    out["current_drawdown"] = None
    out["max_drawdown"] = None
    if equity is not None and not equity.empty and "nav" in equity.columns:
        eq = equity.sort_values("date")
        rets = eq["daily_return"].astype(float).iloc[1:] if "daily_return" in eq.columns else pd.Series(dtype=float)
        if len(rets) >= 10:
            out["realized_vol_30d_annualized"] = annualized_vol(rets.tail(int(cfg.get("vol_window", 30))))
        out["current_drawdown"] = current_drawdown(eq["nav"])
        out["max_drawdown"] = max_drawdown(eq["nav"])
    return out


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF daily risk dashboard").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("risk_report", {})

    market = read_parquet_safe(root / paths["market"], columns=["date_ts", "symbol", "close"])
    if market is None or market.empty:
        write_report(root, paths["reports_dir"], "risk_report",
                     {"status": "missing_input", "detail": paths["market"], "alerts": []})
        print("risk_report: market data missing; nothing to mark against")
        return 0
    market = market.dropna(subset=["close"]).sort_values("date_ts")
    prices = market.groupby("symbol")["close"].last()
    lookback = int(cfg.get("returns_lookback_days", 180)) + 5
    close_wide = (
        market.assign(date=market["date_ts"].dt.normalize())
        .pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
        .sort_index()
        .tail(lookback)
    )
    returns = close_wide.pct_change(fill_method=None)

    books = list_papertrade_books(root, paths["papertrade_dir"])
    alerts: List[str] = []
    book_reports: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    for book in books:
        book_dir = root / paths["papertrade_dir"] / book
        state = read_json_safe(book_dir / "state.json")
        if not isinstance(state, dict):
            book_reports[book] = {"status": "missing_state"}
            continue
        equity = read_parquet_safe(book_dir / "equity.parquet")
        snap = book_snapshot(state, prices, returns, equity, cfg)
        book_reports[book] = snap
        dd = snap.get("current_drawdown")
        if dd is not None and dd < float(cfg.get("max_drawdown_alert", -0.25)):
            alerts.append(f"{book}: current drawdown {dd:.1%} breaches alert level")
        top = snap.get("top_position")
        conc_exempt = book in set(cfg.get("concentration_exclude_books", []) or [])
        if top and not conc_exempt and top["weight"] > float(cfg.get("top_weight_alert", 0.60)):
            alerts.append(f"{book}: top position {top['symbol']} weight {top['weight']:.1%} exceeds limit")
        rows.append(
            {
                "book": book,
                "nav": snap["nav"],
                "cash_share": snap["cash_share"],
                "n_positions": snap["n_positions"],
                "top_weight": top["weight"] if top else None,
                "vol_30d": snap.get("realized_vol_30d_annualized") or snap.get("synthetic_vol_30d_annualized"),
                "current_drawdown": snap.get("current_drawdown"),
                "max_drawdown": snap.get("max_drawdown"),
                "btc_beta_60d": (snap.get("btc_beta_60d") or {}).get("beta") if snap.get("btc_beta_60d") else None,
                "unpriced_symbols": json.dumps(snap["unpriced_symbols"]),
            }
        )

    summary = {
        "status": "alert" if alerts else "ok",
        "as_of_market_date": str(market["date_ts"].max().date()),
        "n_books": len(books),
        "alerts": alerts,
        "books": book_reports,
    }
    written = write_report(root, paths["reports_dir"], "risk_report", summary, pd.DataFrame(rows))
    print(f"risk_report: status={summary['status']} books={len(books)} -> {written['json']}")
    for alert in alerts:
        print(f"  ALERT: {alert}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
