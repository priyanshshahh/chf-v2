"""Five-family technical ensemble sleeve, crypto-tuned.

Formulas adapted from the public-domain technical analyst in
https://github.com/virattt/ai-hedge-fund (src/agents/technicals.py) —
plain-math indicator arithmetic (EMA/ADX/Bollinger/Hurst/skew), re-implemented
deterministically here and tuned for crypto: sqrt(365) annualization and
21/63/126-day windows kept as in the source.

Per-asset families (each returns a score in [-1, 1]):
    1. trend (w=0.25): EMA 8/21/55 alignment; confidence = ADX(14)/100.
       ema8>ema21>ema55 -> +adx/100; ema8<ema21<ema55 -> -adx/100; else 0.
    2. mean_reversion (w=0.25): z = (close - MA50)/STD50 and Bollinger %B
       (20d, 2 sigma). z < -2 and %B < 0.2 -> +min(|z|/4, 1);
       z > +2 and %B > 0.8 -> -min(|z|/4, 1); else 0.
    3. momentum (w=0.20): mom = 0.4*r21 + 0.3*r63 + 0.3*r126;
       volume confirm = last volume / 21d mean volume > 1.
       score = clip(mom*5, -1, 1), halved when volume does not confirm.
    4. volatility_regime (w=0.15): vol21 (sqrt(365)-annualized) vs its 63d
       mean; vol_z = (vol21 - mean63)/std63. ratio < 0.8 and vol_z < -1 ->
       +min(|vol_z|/3, 1); ratio > 1.2 and vol_z > +1 -> -min(vol_z/3, 1).
    5. stat_arb (w=0.15): Hurst exponent via R/S log-log slope + 63d skew.
       hurst < 0.4 (mean-reverting): conf = min((0.5-hurst)*2, 1);
       skew63 > 1 -> +conf; skew63 < -1 -> -conf; else 0. hurst >= 0.4 -> 0.

Combined score = 0.25*trend + 0.25*meanrev + 0.20*mom + 0.15*vol + 0.15*statarb.
Sleeve holds the top-5 scorers with score > 0.2, inverse-30d-vol weighted,
gross scaled by n_holdings/5 (fewer qualifiers -> more cash).
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .base import (
    ANNUALIZATION_FACTOR,
    Sleeve,
    build_weights_frame,
    close_panel,
    inverse_vol_weights,
    realized_vol,
    signal_date_for,
    top_liquid_symbols,
    trailing_return,
)

FAMILY_WEIGHTS = {
    "trend": 0.25,
    "mean_reversion": 0.25,
    "momentum": 0.20,
    "volatility_regime": 0.15,
    "stat_arb": 0.15,
}

DEFAULT_PARAMS = {
    "universe_size": 20,
    "adv_window_days": 90,
    "min_history_days": 200,
    "top_k": 5,
    "score_threshold": 0.2,
    "vol_window_days": 30,
}


# ---------------------------------------------------------------------------
# indicator primitives
# ---------------------------------------------------------------------------


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> Optional[float]:
    """Wilder ADX; returns the latest value (0-100) or None on short history."""
    df = pd.DataFrame({"high": high, "low": low, "close": close}).dropna()
    if len(df) < window * 3:
        return None
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    alpha = 1.0 / window
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    adx_series = dx.ewm(alpha=alpha, adjust=False).mean().dropna()
    if adx_series.empty:
        return None
    value = float(adx_series.iloc[-1])
    return value if np.isfinite(value) else None


def hurst_rs(series: pd.Series, min_window: int = 8, max_window: int = 64) -> Optional[float]:
    """Hurst exponent from the rescaled-range (R/S) log-log slope.

    For window sizes n in doubling steps [min_window..max_window], average
    R/S over non-overlapping segments of log returns, then fit
    log(R/S) ~ H * log(n).
    """
    prices = series.dropna()
    if len(prices) < max_window * 2:
        return None
    rets = np.log(prices / prices.shift(1)).dropna().to_numpy()
    sizes = []
    n = min_window
    while n <= max_window:
        sizes.append(n)
        n *= 2
    log_n, log_rs = [], []
    for size in sizes:
        n_segments = len(rets) // size
        if n_segments < 1:
            continue
        rs_vals = []
        for i in range(n_segments):
            seg = rets[i * size:(i + 1) * size]
            dev = seg - seg.mean()
            cum = np.cumsum(dev)
            r = cum.max() - cum.min()
            s = seg.std(ddof=1)
            if s > 0 and r > 0:
                rs_vals.append(r / s)
        if rs_vals:
            log_n.append(np.log(size))
            log_rs.append(np.log(np.mean(rs_vals)))
    if len(log_n) < 3:
        return None
    slope = float(np.polyfit(log_n, log_rs, 1)[0])
    return slope if np.isfinite(slope) else None


# ---------------------------------------------------------------------------
# family scores (each in [-1, 1])
# ---------------------------------------------------------------------------


def trend_score(close: pd.Series, high: pd.Series, low: pd.Series) -> float:
    s = close.dropna()
    if len(s) < 60:
        return 0.0
    e8, e21, e55 = ema(s, 8).iloc[-1], ema(s, 21).iloc[-1], ema(s, 55).iloc[-1]
    strength = adx(high, low, close)
    conf = min((strength or 0.0) / 100.0, 1.0)
    if e8 > e21 > e55:
        return conf
    if e8 < e21 < e55:
        return -conf
    return 0.0


def mean_reversion_score(close: pd.Series) -> float:
    s = close.dropna()
    if len(s) < 50:
        return 0.0
    ma50 = float(s.rolling(50).mean().iloc[-1])
    std50 = float(s.rolling(50).std(ddof=1).iloc[-1])
    if not np.isfinite(std50) or std50 <= 0:
        return 0.0
    z = (float(s.iloc[-1]) - ma50) / std50
    mid = s.rolling(20).mean()
    sd = s.rolling(20).std(ddof=1)
    upper = float((mid + 2 * sd).iloc[-1])
    lower = float((mid - 2 * sd).iloc[-1])
    if upper == lower:
        return 0.0
    pct_b = (float(s.iloc[-1]) - lower) / (upper - lower)
    strength = min(abs(z) / 4.0, 1.0)
    if z < -2.0 and pct_b < 0.2:
        return strength
    if z > 2.0 and pct_b > 0.8:
        return -strength
    return 0.0


def momentum_score(close: pd.Series, volume: pd.Series) -> float:
    r21 = trailing_return(close, 21)
    r63 = trailing_return(close, 63)
    r126 = trailing_return(close, 126)
    if r21 is None or r63 is None or r126 is None:
        return 0.0
    mom = 0.4 * r21 + 0.3 * r63 + 0.3 * r126
    score = float(np.clip(mom * 5.0, -1.0, 1.0))
    vol = volume.dropna()
    confirmed = False
    if len(vol) >= 21:
        vol_ma21 = float(vol.rolling(21).mean().iloc[-1])
        confirmed = vol_ma21 > 0 and float(vol.iloc[-1]) / vol_ma21 > 1.0
    return score if confirmed else score * 0.5


def volatility_regime_score(close: pd.Series) -> float:
    s = close.dropna()
    if len(s) < 21 + 63 + 1:
        return 0.0
    rets = s.pct_change().dropna()
    vol21 = rets.rolling(21).std(ddof=1) * ANNUALIZATION_FACTOR
    mean63 = vol21.rolling(63).mean()
    std63 = vol21.rolling(63).std(ddof=1)
    v = float(vol21.iloc[-1])
    m = float(mean63.iloc[-1])
    sd = float(std63.iloc[-1])
    if not np.isfinite(v) or not np.isfinite(m) or m <= 0 or not np.isfinite(sd) or sd <= 0:
        return 0.0
    ratio = v / m
    vol_z = (v - m) / sd
    if ratio < 0.8 and vol_z < -1.0:
        return min(abs(vol_z) / 3.0, 1.0)
    if ratio > 1.2 and vol_z > 1.0:
        return -min(vol_z / 3.0, 1.0)
    return 0.0


def stat_arb_score(close: pd.Series) -> float:
    s = close.dropna()
    hurst = hurst_rs(s)
    if hurst is None or hurst >= 0.4:
        return 0.0
    rets = s.pct_change().dropna().tail(63)
    if len(rets) < 30:
        return 0.0
    skew63 = float(rets.skew())
    conf = min((0.5 - hurst) * 2.0, 1.0)
    if skew63 > 1.0:
        return conf
    if skew63 < -1.0:
        return -conf
    return 0.0


def combined_score(
    close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series
) -> Dict[str, float]:
    scores = {
        "trend": trend_score(close, high, low),
        "mean_reversion": mean_reversion_score(close),
        "momentum": momentum_score(close, volume),
        "volatility_regime": volatility_regime_score(close),
        "stat_arb": stat_arb_score(close),
    }
    scores["combined"] = float(
        np.clip(sum(FAMILY_WEIGHTS[k] * scores[k] for k in FAMILY_WEIGHTS), -1.0, 1.0)
    )
    return scores


# ---------------------------------------------------------------------------
# sleeve
# ---------------------------------------------------------------------------


class TechnicalEnsembleSleeve(Sleeve):
    name = "sleeve_technical"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        universe = top_liquid_symbols(
            market_df,
            signal_date,
            n=int(p["universe_size"]),
            adv_window=int(p["adv_window_days"]),
            min_history_days=int(p["min_history_days"]),
        )
        cutoff = market_df["date_ts"] <= signal_date
        panel = market_df.loc[cutoff & market_df["symbol"].isin(universe)]

        scored: List[tuple] = []
        for sym, grp in panel.groupby("symbol"):
            grp = grp.sort_values("date_ts").set_index("date_ts")
            scores = combined_score(grp["close"], grp["high"], grp["low"], grp["volume"])
            scored.append((sym, scores["combined"]))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))

        holders = [
            (sym, sc) for sym, sc in scored[: int(p["top_k"])]
            if sc > float(p["score_threshold"])
        ]
        weights: Dict[str, float] = {}
        if holders:
            vols = {}
            for sym, _ in holders:
                sub = panel.loc[panel["symbol"] == sym].sort_values("date_ts")
                vol = realized_vol(sub.set_index("date_ts")["close"], int(p["vol_window_days"]))
                if vol is not None and vol > 0:
                    vols[sym] = vol
            gross = len(vols) / float(p["top_k"])
            weights = inverse_vol_weights(vols, gross=gross)
        return build_weights_frame(weights, signal_date, self.name)
