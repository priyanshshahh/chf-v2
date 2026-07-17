"""Macro gross-exposure overlay (PROPOSAL input; never auto-executes).

Reads the daily regime panel (data/strategies/regime_daily.parquet) and, when
present, a FRED macro panel (data/external/macro_fred.parquet), and emits a
daily gross-exposure MULTIPLIER for the risk-on sleeves (trend / xsmom /
technical). Full gross in risk_on, progressively cut through neutral /
risk_off / stress.

FRED is currently empty in this environment, so the overlay degrades
gracefully: if no macro panel is available the multiplier is driven by regime
alone. When a FRED panel exists it can only *further reduce* gross (a stress
tilt), never raise it above the regime multiplier.

The allocator consumes ``latest_multiplier`` as an input to its proposal; it
does not change any book directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

DEFAULT_REGIME_MULTIPLIER = {
    "risk_on": 1.0,
    "neutral": 0.85,
    "risk_off": 0.60,
    "stress": 0.35,
}

DEFAULT_PARAMS = {
    "regime_path": "data/strategies/regime_daily.parquet",
    "macro_fred_path": "data/external/macro_fred.parquet",
    "regime_multiplier": DEFAULT_REGIME_MULTIPLIER,
    "output_parquet": "data/strategies/macro_overlay.parquet",
    "output_json": "data/strategies/macro_overlay_latest.json",
    "min_multiplier": 0.2,
}


def _load_macro_stress(path: str | Path) -> Optional[pd.Series]:
    """Optional FRED-derived stress factor in [0,1] (1 = calm). Absent -> None."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty or "date" not in df.columns:
        return None
    # Accept a pre-computed 'macro_calm' column; otherwise no usable signal.
    if "macro_calm" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["macro_calm"].sort_index()


def build_overlay(params: dict | None = None) -> pd.DataFrame:
    """Daily overlay table: [date_ts, regime, regime_multiplier, macro_calm,
    gross_multiplier]."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    regime_path = Path(p["regime_path"])
    if not regime_path.exists():
        raise FileNotFoundError(f"regime panel not found: {regime_path}")
    reg = pd.read_parquet(regime_path)
    if "date_ts" not in reg.columns or "regime" not in reg.columns:
        raise ValueError("regime panel needs date_ts + regime columns")
    reg = reg[["date_ts", "regime"]].copy()
    reg["date_ts"] = pd.to_datetime(reg["date_ts"], utc=True)
    table = {**DEFAULT_REGIME_MULTIPLIER, **(p.get("regime_multiplier") or {})}
    reg["regime_multiplier"] = reg["regime"].map(lambda r: float(table.get(r, 0.85)))

    calm = _load_macro_stress(p["macro_fred_path"])
    if calm is not None:
        reg["macro_calm"] = (
            calm.reindex(reg["date_ts"], method="ffill").clip(0.0, 1.0).values
        )
    else:
        reg["macro_calm"] = np.nan
    # FRED only tightens: multiplier = regime_mult * macro_calm (when present).
    macro_factor = reg["macro_calm"].fillna(1.0)
    reg["gross_multiplier"] = (reg["regime_multiplier"] * macro_factor).clip(
        lower=float(p["min_multiplier"]), upper=1.0
    )
    return reg.reset_index(drop=True)


def run_macro_overlay(as_of=None, params: dict | None = None) -> dict:
    """Build + persist the overlay; return the latest snapshot."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    table = build_overlay(p)
    if as_of is not None:
        cutoff = pd.Timestamp(as_of, tz="UTC").normalize()
        table = table[table["date_ts"] <= cutoff]
    out_parquet = Path(p["output_parquet"])
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out_parquet, index=False)

    last = table.iloc[-1]
    latest = {
        "date": str(pd.Timestamp(last["date_ts"]).date()),
        "regime": str(last["regime"]),
        "gross_multiplier": float(last["gross_multiplier"]),
        "has_macro_fred": bool(pd.notna(last.get("macro_calm"))),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    Path(p["output_json"]).write_text(json.dumps(latest, indent=2))
    return latest


def latest_multiplier(as_of=None, params: dict | None = None) -> float:
    """Convenience: the gross multiplier as of ``as_of`` (default: latest)."""
    try:
        snap = run_macro_overlay(as_of=as_of, params=params)
    except (FileNotFoundError, ValueError):
        return 1.0
    return float(snap["gross_multiplier"])
