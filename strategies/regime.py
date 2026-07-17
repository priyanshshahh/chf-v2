"""Daily market regime classifier from data CHF already has (deterministic).

Inputs per day:
    * BTC trend: BTC close vs its 100d simple MA (up/down).
    * Breadth: % of that day's universe members (``is_universe_member``,
      fallback: all symbols with data) trading above their own 50d MA.
    * Correlation spike: average pairwise 60d return correlation of the
      top-20 liquid coins (fixed set as of the latest date — documented
      simplification) > 0.8.
    * BTC dominance trend: CMC global-metrics btc_dominance vs its 30d MA
      (rising/falling/unknown).
    * Fear & greed bucket: CMC index value bucketed
      (<25 extreme_fear, <45 fear, <=55 neutral, <=75 greed, else
      extreme_greed; missing -> unknown).

Composite regime label per day:
    stress   : correlation spike AND BTC trend down
    risk_off : BTC trend down (no corr spike)
    risk_on  : BTC trend up AND breadth >= 0.5
    neutral  : otherwise

Outputs: data/strategies/regime_daily.parquet + data/strategies/regime_latest.json.
Sleeve multipliers per regime live in configs/sleeves.yaml.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .base import close_panel, signal_date_for, to_utc, top_liquid_symbols

DEFAULT_PARAMS = {
    "btc_ma_window_days": 100,
    "breadth_ma_window_days": 50,
    "breadth_risk_on_min": 0.5,
    "corr_window_days": 60,
    "corr_top_n": 20,
    "corr_adv_window_days": 90,
    "corr_spike_threshold": 0.8,
    "dominance_ma_window_days": 30,
    "global_metrics_path": (
        "cmc_complete/data/coinmarketcap_data/pro_api_global_metrics_historical/"
        "cmc_global_metrics.parquet"
    ),
    "fear_greed_path": (
        "cmc_complete/data/coinmarketcap_data/pro_api_index_historical/cmc_fear_greed.parquet"
    ),
    "output_parquet": "data/strategies/regime_daily.parquet",
    "output_json": "data/strategies/regime_latest.json",
}


def fear_greed_bucket(value) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)) or pd.isna(value):
        return "unknown"
    v = float(value)
    if v < 25:
        return "extreme_fear"
    if v < 45:
        return "fear"
    if v <= 55:
        return "neutral"
    if v <= 75:
        return "greed"
    return "extreme_greed"


def classify_row(btc_trend_up: bool, breadth: float, corr_spike: bool,
                 breadth_risk_on_min: float = 0.5) -> str:
    if corr_spike and not btc_trend_up:
        return "stress"
    if not btc_trend_up:
        return "risk_off"
    if breadth >= breadth_risk_on_min:
        return "risk_on"
    return "neutral"


def _load_dated_series(path: str | Path, value_col: str) -> Optional[pd.Series]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if "date" not in df.columns or value_col not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")[value_col].sort_index()


def build_regime_daily(market_df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Full daily regime panel over the market history."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    closes = close_panel(market_df)

    # BTC trend
    if "BTC" not in closes.columns:
        raise ValueError("regime classifier requires BTC in the market panel")
    btc = closes["BTC"]
    btc_ma = btc.rolling(int(p["btc_ma_window_days"]), min_periods=int(p["btc_ma_window_days"])).mean()
    btc_trend_up = (btc > btc_ma).fillna(False)

    # Breadth over per-day universe members (fallback: any symbol with data)
    ma50 = closes.rolling(int(p["breadth_ma_window_days"]),
                          min_periods=int(p["breadth_ma_window_days"])).mean()
    above = closes > ma50
    if "is_universe_member" in market_df.columns and market_df["is_universe_member"].any():
        member = (
            market_df.pivot_table(
                index="date_ts", columns="symbol", values="is_universe_member",
                aggfunc="last",
            )
            .reindex(closes.index)
            .astype("boolean")
            .fillna(False)
            .astype(bool)
        )
        # Days with zero flagged members (e.g. dates past the last monthly
        # snapshot) fall back to "every symbol with data" so breadth is
        # never undefined on recent days.
        no_members = member.sum(axis=1) == 0
        member.loc[no_members] = closes.notna().loc[no_members]
    else:
        member = closes.notna()
    valid = member & ma50.notna()
    breadth = (above & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan)

    # Correlation spike: fixed top-20 liquid set as of the latest date
    latest = closes.index.max()
    top20 = top_liquid_symbols(
        market_df, latest, n=int(p["corr_top_n"]),
        adv_window=int(p["corr_adv_window_days"]),
        min_history_days=int(p["corr_window_days"]) + 1,
    )
    corr_window = int(p["corr_window_days"])
    rets = closes[top20].pct_change() if top20 else pd.DataFrame(index=closes.index)
    avg_corr = pd.Series(np.nan, index=closes.index)
    if len(top20) >= 2:
        # mean pairwise corr == (mean of corr matrix incl. diag * n - n) / (n*(n-1))
        for i in range(corr_window, len(rets)):
            window = rets.iloc[i - corr_window + 1: i + 1]
            window = window.dropna(axis=1, thresh=int(corr_window * 0.8))
            n = window.shape[1]
            if n < 2:
                continue
            cm = window.corr().to_numpy()
            avg_corr.iloc[i] = (np.nansum(cm) - n) / (n * (n - 1))
    corr_spike = (avg_corr > float(p["corr_spike_threshold"])).fillna(False)

    # BTC dominance trend (external, optional)
    dom = _load_dated_series(p["global_metrics_path"], "btc_dominance")
    if dom is not None:
        dom = dom.reindex(closes.index, method="ffill")
        dom_ma = dom.rolling(int(p["dominance_ma_window_days"]),
                             min_periods=int(p["dominance_ma_window_days"])).mean()
        dom_trend = pd.Series(
            np.where(dom.isna() | dom_ma.isna(), "unknown",
                     np.where(dom > dom_ma, "rising", "falling")),
            index=closes.index,
        )
    else:
        dom = pd.Series(np.nan, index=closes.index)
        dom_trend = pd.Series("unknown", index=closes.index)

    # Fear & greed (external, optional)
    fg = _load_dated_series(p["fear_greed_path"], "value")
    if fg is not None:
        fg = fg.reindex(closes.index, method="ffill")
    else:
        fg = pd.Series(np.nan, index=closes.index)
    fg_bucket = fg.map(fear_greed_bucket)

    out = pd.DataFrame(
        {
            "date_ts": closes.index,
            "btc_close": btc.values,
            "btc_ma": btc_ma.values,
            "btc_trend_up": btc_trend_up.values,
            "breadth_pct_above_50d_ma": breadth.values,
            "avg_pairwise_corr_60d_top20": avg_corr.values,
            "corr_spike": corr_spike.values,
            "btc_dominance": dom.values,
            "btc_dominance_trend": dom_trend.values,
            "fear_greed_value": fg.values,
            "fear_greed_bucket": fg_bucket.values,
        }
    )
    out["regime"] = [
        classify_row(bool(t), float(b) if np.isfinite(b) else 0.0, bool(c),
                     float(p["breadth_risk_on_min"]))
        for t, b, c in zip(out["btc_trend_up"],
                           out["breadth_pct_above_50d_ma"].fillna(0.0),
                           out["corr_spike"])
    ]
    return out.reset_index(drop=True)


def run_regime(market_df: pd.DataFrame, as_of=None, params: dict | None = None) -> dict:
    """Build + persist the daily regime panel; return the latest snapshot."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    daily = build_regime_daily(market_df, p)
    if as_of is not None:
        cutoff = signal_date_for(market_df, as_of)
        daily = daily[daily["date_ts"] <= cutoff]
    parquet_path = Path(p["output_parquet"])
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(parquet_path, index=False)

    last = daily.iloc[-1]
    latest = {
        "date": str(pd.Timestamp(last["date_ts"]).date()),
        "regime": str(last["regime"]),
        "btc_trend_up": bool(last["btc_trend_up"]),
        "breadth_pct_above_50d_ma": (
            float(last["breadth_pct_above_50d_ma"])
            if pd.notna(last["breadth_pct_above_50d_ma"]) else None
        ),
        "avg_pairwise_corr_60d_top20": (
            float(last["avg_pairwise_corr_60d_top20"])
            if pd.notna(last["avg_pairwise_corr_60d_top20"]) else None
        ),
        "corr_spike": bool(last["corr_spike"]),
        "btc_dominance_trend": str(last["btc_dominance_trend"]),
        "fear_greed_value": (
            float(last["fear_greed_value"]) if pd.notna(last["fear_greed_value"]) else None
        ),
        "fear_greed_bucket": str(last["fear_greed_bucket"]),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p["output_json"]).write_text(json.dumps(latest, indent=2))
    return latest
