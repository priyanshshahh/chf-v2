"""Cointegration / z-score pairs-trading sleeve (market-neutral, short-capable).

For each candidate pair (A, B) of liquid assets:
    * hedge ratio beta = rolling OLS of log(A) on log(B) (leakage-safe: uses
      only closes up to the signal date).
    * spread_t = log(A_t) - beta_t * log(B_t); z = (spread - mean)/std over a
      rolling window.
    * Deterministic hysteresis walk over the z history decides the current
      position:  enter when |z| > entry_z (2.0), hold while |z| > exit_z (0.5),
      flatten when |z| <= exit_z, and hard-stop to flat when |z| > stop_z (4.0).
    * z > +entry  -> A rich  -> SHORT A / LONG B
      z < -entry  -> A cheap -> LONG  A / SHORT B
    * Legs are equal-dollar (dollar-neutral): +w to the long leg, -w to the
      short leg. All legs are spot; the book must allow shorts.

Candidate pairs = configured pairs present in the panel, then filled up to
``max_pairs`` with the most correlated pairs among the top liquid symbols
(deterministic, alphabetical tie-break). Emits a SIGNED weights frame.
"""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .base import (
    Sleeve,
    build_signed_weights_frame,
    close_panel,
    signal_date_for,
    top_liquid_symbols,
)

DEFAULT_PARAMS = {
    "configured_pairs": [["ETH", "BTC"]],
    "auto_pairs_from_top_n": 12,   # correlation search universe
    "adv_window_days": 90,
    "max_pairs": 3,
    "ols_window_days": 60,
    "zscore_window_days": 60,
    "min_correlation": 0.6,        # auto pairs must be at least this correlated
    "entry_z": 2.0,
    "exit_z": 0.5,
    "stop_z": 4.0,
    "gross_target": 1.0,           # summed |weight| across all legs
    "min_history_days": 130,
}


def rolling_hedge_z(
    log_a: pd.Series, log_b: pd.Series, ols_window: int, z_window: int
) -> Optional[pd.Series]:
    """Rolling-OLS hedge ratio -> spread -> rolling z-score series (aligned)."""
    df = pd.concat([log_a.rename("a"), log_b.rename("b")], axis=1).dropna()
    if len(df) < max(ols_window, z_window) + 2:
        return None
    a, b = df["a"], df["b"]
    # rolling beta = cov(a,b)/var(b) over ols_window (no intercept drift term)
    mean_a = a.rolling(ols_window).mean()
    mean_b = b.rolling(ols_window).mean()
    cov = (a * b).rolling(ols_window).mean() - mean_a * mean_b
    var = (b * b).rolling(ols_window).mean() - mean_b * mean_b
    beta = cov / var.replace(0.0, np.nan)
    spread = a - beta * b
    z = (spread - spread.rolling(z_window).mean()) / spread.rolling(z_window).std(ddof=1)
    return z.dropna()


def walk_pair_position(z: pd.Series, entry: float, exit_: float, stop: float) -> int:
    """Deterministic hysteresis walk -> current position sign for the pair.

    +1 = long A / short B (z <= -entry), -1 = short A / long B (z >= +entry),
    0 = flat. Held while |z| > exit; hard-stopped when |z| > stop.
    """
    pos = 0
    for val in z:
        if not np.isfinite(val):
            continue
        if pos == 0:
            if val >= entry:
                pos = -1
            elif val <= -entry:
                pos = 1
        else:
            if abs(val) > stop or abs(val) <= exit_:
                pos = 0
            elif val >= entry:
                pos = -1
            elif val <= -entry:
                pos = 1
    return pos


class StatArbSleeve(Sleeve):
    name = "sleeve_statarb"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def _candidate_pairs(
        self, market_df: pd.DataFrame, closes: pd.DataFrame, signal_date
    ) -> List[Tuple[str, str]]:
        p = self.params
        available = set(closes.columns[closes.notna().sum(axis=0) >= int(p["min_history_days"])])
        pairs: List[Tuple[str, str]] = []
        for raw in p["configured_pairs"]:
            a, b = str(raw[0]).upper(), str(raw[1]).upper()
            if a in available and b in available and (a, b) not in pairs:
                pairs.append((a, b))
        # fill with the most correlated pairs among top liquid names
        top = [s for s in top_liquid_symbols(
            market_df, signal_date, n=int(p["auto_pairs_from_top_n"]),
            adv_window=int(p["adv_window_days"]),
            min_history_days=int(p["min_history_days"]),
        ) if s in available]
        rets = closes[top].pct_change(fill_method=None)
        scored: List[Tuple[float, Tuple[str, str]]] = []
        for a, b in combinations(sorted(top), 2):
            if (a, b) in pairs or (b, a) in pairs:
                continue
            corr = rets[a].corr(rets[b])
            if pd.notna(corr) and corr >= float(p["min_correlation"]):
                scored.append((float(corr), (a, b)))
        scored.sort(key=lambda kv: (-kv[0], kv[1]))
        for _, pair in scored:
            if len(pairs) >= int(p["max_pairs"]):
                break
            pairs.append(pair)
        return pairs[: int(p["max_pairs"])]

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        closes = close_panel(market_df)
        closes = closes.loc[closes.index <= signal_date]

        pairs = self._candidate_pairs(market_df, closes, signal_date)
        # accumulate signed weight per (symbol) then map to legs
        active: List[Tuple[str, str, int]] = []  # (A, B, position sign)
        for a, b in pairs:
            z = rolling_hedge_z(
                np.log(closes[a]), np.log(closes[b]),
                int(p["ols_window_days"]), int(p["zscore_window_days"]),
            )
            if z is None or z.empty:
                continue
            pos = walk_pair_position(
                z, float(p["entry_z"]), float(p["exit_z"]), float(p["stop_z"])
            )
            if pos != 0:
                active.append((a, b, pos))

        legs: List[dict] = []
        if active:
            # equal-dollar per leg; gross across all legs == gross_target
            w = float(p["gross_target"]) / (2.0 * len(active))
            for a, b, pos in active:
                # pos +1: long A / short B ; pos -1: short A / long B
                legs.append({"symbol": a, "weight": w * pos, "instrument_type": "spot"})
                legs.append({"symbol": b, "weight": -w * pos, "instrument_type": "spot"})
        return build_signed_weights_frame(legs, signal_date, self.name)
