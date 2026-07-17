"""Shared sleeve interface + helpers for the CHF multi-strategy layer.

Conventions
-----------
* Crypto trades 365 days/year: annualization uses sqrt(365) (vol) / 365 (mean).
* ``signal_date``  = last completed daily candle <= as_of in the market panel.
* ``execution_date`` = signal_date + 1 day (matches CHF execution_lag_days=1).
* Weight frames: [date_ts, execution_date, symbol, weight] (+ sleeve column),
  weights >= 0, sum <= 1.0 + eps. An all-cash decision is encoded as a single
  ``CASH`` row with weight 0.0 so the papertrade engine still sees a fresh
  execution_date and rebalances the book to cash.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 365
ANNUALIZATION_FACTOR = math.sqrt(float(TRADING_DAYS_PER_YEAR))  # sqrt(365)
WEIGHT_SUM_TOLERANCE = 1e-6
CASH_SYMBOL = "CASH"

WEIGHTS_SCHEMA = ["date_ts", "execution_date", "symbol", "weight"]


# ---------------------------------------------------------------------------
# market panel helpers
# ---------------------------------------------------------------------------


def load_market_data(path: str | Path) -> pd.DataFrame:
    """Load the CHF market OHLCV panel with only the columns sleeves need."""
    df = pd.read_parquet(path)
    keep = [c for c in ("date_ts", "symbol", "open", "high", "low", "close",
                        "volume", "dollar_volume_usd", "is_universe_member")
            if c in df.columns]
    df = df[keep].copy()
    df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True)
    return df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)


def to_utc(ts) -> pd.Timestamp:
    """Coerce a date-like value into a tz-aware UTC pandas Timestamp."""
    out = pd.Timestamp(ts)
    if out.tzinfo is None:
        out = out.tz_localize("UTC")
    return out.tz_convert("UTC").normalize()


def signal_date_for(market_df: pd.DataFrame, as_of) -> pd.Timestamp:
    """Last completed candle date <= as_of (raises if none)."""
    cutoff = to_utc(as_of)
    dates = market_df["date_ts"]
    eligible = dates[dates <= cutoff]
    if eligible.empty:
        raise ValueError(f"no market data on or before {cutoff.date()}")
    return eligible.max()


def close_panel(market_df: pd.DataFrame) -> pd.DataFrame:
    """Wide close-price panel: index date_ts, columns symbol."""
    return market_df.pivot_table(
        index="date_ts", columns="symbol", values="close", aggfunc="last"
    ).sort_index()


def dollar_volume_panel(market_df: pd.DataFrame) -> pd.DataFrame:
    """Wide dollar-volume panel; falls back to close*volume when absent."""
    df = market_df.copy()
    if "dollar_volume_usd" in df.columns:
        dv = df["dollar_volume_usd"]
        fallback = df["close"] * df["volume"]
        df["_dv"] = dv.where(dv.notna() & (dv > 0), fallback)
    else:
        df["_dv"] = df["close"] * df["volume"]
    return df.pivot_table(
        index="date_ts", columns="symbol", values="_dv", aggfunc="last"
    ).sort_index()


def top_liquid_symbols(
    market_df: pd.DataFrame,
    signal_date: pd.Timestamp,
    n: int,
    adv_window: int = 90,
    min_adv_usd: float = 0.0,
    exclude: Sequence[str] = (),
    min_history_days: int = 0,
) -> List[str]:
    """Top-N symbols by trailing ``adv_window``-day *median* dollar volume.

    Deterministic: ties broken alphabetically. Symbols must have a close on
    the signal date and (optionally) ``min_history_days`` of prior closes.
    """
    dv = dollar_volume_panel(market_df)
    closes = close_panel(market_df)
    dv = dv.loc[dv.index <= signal_date]
    closes = closes.loc[closes.index <= signal_date]
    if dv.empty or closes.empty:
        return []
    window = dv.tail(adv_window)
    med = window.median(axis=0, skipna=True)
    alive = closes.iloc[-1].notna()
    history_ok = closes.notna().sum(axis=0) >= max(min_history_days, 1)
    med = med[alive & history_ok & (med > float(min_adv_usd))]
    med = med.drop(labels=[s for s in exclude if s in med.index])
    ranked = sorted(med.items(), key=lambda kv: (-kv[1], kv[0]))
    return [sym for sym, _ in ranked[:n]]


def trailing_return(closes: pd.Series, window: int) -> Optional[float]:
    """Simple return over the trailing ``window`` days (needs window+1 obs)."""
    s = closes.dropna()
    if len(s) < window + 1:
        return None
    past = float(s.iloc[-(window + 1)])
    last = float(s.iloc[-1])
    if past <= 0:
        return None
    return last / past - 1.0


def realized_vol(closes: pd.Series, window: int, annualize: bool = True) -> Optional[float]:
    """Std of daily simple returns over trailing window, sqrt(365)-annualized."""
    s = closes.dropna()
    if len(s) < window + 1:
        return None
    rets = s.pct_change().dropna().tail(window)
    if len(rets) < 2:
        return None
    vol = float(rets.std(ddof=1))
    if not np.isfinite(vol):
        return None
    return vol * ANNUALIZATION_FACTOR if annualize else vol


def inverse_vol_weights(
    vols: Dict[str, float], gross: float = 1.0, min_vol: float = 1e-6
) -> Dict[str, float]:
    """Weights proportional to 1/vol, normalized to sum to ``gross``."""
    inv = {s: 1.0 / max(float(v), min_vol) for s, v in vols.items()
           if v is not None and np.isfinite(v)}
    total = sum(inv.values())
    if total <= 0:
        return {}
    return {s: gross * w / total for s, w in sorted(inv.items())}


# ---------------------------------------------------------------------------
# weight-frame construction / validation
# ---------------------------------------------------------------------------


def build_weights_frame(
    weights: Dict[str, float],
    signal_date: pd.Timestamp,
    sleeve: str,
    execution_lag_days: int = 1,
) -> pd.DataFrame:
    """Engine-compatible weight frame for one rebalance date.

    Empty ``weights`` (all-cash) emits one CASH row at weight 0.0 so the
    engine still observes a fresh execution_date and flattens the book.
    """
    signal_date = to_utc(signal_date)
    execution_date = signal_date + pd.Timedelta(days=int(execution_lag_days))
    rows = [
        {"date_ts": signal_date, "execution_date": execution_date,
         "symbol": str(sym).upper(), "weight": float(w), "sleeve": sleeve}
        for sym, w in sorted(weights.items())
        if float(w) > 0.0
    ]
    if not rows:
        rows = [{"date_ts": signal_date, "execution_date": execution_date,
                 "symbol": CASH_SYMBOL, "weight": 0.0, "sleeve": sleeve}]
    df = pd.DataFrame(rows, columns=WEIGHTS_SCHEMA + ["sleeve"])
    validate_weights_frame(df)
    return df


SIGNED_WEIGHTS_SCHEMA = ["date_ts", "execution_date", "symbol", "weight", "instrument_type"]
VALID_INSTRUMENTS = ("spot", "perp")


def build_signed_weights_frame(
    legs: Sequence[dict],
    signal_date: pd.Timestamp,
    sleeve: str,
    execution_lag_days: int = 1,
    max_gross: float = 3.0,
) -> pd.DataFrame:
    """Signed multi-instrument weight frame (short-capable sleeves).

    ``legs`` is a sequence of ``{"symbol", "weight" (signed), "instrument_type"}``.
    An empty leg set (all-cash) emits one CASH/spot row at weight 0.0 so the
    engine still observes a fresh execution_date and flattens the book.
    """
    signal_date = to_utc(signal_date)
    execution_date = signal_date + pd.Timedelta(days=int(execution_lag_days))
    rows = []
    for leg in legs:
        weight = float(leg["weight"])
        if weight == 0.0:
            continue
        itype = str(leg.get("instrument_type", "spot")).lower()
        rows.append({
            "date_ts": signal_date, "execution_date": execution_date,
            "symbol": str(leg["symbol"]).upper(), "weight": weight,
            "instrument_type": itype, "sleeve": sleeve,
        })
    if not rows:
        rows = [{"date_ts": signal_date, "execution_date": execution_date,
                 "symbol": CASH_SYMBOL, "weight": 0.0, "instrument_type": "spot",
                 "sleeve": sleeve}]
    df = pd.DataFrame(rows, columns=SIGNED_WEIGHTS_SCHEMA + ["sleeve"])
    validate_signed_weights_frame(df, max_gross=max_gross)
    return df


def validate_signed_weights_frame(df: pd.DataFrame, max_gross: float = 3.0) -> None:
    """Assert engine compatibility for signed frames: schema, valid instruments,
    per-date gross (sum |weight|) within ``max_gross``."""
    missing = [c for c in SIGNED_WEIGHTS_SCHEMA if c not in df.columns]
    if missing:
        raise ValueError(f"signed weights frame missing columns: {missing}")
    if df.empty:
        raise ValueError("signed weights frame is empty")
    bad_it = set(df["instrument_type"].str.lower()) - set(VALID_INSTRUMENTS)
    if bad_it:
        raise ValueError(f"invalid instrument_type(s): {sorted(bad_it)}")
    gross = df.assign(_g=df["weight"].abs()).groupby("execution_date")["_g"].sum()
    over = gross[gross > float(max_gross) + WEIGHT_SUM_TOLERANCE]
    if len(over):
        raise ValueError(f"gross exposure > {max_gross} for execution_date(s): {over.to_dict()}")


def validate_weights_frame(df: pd.DataFrame) -> None:
    """Assert engine compatibility: schema, weights >= 0, per-date sum <= 1."""
    missing = [c for c in WEIGHTS_SCHEMA if c not in df.columns]
    if missing:
        raise ValueError(f"weights frame missing columns: {missing}")
    if df.empty:
        raise ValueError("weights frame is empty")
    if (df["weight"] < 0).any():
        raise ValueError("weights frame contains negative weights")
    sums = df.groupby("execution_date")["weight"].sum()
    bad = sums[sums > 1.0 + WEIGHT_SUM_TOLERANCE]
    if len(bad):
        raise ValueError(f"weights sum > 1.0 for execution_date(s): {bad.to_dict()}")


def upsert_weights_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    """Idempotently append/refresh a rebalance date in a weights parquet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    validate_weights_frame(df)
    if path.exists():
        existing = pd.read_parquet(path)
        new_dates = set(pd.to_datetime(df["execution_date"], utc=True))
        existing["execution_date"] = pd.to_datetime(existing["execution_date"], utc=True)
        existing = existing[~existing["execution_date"].isin(new_dates)]
        df = pd.concat([existing, df], ignore_index=True)
        df = df.sort_values(["execution_date", "symbol"]).reset_index(drop=True)
    df.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# sleeve interface
# ---------------------------------------------------------------------------


class Sleeve(ABC):
    """A deterministic signal -> target-weights generator."""

    name: str = "sleeve"

    def __init__(self, params: Optional[dict] = None) -> None:
        self.params = dict(params or {})

    @abstractmethod
    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        """Target weights for the rebalance as of ``as_of`` (weights frame)."""

    def describe_holdings(self, weights_df: pd.DataFrame) -> str:
        held = weights_df[weights_df["weight"] > 0]
        if held.empty:
            return f"{self.name}: 100% cash"
        parts = [f"{r.symbol}={r.weight:.3f}" for r in held.itertuples()]
        cash = 1.0 - float(held["weight"].sum())
        return f"{self.name}: " + ", ".join(parts) + f" (cash={cash:.3f})"
