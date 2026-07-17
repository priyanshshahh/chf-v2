"""Volatility targeting for CHF.

Pure functions, deterministic, no LLM:
  - EWMA covariance (zero-mean RiskMetrics style, halflife in days) from daily
    log returns.
  - Annualized portfolio vol forecast (sqrt(365) scaling by default).
  - Exposure multiplier = clip(target_vol / forecast_vol, min, max); the
    remainder of the book goes to cash (the "vol cut").
  - Inverse-vol per-asset sizing helper.
  - Risk-contribution capper: no asset may contribute more than a configured
    share of forecast portfolio variance (trim + renormalize).

CLI:
    python -m portfolio.vol_target --weights '{"BTC":0.5,"ETH":0.5}'
"""

from __future__ import annotations

import argparse
import json
from typing import Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULTS = {
    "target_vol_annual": 0.15,
    "ewma_halflife_days": 30.0,
    "annualization_days": 365,
    "min_exposure": 0.2,
    "max_exposure": 1.0,
    "min_observations": 20,
    "max_risk_contribution": 0.25,
    "risk_contribution_max_iterations": 50,
    "risk_contribution_tolerance": 1e-6,
}


# -- returns & covariance ----------------------------------------------------


def daily_log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily log returns from a wide close-price frame (date index, symbol cols)."""
    prices = prices.sort_index()
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.log(prices / prices.shift(1))
    return rets.iloc[1:].replace([np.inf, -np.inf], np.nan)


def ewma_covariance(returns: pd.DataFrame, halflife_days: float = 30.0) -> pd.DataFrame:
    """Zero-mean EWMA covariance of daily returns (RiskMetrics style).

    Weights w_t proportional to lambda^(T-1-t) with
    lambda = 0.5 ** (1 / halflife); normalized to sum to 1.
    Rows containing any NaN are dropped so the estimate stays PSD.
    """
    rets = returns.dropna(axis=0, how="any")
    if rets.empty:
        raise ValueError("no complete return observations for covariance")
    lam = 0.5 ** (1.0 / float(halflife_days))
    n = len(rets)
    wts = lam ** np.arange(n - 1, -1, -1, dtype=float)
    wts /= wts.sum()
    mat = rets.to_numpy(dtype=float)
    cov = (mat * wts[:, None]).T @ mat
    return pd.DataFrame(cov, index=rets.columns, columns=rets.columns)


def forecast_portfolio_vol(
    weights: Mapping[str, float],
    cov_daily: pd.DataFrame,
    annualization_days: int = 365,
) -> float:
    """Annualized portfolio vol forecast: sqrt(w' Sigma_daily w * ann_days)."""
    syms = [s for s in cov_daily.columns if float(weights.get(s, 0.0)) != 0.0]
    if not syms:
        return 0.0
    w = np.array([float(weights[s]) for s in syms], dtype=float)
    sigma = cov_daily.loc[syms, syms].to_numpy(dtype=float)
    var_daily = float(w @ sigma @ w)
    return float(np.sqrt(max(var_daily, 0.0) * float(annualization_days)))


# -- exposure multiplier -----------------------------------------------------


def exposure_multiplier(
    forecast_vol_annual: float,
    target_vol_annual: float = 0.15,
    min_exposure: float = 0.2,
    max_exposure: float = 1.0,
) -> float:
    """clip(target / forecast, min, max); max_exposure when forecast is ~0."""
    if forecast_vol_annual <= 0.0:
        return float(max_exposure)
    raw = float(target_vol_annual) / float(forecast_vol_annual)
    return float(np.clip(raw, float(min_exposure), float(max_exposure)))


# -- per-asset helpers -------------------------------------------------------


def inverse_vol_weights(
    vols: Mapping[str, float], gross: float = 1.0
) -> Dict[str, float]:
    """Weights proportional to 1/vol, normalized to `gross`. Non-positive or
    non-finite vols are excluded."""
    inv = {
        str(s).upper(): 1.0 / float(v)
        for s, v in vols.items()
        if np.isfinite(v) and float(v) > 0.0
    }
    total = sum(inv.values())
    if total <= 0.0:
        return {}
    return {s: gross * inv[s] / total for s in sorted(inv)}


def risk_contributions(
    weights: Mapping[str, float], cov_daily: pd.DataFrame
) -> Dict[str, float]:
    """Fractional risk contributions RC_i = w_i (Sigma w)_i / (w' Sigma w).

    Contributions w_i (Sigma w)_i sum to portfolio variance by identity, so
    the fractions sum to 1 whenever variance > 0.
    """
    syms = [s for s in cov_daily.columns if float(weights.get(s, 0.0)) != 0.0]
    if not syms:
        return {}
    w = np.array([float(weights[s]) for s in syms], dtype=float)
    sigma = cov_daily.loc[syms, syms].to_numpy(dtype=float)
    marginal = sigma @ w
    contrib = w * marginal
    var = float(contrib.sum())
    if var <= 0.0:
        return {s: 0.0 for s in syms}
    return {s: float(c / var) for s, c in zip(syms, contrib)}


def cap_risk_contributions(
    weights: Mapping[str, float],
    cov_daily: pd.DataFrame,
    max_contribution: float = 0.25,
    max_iterations: int = 50,
    tolerance: float = 1e-6,
) -> Tuple[Dict[str, float], list]:
    """Trim assets contributing more than `max_contribution` of forecast
    variance, renormalizing to preserve gross exposure; iterate to convergence.

    Returns (adjusted weights, adjustments list of
    {symbol, reason, before, after}).
    """
    w = {str(s).upper(): float(v) for s, v in weights.items() if float(v) > 0.0}
    adjustments: list = []
    if len(w) <= 1:
        return dict(w), adjustments  # single asset always contributes 100%
    # Fractional contributions sum to 1, so a cap below 1/n is infeasible;
    # floor at equal-risk-contribution.
    max_contribution = max(float(max_contribution), 1.0 / len(w))
    gross = sum(w.values())
    original = dict(w)
    for _ in range(int(max_iterations)):
        rc = risk_contributions(w, cov_daily)
        offenders = {s: c for s, c in rc.items() if c > max_contribution + tolerance}
        if not offenders:
            break
        for sym in sorted(offenders):
            # sqrt damping: RC is roughly quadratic in weight
            w[sym] *= float(np.sqrt(max_contribution / offenders[sym]))
        scale = gross / sum(w.values())
        for sym in sorted(w):
            w[sym] *= scale
    # audit the net effect per symbol (iteration micro-steps are not useful)
    for sym in sorted(w):
        if abs(w[sym] - original[sym]) > tolerance:
            adjustments.append(
                {"symbol": sym,
                 "reason": "max_risk_contribution",
                 "before": original[sym],
                 "after": w[sym]}
            )
    return {s: w[s] for s in sorted(w)}, adjustments


# -- composite ---------------------------------------------------------------


def vol_target_report(
    weights: Mapping[str, float],
    prices: pd.DataFrame,
    cfg: Optional[Mapping] = None,
) -> Dict[str, object]:
    """Full vol-targeting pass: EWMA cov -> RC cap -> exposure multiplier.

    `prices` is a wide daily close frame restricted to (at least) held symbols.
    Returns weights after RC capping, the multiplier, and diagnostics.
    """
    params = dict(DEFAULTS)
    if cfg:
        params.update({k: cfg[k] for k in DEFAULTS if k in cfg})

    held = sorted(s for s, v in weights.items() if float(v) > 0.0)
    cols = [s for s in held if s in prices.columns]
    missing = [s for s in held if s not in prices.columns]
    rets = daily_log_returns(prices[cols]) if cols else pd.DataFrame()
    complete = rets.dropna(axis=0, how="any")

    if len(complete) < int(params["min_observations"]) or not cols:
        return {
            "weights": {s: float(weights[s]) for s in held},
            "multiplier": 1.0,
            "forecast_vol_annual": None,
            "risk_contributions": {},
            "adjustments": [],
            "insufficient_history": True,
            "missing_price_symbols": missing,
            "observations": int(len(complete)),
        }

    cov = ewma_covariance(rets, float(params["ewma_halflife_days"]))
    capped, adjustments = cap_risk_contributions(
        {s: float(weights[s]) for s in cols},
        cov,
        float(params["max_risk_contribution"]),
        int(params["risk_contribution_max_iterations"]),
        float(params["risk_contribution_tolerance"]),
    )
    fvol = forecast_portfolio_vol(capped, cov, int(params["annualization_days"]))
    mult = exposure_multiplier(
        fvol,
        float(params["target_vol_annual"]),
        float(params["min_exposure"]),
        float(params["max_exposure"]),
    )
    return {
        "weights": capped,
        "multiplier": mult,
        "forecast_vol_annual": fvol,
        "risk_contributions": risk_contributions(capped, cov),
        "adjustments": adjustments,
        "insufficient_history": False,
        "missing_price_symbols": missing,
        "observations": int(len(complete)),
    }


# -- CLI ---------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    from portfolio.risk_limits import load_risk_config

    parser = argparse.ArgumentParser(description="CHF volatility targeting")
    parser.add_argument("--weights", required=True, help="JSON dict of weights")
    parser.add_argument("--config", default="configs/risk.yaml")
    parser.add_argument("--market", default=None,
                        help="override market parquet path")
    args = parser.parse_args(argv)

    risk_cfg = load_risk_config(args.config)
    market_path = args.market or risk_cfg["paths"]["market_data"]
    weights = {str(k).upper(): float(v) for k, v in json.loads(args.weights).items()}

    market = pd.read_parquet(market_path, columns=["symbol", "date_ts", "close"])
    market = market[market["symbol"].isin(weights)]
    prices = market.pivot_table(index="date_ts", columns="symbol",
                                values="close", aggfunc="last")
    report = vol_target_report(weights, prices, risk_cfg.get("vol_target", {}))
    print(json.dumps(report, indent=2, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
