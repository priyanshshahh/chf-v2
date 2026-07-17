"""Pod-shop capital allocator across CHF sleeves (PROPOSAL artifact only).

Monthly process:
    1. For each sleeve, build a trailing-``lookback_days`` daily return series
       from its paper book equity (data/papertrade/<book>/equity.parquet).
       If the book has fewer than ``min_equity_days`` observations, fall back
       to a deterministic weekly-rebalance backtest of the sleeve's own
       ``generate()`` over the same window (20 bps cost on turnover).
    2. Compute the trailing Sortino ratio (365-day annualization).
    3. Allocate capital proportional to max(Sortino, sortino_floor), then
       project onto [weight_floor, weight_cap] per sleeve (5% / 40%).
    4. Apply the regime multiplier for the CURRENT regime
       (configs/sleeves.yaml regime_multipliers); the multiplied-away mass
       stays in cash (no re-normalization after multipliers).
    5. Write data/strategies/sleeve_allocation.parquet + a JSON PROPOSAL.

NEVER auto-executes: the output is a proposal for the orchestration approval
gate — humans own capital allocation, consistent with fund practice.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .base import TRADING_DAYS_PER_YEAR, Sleeve, close_panel, signal_date_for, to_utc

SORTINO_CAP = 10.0


# ---------------------------------------------------------------------------
# return series
# ---------------------------------------------------------------------------


def paper_book_returns(
    papertrade_dir: str | Path, book_name: str, lookback_days: int, as_of: pd.Timestamp
) -> Optional[pd.Series]:
    """Daily returns from a paper book's equity parquet, if enough history."""
    path = Path(papertrade_dir) / book_name / "equity.parquet"
    if not path.exists():
        return None
    eq = pd.read_parquet(path)
    if "date" not in eq.columns or "daily_return" not in eq.columns or eq.empty:
        return None
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"], utc=True)
    eq = eq[eq["date"] <= as_of].sort_values("date")
    rets = eq.set_index("date")["daily_return"].tail(lookback_days)
    return rets if len(rets) else None


def backtest_sleeve_returns(
    sleeve: Sleeve,
    market_df: pd.DataFrame,
    as_of: pd.Timestamp,
    lookback_days: int,
    rebalance_days: int = 7,
    cost_bps: float = 20.0,
) -> Optional[pd.Series]:
    """Deterministic weekly-rebalance replay of a sleeve over the lookback."""
    closes = close_panel(market_df)
    closes = closes.loc[closes.index <= as_of]
    if closes.empty:
        return None
    dates = closes.index[-(lookback_days + 1):]
    if len(dates) < rebalance_days + 2:
        return None
    asset_rets = closes.pct_change(fill_method=None)

    weights: Dict[str, float] = {}
    daily: List[Tuple[pd.Timestamp, float]] = []
    for i, day in enumerate(dates):
        cost = 0.0
        if i % rebalance_days == 0:
            try:
                wdf = sleeve.generate(market_df, day)
            except Exception:
                wdf = None
            new_weights: Dict[str, float] = {}
            if wdf is not None:
                held = wdf[wdf["weight"] > 0]
                new_weights = {
                    str(r.symbol): float(r.weight) for r in held.itertuples()
                }
            turnover = sum(
                abs(new_weights.get(s, 0.0) - weights.get(s, 0.0))
                for s in set(new_weights) | set(weights)
            )
            cost = turnover * cost_bps / 10_000.0
            weights = new_weights
        if i == 0:
            continue
        ret = 0.0
        for sym, w in weights.items():
            r = asset_rets.at[day, sym] if sym in asset_rets.columns else np.nan
            if pd.notna(r):
                ret += w * float(r)
        daily.append((day, ret - cost))
    if not daily:
        return None
    idx, vals = zip(*daily)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


# ---------------------------------------------------------------------------
# scoring + weighting math
# ---------------------------------------------------------------------------


def sortino_ratio(returns: pd.Series) -> float:
    """Annualized Sortino: mean*365 / downside_deviation*sqrt(365), capped."""
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return 0.0
    mean_ann = float(r.mean()) * TRADING_DAYS_PER_YEAR
    downside = np.minimum(r.to_numpy(), 0.0)
    dd = math.sqrt(float(np.mean(downside**2))) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if dd <= 1e-12:
        return SORTINO_CAP if mean_ann > 0 else 0.0
    return float(np.clip(mean_ann / dd, -SORTINO_CAP, SORTINO_CAP))


def allocate_proportional(
    scores: Dict[str, float],
    sortino_floor: float = 0.1,
    weight_floor: float = 0.05,
    weight_cap: float = 0.40,
) -> Dict[str, float]:
    """Capital ∝ max(score, sortino_floor), projected onto [floor, cap].

    Iterative projection: proportionally fill the free mass, clip violators to
    their bound and fix them, repeat. Deterministic; requires
    n*floor <= 1 <= n*cap to be feasible.
    """
    names = sorted(scores)
    n = len(names)
    if n == 0:
        return {}
    if n * weight_floor > 1.0 + 1e-9 or n * weight_cap < 1.0 - 1e-9:
        raise ValueError("infeasible floor/cap for number of sleeves")
    raw = {k: max(float(scores[k]), sortino_floor) for k in names}
    fixed: Dict[str, float] = {}
    free = list(names)
    for _ in range(2 * n + 1):
        mass = 1.0 - sum(fixed.values())
        total_raw = sum(raw[k] for k in free)
        if not free or total_raw <= 0:
            break
        tentative = {k: mass * raw[k] / total_raw for k in free}
        # Fix CAP violators first: capping frees mass, which can lift the
        # small sleeves back above the floor (fixing both sides at once and
        # rescaling would break the bounds).
        cap_violators = [k for k, v in tentative.items() if v > weight_cap + 1e-12]
        if cap_violators:
            for k in cap_violators:
                fixed[k] = weight_cap
                free.remove(k)
            continue
        floor_violators = [k for k, v in tentative.items() if v < weight_floor - 1e-12]
        if floor_violators:
            for k in floor_violators:
                fixed[k] = weight_floor
                free.remove(k)
            continue
        fixed.update(tentative)
        free = []
        break
    result = {k: fixed.get(k, weight_floor) for k in names}
    total = sum(result.values())
    if abs(total - 1.0) > 1e-6:  # numeric safety only; keep within bounds
        scale = 1.0 / total
        result = {
            k: float(np.clip(v * scale, weight_floor, weight_cap))
            for k, v in result.items()
        }
    return result


