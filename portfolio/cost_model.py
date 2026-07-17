"""Realistic, size/liquidity-aware transaction-cost model for Project CHF.

Replaces the flat 20 bps assumption with a size-aware cost:

    participation = trade_notional / ADV
    impact_bps    = impact_k * sqrt(participation)        # square-root model
    cost_bps      = fee_bps + spread_bps + impact_bps
    cost_bps      = min(cost_bps, max_cost_bps)           # sanity cap

where ADV is the asset's average daily *dollar* volume (close * volume) taken
from data/raw/market/market_ohlcv.parquet.

This module is STANDALONE by design: it exposes a clean API and CLI but is not
wired into papertrade/ or backtest/ (a later step integrates it). Deterministic,
no LLM. Cost parameters live in configs/costs.yaml (configs/risk.yaml is owned
by another agent and must not be touched).

CLI:
    .venv/bin/python -m portfolio.cost_model --demo
"""
from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chf.cost_model")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_RELPATH = "configs/costs.yaml"
DEFAULT_MARKET_RELPATH = "data/raw/market/market_ohlcv.parquet"

DEFAULT_COSTS: Dict[str, Any] = {
    "fee_bps": 10.0,
    "spread_bps": 5.0,
    "impact_k": 100.0,
    "adv_window_days": 30,
    "adv_floor_usd": 100000.0,
    "max_cost_bps": 500.0,
}


@dataclass(frozen=True)
class CostConfig:
    """Resolved cost parameters (bps units unless noted)."""

    fee_bps: float = 10.0
    spread_bps: float = 5.0
    impact_k: float = 100.0
    adv_window_days: int = 30
    adv_floor_usd: float = 100000.0
    max_cost_bps: float = 500.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CostConfig":
        merged = {**DEFAULT_COSTS, **(d or {})}
        return cls(
            fee_bps=float(merged["fee_bps"]),
            spread_bps=float(merged["spread_bps"]),
            impact_k=float(merged["impact_k"]),
            adv_window_days=int(merged["adv_window_days"]),
            adv_floor_usd=float(merged["adv_floor_usd"]),
            max_cost_bps=float(merged["max_cost_bps"]),
        )


def load_cost_config(root: Optional[Path] = None, config_path: Optional[str] = None) -> CostConfig:
    """Load configs/costs.yaml (``costs:`` block) merged over defaults."""
    root = root or ROOT
    path = Path(config_path) if config_path else root / DEFAULT_CONFIG_RELPATH
    if not path.is_absolute():
        path = root / path
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, "r") as f:
                loaded = yaml.safe_load(f) or {}
            data = loaded.get("costs", loaded) or {}
        except Exception as exc:  # noqa: BLE001 - bad config falls back to defaults
            logger.warning("cost_model: failed to read %s (%s); using defaults", path, exc)
    return CostConfig.from_dict(data)


# ---------------------------------------------------------------------------
# core math
# ---------------------------------------------------------------------------
def impact_bps(notional: float, adv: float, cfg: CostConfig) -> float:
    """Square-root market-impact in bps: impact_k * sqrt(trade/ADV).

    participation is floored at 0 (a zero-notional trade has zero impact) and
    ADV is floored at cfg.adv_floor_usd so thin/zero-volume names never divide
    by zero or produce infinite impact.
    """
    notional = abs(float(notional))
    if notional <= 0.0:
        return 0.0
    eff_adv = max(float(adv) if adv and adv > 0 else 0.0, cfg.adv_floor_usd)
    participation = notional / eff_adv
    return cfg.impact_k * math.sqrt(participation)


def estimate_cost(notional: float, adv: float, cfg: CostConfig) -> Dict[str, float]:
    """Estimate transaction cost for a trade.

    Returns a breakdown dict with fee_bps, spread_bps, impact_bps, cost_bps
    (capped) and cost_usd (= cost_bps/1e4 * |notional|).

    At zero participation (notional -> 0) impact is 0, so cost_bps collapses to
    fee_bps + spread_bps. Larger participation strictly increases impact_bps.
    """
    notional_abs = abs(float(notional))
    imp = impact_bps(notional_abs, adv, cfg)
    raw_bps = cfg.fee_bps + cfg.spread_bps + imp
    cost_bps = min(raw_bps, cfg.max_cost_bps)
    eff_adv = max(float(adv) if adv and adv > 0 else 0.0, cfg.adv_floor_usd)
    participation = (notional_abs / eff_adv) if notional_abs > 0 else 0.0
    return {
        "notional_usd": notional_abs,
        "adv_usd": float(adv) if adv else 0.0,
        "participation": participation,
        "fee_bps": cfg.fee_bps,
        "spread_bps": cfg.spread_bps,
        "impact_bps": imp,
        "raw_cost_bps": raw_bps,
        "cost_bps": cost_bps,
        "capped": raw_bps > cfg.max_cost_bps,
        "cost_usd": cost_bps / 1e4 * notional_abs,
    }


