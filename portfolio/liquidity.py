"""Liquidity constraints for CHF.

Pure functions, deterministic, no LLM:
  - 30-day dollar ADV per symbol from market OHLCV (close * volume).
  - Position caps: per-rebalance-day traded notional <= k * ADV
    (default 5% of ADV for a configurable NAV, default $100k paper NAV).
  - Days-to-liquidate report for 25/50/100% of the book at 25% ADV
    participation, normal and stressed (volume / 3).

CLI:
    python -m portfolio.liquidity --weights '{"BTC":0.5,"ETH":0.5}'
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

DEFAULTS = {
    "adv_window_days": 30,
    "max_adv_participation": 0.05,
    "nav_usd": 100_000.0,
    "dtl_participation": 0.25,
    "dtl_book_fractions": (0.25, 0.5, 1.0),
    "stressed_volume_divisor": 3.0,
}


def compute_adv(
    market: pd.DataFrame,
    window_days: int = 30,
    as_of: Optional[str] = None,
    symbols: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """Dollar ADV per symbol: mean(close * volume) over each symbol's last
    `window_days` observations at or before `as_of` (date string)."""
    df = market[["symbol", "date_ts", "close", "volume"]].copy()
    if symbols is not None:
        df = df[df["symbol"].isin(set(str(s).upper() for s in symbols))]
    if as_of is not None:
        df = df[df["date_ts"].astype(str).str[:10] <= str(as_of)[:10]]
    df = df.dropna(subset=["close", "volume"]).sort_values(["symbol", "date_ts"])
    df["dollar_volume"] = df["close"].astype(float) * df["volume"].astype(float)
    tail = df.groupby("symbol", sort=True).tail(int(window_days))
    adv = tail.groupby("symbol", sort=True)["dollar_volume"].mean()
    return {str(s): float(v) for s, v in adv.items()}


def cap_weights_by_adv(
    weights: Mapping[str, float],
    adv_usd: Mapping[str, float],
    nav_usd: float = 100_000.0,
    max_adv_participation: float = 0.05,
) -> Tuple[Dict[str, float], List[dict]]:
    """Cap each position's notional at participation * ADV.

    Freed weight goes to cash (no redistribution: a liquidity cap must not
    push exposure into other names). Symbols with no ADV data are capped to 0
    and recorded with reason 'no_adv_data'.
    Returns (adjusted weights, adjustments).
    """
    adjustments: List[dict] = []
    out: Dict[str, float] = {}
    for sym in sorted(weights):
        w = float(weights[sym])
        if w <= 0.0:
            continue
        adv = adv_usd.get(sym)
        if adv is None or not math.isfinite(adv) or adv <= 0.0:
            adjustments.append(
                {"symbol": sym, "reason": "no_adv_data", "before": w, "after": 0.0}
            )
            continue
        max_w = (float(max_adv_participation) * float(adv)) / float(nav_usd)
        if w > max_w:
            adjustments.append(
                {"symbol": sym, "reason": "adv_participation_cap",
                 "before": w, "after": max_w}
            )
            out[sym] = max_w
        else:
            out[sym] = w
    return out, adjustments


def days_to_liquidate(
    weights: Mapping[str, float],
    adv_usd: Mapping[str, float],
    nav_usd: float = 100_000.0,
    participation: float = 0.25,
    book_fractions: Sequence[float] = (0.25, 0.5, 1.0),
    stressed_volume_divisor: float = 3.0,
) -> Dict[str, object]:
    """Days to liquidate fractions of the book at `participation` of ADV.

    Per symbol: days = (fraction * weight * nav) / (participation * ADV);
    the book number is the max over positions (slowest position gates the
    unwind). Stressed scenario divides ADV by `stressed_volume_divisor`.
    Symbols without ADV data report infinity (JSON: null) and are listed.
    """
    scenarios: Dict[str, object] = {}
    missing = sorted(
        s for s, w in weights.items()
        if float(w) > 0.0 and not (adv_usd.get(s) and adv_usd[s] > 0.0)
    )
    for label, divisor in (("normal", 1.0), ("stressed", float(stressed_volume_divisor))):
        frac_reports: Dict[str, object] = {}
        for frac in book_fractions:
            per_symbol: Dict[str, Optional[float]] = {}
            for sym in sorted(weights):
                w = float(weights[sym])
                if w <= 0.0:
                    continue
                adv = adv_usd.get(sym)
                if adv is None or adv <= 0.0:
                    per_symbol[sym] = None  # not liquidatable on volume data
                    continue
                daily_capacity = float(participation) * float(adv) / divisor
                per_symbol[sym] = (float(frac) * w * float(nav_usd)) / daily_capacity
            vals = [v for v in per_symbol.values() if v is not None]
            frac_reports[f"{float(frac):g}"] = {
                "per_symbol_days": per_symbol,
                "book_days": (max(vals) if vals else 0.0) if not missing else None,
                "book_days_ex_missing": max(vals) if vals else 0.0,
            }
        scenarios[label] = frac_reports
    return {
        "nav_usd": float(nav_usd),
        "participation": float(participation),
        "stressed_volume_divisor": float(stressed_volume_divisor),
        "missing_adv_symbols": missing,
        "scenarios": scenarios,
    }


def liquidity_report(
    weights: Mapping[str, float],
    market: pd.DataFrame,
    cfg: Optional[Mapping] = None,
    as_of: Optional[str] = None,
) -> Dict[str, object]:
    """ADV computation + position caps + days-to-liquidate in one pass."""
    params = dict(DEFAULTS)
    if cfg:
        params.update({k: cfg[k] for k in DEFAULTS if k in cfg})
    weights = {str(s).upper(): float(v) for s, v in weights.items() if float(v) > 0.0}
    adv = compute_adv(market, int(params["adv_window_days"]), as_of, list(weights))
    capped, adjustments = cap_weights_by_adv(
        weights, adv, float(params["nav_usd"]), float(params["max_adv_participation"])
    )
    dtl = days_to_liquidate(
        capped, adv, float(params["nav_usd"]), float(params["dtl_participation"]),
        list(params["dtl_book_fractions"]), float(params["stressed_volume_divisor"])
    )
    return {
        "weights": capped,
        "adjustments": adjustments,
        "adv_usd": {s: adv.get(s) for s in sorted(weights)},
        "days_to_liquidate": dtl,
    }


# -- CLI ---------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    from portfolio.risk_limits import load_risk_config

    parser = argparse.ArgumentParser(description="CHF liquidity constraints")
    parser.add_argument("--weights", required=True, help="JSON dict of weights")
    parser.add_argument("--config", default="configs/risk.yaml")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD ADV cutoff")
    args = parser.parse_args(argv)

    risk_cfg = load_risk_config(args.config)
    market = pd.read_parquet(
        risk_cfg["paths"]["market_data"],
        columns=["symbol", "date_ts", "close", "volume"],
    )
    report = liquidity_report(
        json.loads(args.weights), market, risk_cfg.get("liquidity", {}), args.as_of
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