def apply_regime_multipliers(
    weights: Dict[str, float], regime: str, multipliers: Dict[str, Dict[str, float]]
) -> Dict[str, float]:
    """Scale sleeve weights by the current regime's multiplier (rest -> cash)."""
    table = multipliers.get(regime, {})
    return {k: float(w) * float(table.get(k, 1.0)) for k, w in weights.items()}


# ---------------------------------------------------------------------------
# main entrypoint
# ---------------------------------------------------------------------------


RISK_ON_SLEEVES = ("sleeve_trend", "sleeve_xsmom", "sleeve_technical")


def run_allocator(
    sleeves: Dict[str, Sleeve],
    market_df: pd.DataFrame,
    sleeves_cfg: dict,
    as_of=None,
    regime_latest: Optional[dict] = None,
    macro_multiplier: Optional[float] = None,
) -> dict:
    """Compute the monthly sleeve-capital PROPOSAL and persist artifacts.

    ``macro_multiplier`` (from strategies.macro_overlay) is an optional
    gross-exposure input applied to the risk-on sleeves only (trend / xsmom /
    technical); the multiplied-away mass stays in cash. ``None`` leaves the
    proposal unchanged (backward compatible).
    """
    acfg = sleeves_cfg.get("allocator", {})
    lookback = int(acfg.get("lookback_days", 90))
    min_days = int(acfg.get("min_equity_days", 30))
    papertrade_dir = acfg.get("papertrade_dir", "data/papertrade")
    signal_date = signal_date_for(market_df, as_of or pd.Timestamp.now(tz="UTC"))

    sortinos: Dict[str, float] = {}
    sources: Dict[str, str] = {}
    for name, sleeve in sleeves.items():
        rets = paper_book_returns(papertrade_dir, name, lookback, signal_date)
        if rets is not None and len(rets) >= min_days:
            sources[name] = "paper_book"
        else:
            rets = backtest_sleeve_returns(
                sleeve,
                market_df,
                signal_date,
                lookback,
                rebalance_days=int(acfg.get("backtest_rebalance_days", 7)),
                cost_bps=float(acfg.get("backtest_cost_bps", 20)),
            )
            sources[name] = "backtest_fallback"
        sortinos[name] = sortino_ratio(rets) if rets is not None else 0.0

    base = allocate_proportional(
        sortinos,
        sortino_floor=float(acfg.get("sortino_floor", 0.1)),
        weight_floor=float(acfg.get("weight_floor", 0.05)),
        weight_cap=float(acfg.get("weight_cap", 0.40)),
    )

    regime = str((regime_latest or {}).get("regime", "neutral"))
    multipliers = sleeves_cfg.get("regime_multipliers", {})
    final = apply_regime_multipliers(base, regime, multipliers)
    if macro_multiplier is not None:
        m = float(macro_multiplier)
        final = {
            name: (w * m if name in RISK_ON_SLEEVES else w)
            for name, w in final.items()
        }
    cash = max(0.0, 1.0 - sum(final.values()))

    rows = []
    for name in sorted(sleeves):
        rows.append(
            {
                "date_ts": signal_date,
                "sleeve": name,
                "sortino_90d": sortinos[name],
                "return_source": sources[name],
                "base_weight": base[name],
                "regime": regime,
                "regime_multiplier": float(
                    multipliers.get(regime, {}).get(name, 1.0)
                ),
                "proposed_weight": final[name],
            }
        )
    alloc_df = pd.DataFrame(rows)

    out_parquet = Path(acfg.get("output_parquet", "data/strategies/sleeve_allocation.parquet"))
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    if out_parquet.exists():
        existing = pd.read_parquet(out_parquet)
        existing["date_ts"] = pd.to_datetime(existing["date_ts"], utc=True)
        existing = existing[existing["date_ts"] != signal_date]
        alloc_df = pd.concat([existing, alloc_df], ignore_index=True)
        alloc_df = alloc_df.sort_values(["date_ts", "sleeve"]).reset_index(drop=True)
    alloc_df.to_parquet(out_parquet, index=False)

    proposal = {
        "artifact_type": "PROPOSAL",
        "approved": False,
        "note": (
            "Sleeve capital allocation proposal. Never auto-executed: promotion "
            "goes through the orchestration approval gate (humans own capital "
            "allocation)."
        ),
        "as_of": str(signal_date.date()),
        "regime": regime,
        "macro_multiplier": (float(macro_multiplier) if macro_multiplier is not None else None),
        "cash_weight": cash,
        "sleeves": {
            name: {
                "sortino_90d": sortinos[name],
                "return_source": sources[name],
                "base_weight": base[name],
                "proposed_weight": final[name],
            }
            for name in sorted(sleeves)
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out_json = Path(acfg.get("output_json", "data/strategies/sleeve_allocation_proposal.json"))
    out_json.write_text(json.dumps(proposal, indent=2))
    return proposal