# ---------------------------------------------------------------------------
# ADV table
# ---------------------------------------------------------------------------
def build_adv_table(
    market: pd.DataFrame,
    window_days: int = 30,
    *,
    date_col: str = "date_ts",
    symbol_col: str = "symbol",
    close_col: str = "close",
    volume_col: str = "volume",
) -> pd.DataFrame:
    """Build a per-symbol ADV (average daily dollar volume) table.

    Dollar volume is close * volume per row; ADV is the mean over the most
    recent ``window_days`` observations per symbol. Returns a frame with
    columns [symbol, adv_usd, n_obs, last_date] sorted by descending ADV.
    """
    cols = {date_col, symbol_col, close_col, volume_col}
    missing = cols - set(market.columns)
    if missing:
        raise KeyError(f"market frame missing columns: {sorted(missing)}")

    df = market[[date_col, symbol_col, close_col, volume_col]].copy()
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
    df[volume_col] = pd.to_numeric(df[volume_col], errors="coerce")
    df["dollar_volume"] = df[close_col] * df[volume_col]
    df = df.dropna(subset=["dollar_volume"])
    df = df.sort_values([symbol_col, date_col])

    rows = []
    for sym, g in df.groupby(symbol_col, sort=False):
        tail = g.tail(int(window_days))
        if tail.empty:
            continue
        rows.append(
            {
                "symbol": sym,
                "adv_usd": float(tail["dollar_volume"].mean()),
                "n_obs": int(len(tail)),
                "last_date": tail[date_col].iloc[-1],
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("adv_usd", ascending=False).reset_index(drop=True)
    return out


def build_adv_table_from_file(
    market_path: Optional[Path] = None,
    window_days: int = 30,
    root: Optional[Path] = None,
) -> pd.DataFrame:
    """Load the market parquet and build the ADV table."""
    root = root or ROOT
    path = market_path or (root / DEFAULT_MARKET_RELPATH)
    if not Path(path).exists():
        raise FileNotFoundError(f"market file not found: {path}")
    market = pd.read_parquet(path, columns=["date_ts", "symbol", "close", "volume"])
    return build_adv_table(market, window_days=window_days)


def adv_lookup(adv_table: pd.DataFrame, symbol: str, default: Optional[float] = None) -> Optional[float]:
    """Look up a symbol's ADV from an ADV table (None/default if absent)."""
    if adv_table is None or adv_table.empty:
        return default
    hit = adv_table.loc[adv_table["symbol"] == symbol, "adv_usd"]
    if hit.empty:
        return default
    return float(hit.iloc[0])


# ---------------------------------------------------------------------------
# CLI / demo
# ---------------------------------------------------------------------------
def _demo(cfg: CostConfig, adv_usd: float = 5_000_000.0) -> int:
    print("CHF transaction-cost model demo")
    print("-" * 64)
    print(
        f"config: fee={cfg.fee_bps} bps  spread={cfg.spread_bps} bps  "
        f"impact_k={cfg.impact_k} bps  adv_floor=${cfg.adv_floor_usd:,.0f}  "
        f"cap={cfg.max_cost_bps} bps"
    )
    print(f"assumed ADV: ${adv_usd:,.0f}")
    print("-" * 64)
    print(f"{'notional_usd':>15} {'participation':>14} {'impact_bps':>11} {'cost_bps':>10} {'cost_usd':>12}")
    for notional in [0.0, 1_000.0, 10_000.0, 50_000.0, 250_000.0, 1_000_000.0, 5_000_000.0, 25_000_000.0]:
        b = estimate_cost(notional, adv_usd, cfg)
        print(
            f"{b['notional_usd']:>15,.0f} {b['participation']:>14.4f} "
            f"{b['impact_bps']:>11.2f} {b['cost_bps']:>10.2f} {b['cost_usd']:>12,.2f}"
        )
    print("-" * 64)
    print("Note: participation=0 -> cost == fee+spread; cost rises with sqrt(participation).")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="CHF transaction-cost model")
    parser.add_argument("--demo", action="store_true", help="print a cost curve and exit")
    parser.add_argument("--config", default=None, help="path to costs.yaml")
    parser.add_argument("--adv", type=float, default=5_000_000.0, help="assumed ADV (USD) for the demo")
    parser.add_argument("--notional", type=float, default=None, help="estimate cost for a single trade notional (USD)")
    args = parser.parse_args(argv)

    cfg = load_cost_config(config_path=args.config)

    if args.notional is not None:
        b = estimate_cost(args.notional, args.adv, cfg)
        for k, v in b.items():
            print(f"{k}: {v}")
        return 0

    # default action is the demo curve
    return _demo(cfg, adv_usd=args.adv)


if __name__ == "__main__":
    raise SystemExit(main())
