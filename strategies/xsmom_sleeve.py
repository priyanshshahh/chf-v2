"""BTC-relative cross-sectional momentum sleeve with a BTC regime gate.

The "Solidum" pattern:
    * Universe: top-50 liquid coins (90d median dollar volume), BTC excluded.
    * Score: 90d return ratio vs BTC -> (1 + r_coin) / (1 + r_btc) - 1.
    * Hold the top quintile (top 10 of 50) equal-weight ONLY when
      BTC close > its 100d simple MA; otherwise 100% cash.
    * Altseason gross control: if the CMC altcoin-season index (latest value
      within ``altseason_max_staleness_days`` of the signal date) > 50, allow
      full allocation (gross 1.0); else cap gross at 0.5. Missing/stale data
      conservatively caps gross at 0.5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .base import (
    Sleeve,
    build_weights_frame,
    close_panel,
    signal_date_for,
    to_utc,
    top_liquid_symbols,
    trailing_return,
)

DEFAULT_ALTSEASON_PATH = (
    "cmc_complete/data/coinmarketcap_data/pro_api_index_historical/cmc_altcoin_season.parquet"
)

DEFAULT_PARAMS = {
    "universe_size": 50,
    "adv_window_days": 90,
    "momentum_window_days": 90,
    "quintile": 5,                      # hold top n/quintile names
    "btc_ma_window_days": 100,
    "altseason_path": DEFAULT_ALTSEASON_PATH,
    "altseason_threshold": 50.0,
    "altseason_max_staleness_days": 14,
    "capped_gross": 0.5,
    "full_gross": 1.0,
}


def load_altseason_index(
    path: str | Path,
    signal_date: pd.Timestamp,
    max_staleness_days: int,
) -> Optional[float]:
    """Latest altcoin-season index value at/<= signal_date, if fresh enough."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if "date" not in df.columns or "altcoin_index" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df[df["date"] <= signal_date].sort_values("date")
    if df.empty:
        return None
    last = df.iloc[-1]
    age = (signal_date - last["date"]).days
    if age > max_staleness_days:
        return None
    value = last["altcoin_index"]
    return float(value) if pd.notna(value) else None


class XSMomSleeve(Sleeve):
    name = "sleeve_xsmom"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    # -- components (kept separate so tests can hit them directly) ----------

    def btc_gate_open(self, closes: pd.DataFrame, signal_date: pd.Timestamp) -> bool:
        p = self.params
        if "BTC" not in closes.columns:
            return False
        btc = closes.loc[closes.index <= signal_date, "BTC"].dropna()
        window = int(p["btc_ma_window_days"])
        if len(btc) < window:
            return False
        ma = float(btc.tail(window).mean())
        return float(btc.iloc[-1]) > ma

    def rank_vs_btc(
        self, closes: pd.DataFrame, universe: List[str], signal_date: pd.Timestamp
    ) -> List[Tuple[str, float]]:
        p = self.params
        window = int(p["momentum_window_days"])
        sub = closes.loc[closes.index <= signal_date]
        btc_ret = trailing_return(sub["BTC"], window) if "BTC" in sub.columns else None
        if btc_ret is None or (1.0 + btc_ret) <= 0:
            return []
        scored = []
        for sym in universe:
            if sym not in sub.columns:
                continue
            ret = trailing_return(sub[sym], window)
            if ret is None:
                continue
            ratio = (1.0 + ret) / (1.0 + btc_ret) - 1.0
            scored.append((sym, float(ratio)))
        # deterministic: best ratio first, alphabetical tie-break
        return sorted(scored, key=lambda kv: (-kv[1], kv[0]))

    def gross_allowed(self, signal_date: pd.Timestamp) -> float:
        p = self.params
        idx = load_altseason_index(
            p["altseason_path"], signal_date, int(p["altseason_max_staleness_days"])
        )
        if idx is not None and idx > float(p["altseason_threshold"]):
            return float(p["full_gross"])
        return float(p["capped_gross"])

    # -- main ----------------------------------------------------------------

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        closes = close_panel(market_df)

        weights: Dict[str, float] = {}
        if self.btc_gate_open(closes, signal_date):
            universe = top_liquid_symbols(
                market_df,
                signal_date,
                n=int(p["universe_size"]),
                adv_window=int(p["adv_window_days"]),
                exclude=("BTC",),
                min_history_days=int(p["momentum_window_days"]) + 1,
            )
            ranked = self.rank_vs_btc(closes, universe, signal_date)
            if ranked:
                k = max(1, len(ranked) // int(p["quintile"]))
                top = [sym for sym, _ in ranked[:k]]
                gross = self.gross_allowed(signal_date)
                weights = {sym: gross / len(top) for sym in top}
        return build_weights_frame(weights, signal_date, self.name)
