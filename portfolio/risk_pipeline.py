"""CHF risk pipeline: compose the risk layer over one paper book.

Order (per configs/risk.yaml):
  target weights -> hard limits (risk_limits) -> liquidity ADV caps
  -> vol targeting (risk-contribution cap + exposure multiplier)
  -> drawdown governance multiplier
Exposure multipliers combine via min(); the scaled remainder is cash.

Standalone artifacts only — NOT wired into papertrade/engine.py or any agent:
  data/risk/risk_adjusted_weights_<book>.parquet
  data/risk/risk_audit_<book>.json
  data/risk/drawdown_state_<book>.json (state machine persistence)

CLI:
    python -m portfolio.risk_pipeline --book ridge_30d_top5
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from portfolio import drawdown_control, liquidity, risk_limits, vol_target

DEFAULT_CONFIG_PATH = "configs/risk.yaml"


# -- inputs ------------------------------------------------------------------


def resolve_book_weights(book: str, run_config_path: str) -> Dict[str, float]:
    """Latest target weights for a papertrade book from run_config.yaml.

    Mirrors papertrade.engine.load_target_weights semantics (read-only; the
    engine itself is deliberately not imported or modified).
    """
    import yaml

    with open(run_config_path, "r", encoding="utf-8") as fh:
        run_cfg = yaml.safe_load(fh) or {}
    books = (run_cfg.get("papertrade") or {}).get("books") or []
    entry = next((b for b in books if b.get("name") == book), None)
    if entry is None:
        raise KeyError(f"book '{book}' not found in {run_config_path}")

    static = entry.get("static_weights")
    if static:
        return {str(s).upper(): float(w) for s, w in static.items() if float(w) > 0.0}

    path = Path(entry["weights_path"])
    if not path.exists():
        raise FileNotFoundError(f"weights_path not found for book '{book}': {path}")
    df = pd.read_parquet(path)
    for col in ("weight", "symbol"):
        if col not in df.columns:
            raise ValueError(f"weights parquet for '{book}' has no '{col}' column: {path}")
    date_col = "execution_date" if "execution_date" in df.columns else "date_ts"
    if date_col not in df.columns:
        raise ValueError(f"weights parquet for '{book}' has no execution_date/date_ts: {path}")
    latest = df[date_col].max()
    df = df[df[date_col] == latest]
    out: Dict[str, float] = {}
    for _, row in df.iterrows():
        w = float(row["weight"])
        if w > 0.0:
            out[str(row["symbol"]).upper()] = w
    return out


def load_nav_series(book: str, papertrade_dir: str = "data/papertrade") -> List[Tuple[str, float]]:
    eq = pd.read_parquet(Path(papertrade_dir) / book / "equity.parquet")
    if "date" not in eq.columns or "nav" not in eq.columns:
        raise ValueError(f"equity.parquet for '{book}' needs 'date' and 'nav' columns")
    eq = eq.sort_values("date")
    return [(str(d)[:10], float(n)) for d, n in zip(eq["date"], eq["nav"])]


def load_prices_wide(
    market_path: str, symbols: Sequence[str], as_of: Optional[str] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """(wide close-price frame for `symbols`, raw market slice) up to as_of."""
    market = pd.read_parquet(
        market_path, columns=["symbol", "date_ts", "close", "volume"]
    )
    market = market[market["symbol"].isin(set(str(s).upper() for s in symbols))]
    if as_of is not None:
        market = market[market["date_ts"].astype(str).str[:10] <= str(as_of)[:10]]
    prices = market.pivot_table(
        index="date_ts", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    return prices, market


# -- composition -------------------------------------------------------------


def apply_risk_pipeline(
    target_weights: Mapping[str, float],
    nav_series: Sequence[Tuple[str, float]],
    prices_wide: pd.DataFrame,
    market: pd.DataFrame,
    risk_cfg: Mapping,
    book: str = "adhoc",
    drawdown_state: Optional[Mapping] = None,
    as_of: Optional[str] = None,
) -> Dict[str, object]:
    """Run the full risk stack. Pure given its inputs (no I/O).

    Returns final weights, the persisted-ready drawdown state, and a full
    audit dict covering every stage and adjustment.
    """
    target = {str(s).upper(): float(w) for s, w in target_weights.items() if float(w) > 0.0}

    # 1. hard limits
    limits = risk_limits.limits_from_config(risk_cfg)
    categories = risk_limits.load_categories(
        (risk_cfg.get("limits") or {}).get("category_sources", [])
    )
    lim = risk_limits.apply_risk_limits(target, limits, categories)
    w_limits: Dict[str, float] = dict(lim["weights"])  # type: ignore[arg-type]

    # 2. liquidity ADV caps
    liq = liquidity.liquidity_report(
        w_limits, market, risk_cfg.get("liquidity", {}), as_of=as_of
    )
    w_liquid: Dict[str, float] = dict(liq["weights"])  # type: ignore[arg-type]

    # 3. vol targeting (risk-contribution cap + exposure multiplier)
    vt = vol_target.vol_target_report(
        w_liquid, prices_wide, risk_cfg.get("vol_target", {})
    )
    w_vol: Dict[str, float] = dict(vt["weights"])  # type: ignore[arg-type]
    m_vol = float(vt["multiplier"])  # type: ignore[arg-type]

    # 4. drawdown governance
    dd_cfg = risk_cfg.get("drawdown", {})
    dd_state, dd_transitions = drawdown_control.replay(
        drawdown_state, nav_series, dd_cfg
    )
    m_dd = drawdown_control.multiplier_for_state(str(dd_state["state"]), dd_cfg)

    # 5. combine multipliers via min; remainder is cash
    multiplier = min(m_vol, m_dd)
    final = {s: w * multiplier for s, w in sorted(w_vol.items())}
    gross = sum(final.values())

    audit: Dict[str, object] = {
        "book": book,
        "as_of": as_of,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_weights": {s: target[s] for s in sorted(target)},
        "stages": {
            "risk_limits": {
                "weights": {s: w_limits[s] for s in sorted(w_limits)},
                "adjustments": lim["adjustments"],
                "converged": lim["converged"],
                "iterations": lim["iterations"],
                "category_checks": lim["category_checks"],
                "category_data_unavailable": categories is None,
                "limits": limits,
            },
            "liquidity": {
                "weights": {s: w_liquid[s] for s in sorted(w_liquid)},
                "adjustments": liq["adjustments"],
                "adv_usd": liq["adv_usd"],
                "days_to_liquidate": liq["days_to_liquidate"],
            },
            "vol_target": {
                "weights": {s: w_vol[s] for s in sorted(w_vol)},
                "adjustments": vt["adjustments"],
                "multiplier": m_vol,
                "forecast_vol_annual": vt["forecast_vol_annual"],
                "risk_contributions": vt["risk_contributions"],
                "insufficient_history": vt["insufficient_history"],
                "observations": vt["observations"],
                "missing_price_symbols": vt["missing_price_symbols"],
            },
            "drawdown": {
                "state": dd_state["state"],
                "multiplier": m_dd,
                "peak_nav": dd_state["peak_nav"],
                "trough_nav": dd_state["trough_nav"],
                "new_transitions": dd_transitions,
            },
        },
        "multipliers": {"vol_target": m_vol, "drawdown": m_dd,
                        "combined_min": multiplier},
        "final_weights": final,
        "final_gross_exposure": gross,
        "final_cash_weight": max(0.0, 1.0 - gross),
    }
    return {"weights": final, "audit": audit, "drawdown_state": dd_state}


# -- artifacts ---------------------------------------------------------------


def run_book(
    book: str,
    config_path: str = DEFAULT_CONFIG_PATH,
    as_of: Optional[str] = None,
) -> Dict[str, object]:
    """Load a book's inputs, run the pipeline, write data/risk artifacts."""
    risk_cfg = risk_limits.load_risk_config(config_path)
    paths = risk_cfg.get("paths", {})
    run_config_path = paths.get("run_config", "configs/run_config.yaml")
    market_path = paths.get("market_data", "data/raw/market/market_ohlcv.parquet")
    output_dir = Path(paths.get("output_dir", "data/risk"))

    target = resolve_book_weights(book, run_config_path)
    nav_series = load_nav_series(book)
    if as_of is None:
        as_of = nav_series[-1][0] if nav_series else None
    prices_wide, market = load_prices_wide(market_path, list(target), as_of)

    prev_state = drawdown_control.load_state(book, risk_cfg.get("drawdown", {}))
    result = apply_risk_pipeline(
        target, nav_series, prices_wide, market, risk_cfg,
        book=book, drawdown_state=prev_state, as_of=as_of,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    weights_path = output_dir / f"risk_adjusted_weights_{book}.parquet"
    audit_path = output_dir / f"risk_audit_{book}.json"

    final: Dict[str, float] = result["weights"]  # type: ignore[assignment]
    rows = [{"book": book, "as_of": as_of, "symbol": s, "weight": w}
            for s, w in sorted(final.items())]
    rows.append({"book": book, "as_of": as_of, "symbol": "CASH",
                 "weight": max(0.0, 1.0 - sum(final.values()))})
    pd.DataFrame(rows).to_parquet(weights_path, index=False)

    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(result["audit"], fh, indent=2, sort_keys=True, default=float)

    drawdown_control.save_state(book, result["drawdown_state"],
                                risk_cfg.get("drawdown", {}))

    result["artifacts"] = {
        "weights_parquet": str(weights_path),
        "audit_json": str(audit_path),
        "drawdown_state_json": str(
            drawdown_control.state_path(book, risk_cfg.get("drawdown", {}))
        ),
    }
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CHF risk pipeline (standalone)")
    parser.add_argument("--book", required=True, help="papertrade book name")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD data cutoff")
    args = parser.parse_args(argv)

    result = run_book(args.book, args.config, args.as_of)
    print(json.dumps(
        {"book": args.book,
         "final_weights": result["weights"],
         "multipliers": result["audit"]["multipliers"],
         "drawdown_state": result["drawdown_state"]["state"],
         "artifacts": result["artifacts"]},
        indent=2, sort_keys=True, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
