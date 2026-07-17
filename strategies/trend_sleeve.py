"""Parsimonious trend-following sleeve (ANB-style).

Rule (TOTAL parameters < 10, all fixed in configs/sleeves.yaml):
    * Universe: BTC, ETH, SOL + top-10 by 90d median dollar volume (union).
    * Per-asset vote = sign(10d ret) + sign(30d ret) + sign(90d ret);
      an asset is active only if >= 2 of the 3 windows are positive.
    * Active assets are weighted inverse to their 30d realized vol,
      normalized to sum 1, then scaled by (n_active / n_universe) so that
      when few names trend the sleeve de-grosses; unallocated -> cash.

Parameter count (7): windows (10, 30, 90), min_votes=2, vol_window=30,
top_n_liquid=10, adv_window=90.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from .base import (
    Sleeve,
    build_weights_frame,
    close_panel,
    inverse_vol_weights,
    realized_vol,
    signal_date_for,
    top_liquid_symbols,
    trailing_return,
)

DEFAULT_PARAMS = {
    "core_symbols": ["BTC", "ETH", "SOL"],
    "top_n_liquid": 10,
    "adv_window_days": 90,
    "return_windows": [10, 30, 90],
    "min_votes": 2,
    "vol_window_days": 30,
}


class TrendSleeve(Sleeve):
    name = "sleeve_trend"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def universe(self, market_df: pd.DataFrame, signal_date: pd.Timestamp) -> List[str]:
        p = self.params
        max_window = max(p["return_windows"])
        liquid = top_liquid_symbols(
            market_df,
            signal_date,
            n=int(p["top_n_liquid"]),
            adv_window=int(p["adv_window_days"]),
            min_history_days=max_window + 1,
        )
        closes = close_panel(market_df)
        closes = closes.loc[closes.index <= signal_date]
        available = set(closes.columns[closes.notna().sum(axis=0) >= max_window + 1])
        core = [s for s in p["core_symbols"] if s in available]
        return sorted(set(core) | set(liquid))

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        closes = close_panel(market_df)
        closes = closes.loc[closes.index <= signal_date]
        universe = self.universe(market_df, signal_date)

        active_vols: Dict[str, float] = {}
        for sym in universe:
            series = closes[sym]
            votes = 0
            usable = True
            for window in p["return_windows"]:
                ret = trailing_return(series, int(window))
                if ret is None:
                    usable = False
                    break
                votes += 1 if ret > 0 else 0
            if not usable or votes < int(p["min_votes"]):
                continue
            vol = realized_vol(series, int(p["vol_window_days"]))
            if vol is None or vol <= 0:
                continue
            active_vols[sym] = vol

        weights: Dict[str, float] = {}
        if active_vols and universe:
            gross = len(active_vols) / float(len(universe))
            weights = inverse_vol_weights(active_vols, gross=gross)
        return build_weights_frame(weights, signal_date, self.name)
