"""Funding-rate basis-capture sleeve (daily-frequency, spot-leg paper PROXY).

IMPORTANT — this paper book is a PROXY, not the true carry trade:
    The textbook trade is long spot + short perp, earning the funding rate
    while being price-neutral. The CHF paper engine can only hold spot, so
    this sleeve's book holds ONLY the long-spot leg and therefore shows spot
    price P&L, not carry P&L. The true carry P&L (the funding accrual) is
    written to a diagnostics parquet as a virtual yield column
    (``virtual_daily_funding_yield``). Read the book's equity together with
    the diagnostics to evaluate the strategy honestly.

Rule (ANB textbook):
    * Signal: trailing 7d mean of per-interval funding, annualized as
      rate * 3 * 365 (3 funding events/day, 365 days).
    * Enter a carry slot when annualized funding > entry threshold (10%).
    * Exit the slot when it drops below the exit threshold (5%) (hysteresis,
      evaluated by walking the daily funding history deterministically).
    * Equal SLOT weighting: each active slot gets 1/max_slots of the book
      (max 3 slots); unfilled slots stay in cash. If more assets qualify than
      slots, the highest annualized funding wins (alphabetical tie-break).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .base import (
    Sleeve,
    TRADING_DAYS_PER_YEAR,
    build_signed_weights_frame,
    build_weights_frame,
    signal_date_for,
    to_utc,
)

FUNDING_EVENTS_PER_DAY = 3  # 8h funding

DEFAULT_PARAMS = {
    "funding_path": "data/external/funding_rates.parquet",
    "trailing_days": 7,
    "entry_threshold_ann": 0.10,
    "exit_threshold_ann": 0.05,
    "max_slots": 3,
    "diagnostics_path": "data/strategies/carry_diagnostics.parquet",
    # mode: "proxy"   -> long-spot-only paper proxy (legacy; frozen by tests)
    #       "neutral" -> TRUE delta-neutral: long spot + short perp per pair.
    # The neutral mode is the industry-core market-neutral trade
    # (Nickel/Pythagoras/ANB): P&L ~= accrued funding - costs, not price
    # direction. It requires an ``allow_short: true`` book.
    "mode": "proxy",
    "gross_target": 0.9,  # neutral mode: total long-spot gross across pairs
}


def annualize_funding(mean_rate_per_interval: float) -> float:
    """rate * 3 * 365 (three 8h funding events per day, 365-day year)."""
    return float(mean_rate_per_interval) * FUNDING_EVENTS_PER_DAY * TRADING_DAYS_PER_YEAR


def daily_annualized_funding(funding_df: pd.DataFrame, trailing_days: int) -> pd.DataFrame:
    """Per (symbol, day): trailing ``trailing_days``-day mean funding, annualized.

    Returns a wide frame indexed by UTC day, one column per symbol.
    """
    df = funding_df.copy()
    df["funding_time_utc"] = pd.to_datetime(df["funding_time_utc"], utc=True)
    df["day"] = df["funding_time_utc"].dt.normalize()
    daily_mean = (
        df.groupby(["symbol", "day"])["funding_rate"].mean().unstack("symbol").sort_index()
    )
    trailing = daily_mean.rolling(window=trailing_days, min_periods=trailing_days).mean()
    return trailing * FUNDING_EVENTS_PER_DAY * TRADING_DAYS_PER_YEAR


def walk_active_slots(
    ann_funding: pd.DataFrame,
    entry: float,
    exit_: float,
    max_slots: int,
) -> pd.DataFrame:
    """Deterministic hysteresis walk over the daily annualized-funding panel.

    Returns a boolean frame (same shape) of slot membership per day.
    """
    active: List[str] = []
    out = pd.DataFrame(False, index=ann_funding.index, columns=ann_funding.columns)
    for day, row in ann_funding.iterrows():
        # exits first
        active = [s for s in active if pd.notna(row.get(s)) and row[s] >= exit_]
        # entries: qualifying non-members, best annualized funding first
        candidates = [
            s for s in ann_funding.columns
            if s not in active and pd.notna(row.get(s)) and row[s] > entry
        ]
        candidates.sort(key=lambda s: (-row[s], s))
        for sym in candidates:
            if len(active) >= max_slots:
                break
            active.append(sym)
        out.loc[day, active] = True
    return out


class CarrySleeve(Sleeve):
    name = "sleeve_carry"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def load_funding(self) -> Optional[pd.DataFrame]:
        """Normalize either the legacy funding frame or the OKX derivatives
        long format ({symbol, ts_utc, metric, value}) to
        {symbol, funding_time_utc, funding_rate}."""
        path = Path(self.params["funding_path"])
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if {"symbol", "funding_time_utc", "funding_rate"}.issubset(df.columns):
            return df
        if {"symbol", "ts_utc", "metric", "value"}.issubset(df.columns):
            df = df[df["metric"] == "funding_rate"].copy()
            if df.empty:
                return None
            return df.rename(columns={"ts_utc": "funding_time_utc", "value": "funding_rate"})[
                ["symbol", "funding_time_utc", "funding_rate"]
            ]
        return None

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        funding = self.load_funding()
        neutral = str(p.get("mode", "proxy")).lower() == "neutral"

        active: List[str] = []
        if funding is not None and len(funding):
            ann = daily_annualized_funding(funding, int(p["trailing_days"]))
            ann = ann.loc[ann.index <= signal_date]
            if len(ann):
                membership = walk_active_slots(
                    ann,
                    entry=float(p["entry_threshold_ann"]),
                    exit_=float(p["exit_threshold_ann"]),
                    max_slots=int(p["max_slots"]),
                )
                self._write_diagnostics(ann, membership)
                last = membership.iloc[-1]
                # only hold symbols the spot book can actually price
                available = set(market_df["symbol"].unique())
                active = [sym for sym in sorted(last.index[last]) if sym in available]

        if neutral:
            # TRUE delta-neutral: long spot + short perp of equal notional per
            # active pair. Net delta ~= 0; book P&L ~= funding - costs.
            g = float(p["gross_target"]) / float(p["max_slots"])
            legs = []
            for sym in active:
                legs.append({"symbol": sym, "weight": g, "instrument_type": "spot"})
                legs.append({"symbol": sym, "weight": -g, "instrument_type": "perp"})
            return build_signed_weights_frame(legs, signal_date, self.name)

        slot_weight = 1.0 / float(p["max_slots"])
        weights = {sym: slot_weight for sym in active}
        return build_weights_frame(weights, signal_date, self.name)

    def _write_diagnostics(self, ann: pd.DataFrame, membership: pd.DataFrame) -> None:
        """Virtual carry P&L: funding accrues only while a slot is active.

        ``virtual_daily_funding_yield`` = trailing-mean per-interval rate * 3
        (one day of funding) when active, else 0. This is the true carry-leg
        P&L the spot-only paper book cannot show.
        """
        raw_path = self.params.get("diagnostics_path")
        if not raw_path:  # disabled (e.g. allocator backtest replays)
            return
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        long_ann = ann.stack().rename("annualized_funding").reset_index()
        long_ann.columns = ["date_ts", "symbol", "annualized_funding"]
        long_mem = membership.stack().rename("slot_active").reset_index()
        long_mem.columns = ["date_ts", "symbol", "slot_active"]
        diag = long_ann.merge(long_mem, on=["date_ts", "symbol"], how="left")
        diag["slot_active"] = diag["slot_active"].fillna(False).astype(bool)
        daily_rate = diag["annualized_funding"] / TRADING_DAYS_PER_YEAR
        diag["virtual_daily_funding_yield"] = daily_rate.where(diag["slot_active"], 0.0)
        diag = diag.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        diag.to_parquet(path, index=False)


class CarryNeutralSleeve(CarrySleeve):
    """TRUE delta-neutral carry (long spot + short perp). Requires an
    ``allow_short: true`` book. This is the industry-core market-neutral trade
    (Nickel/Pythagoras/ANB); the base CarrySleeve keeps the legacy spot-only
    proxy for comparison."""

    name = "sleeve_carry_neutral"

    def __init__(self, params: dict | None = None) -> None:
        merged = {"mode": "neutral", **(params or {})}
        merged["mode"] = "neutral"  # force neutral regardless of overrides
        super().__init__(merged)
