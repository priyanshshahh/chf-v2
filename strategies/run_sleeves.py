"""CLI: generate all sleeves' current target weights + papertrade books.

Steps:
    1. Load the market panel and configs/sleeves.yaml.
    2. Run the daily regime classifier (regime_daily.parquet + latest JSON).
    3. Generate each sleeve's current target weights into
       data/strategies/<sleeve>_weights.parquet (idempotent per date).
    4. Run the capital allocator (PROPOSAL artifact only).
    5. Append/refresh the papertrade book entries for the four sleeves in
       configs/run_config.yaml (textual append at the end of papertrade.books
       so existing entries and comments stay untouched).

Usage:
    .venv/bin/python -m strategies.run_sleeves [--as-of YYYY-MM-DD]
        [--config configs/run_config.yaml] [--sleeves-config configs/sleeves.yaml]
        [--no-allocator] [--no-books]

After this, `.venv/bin/python main.py papertrade --books sleeve_trend,...`
opens/marks the new books.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

import pandas as pd
import yaml

from .allocator import run_allocator
from .base import Sleeve, load_market_data, upsert_weights_parquet
from .carry_sleeve import CarryNeutralSleeve, CarrySleeve
from .defi_yield_sleeve import DefiYieldSleeve
from .macro_overlay import run_macro_overlay
from .regime import run_regime
from .statarb_sleeve import StatArbSleeve
from .technical_ensemble import TechnicalEnsembleSleeve
from .trend_sleeve import TrendSleeve
from .xsmom_sleeve import XSMomSleeve

DEFAULT_RUN_CONFIG = "configs/run_config.yaml"
DEFAULT_SLEEVES_CONFIG = "configs/sleeves.yaml"

BOOK_TEMPLATE = """    - name: "{name}"
      weights_path: "{weights_path}"
      rebalance: "{rebalance}"
      cost_bps: {cost_bps}
      starting_cash: {starting_cash}
"""


def build_sleeves(sleeves_cfg: dict) -> Dict[str, Sleeve]:
    params = sleeves_cfg.get("sleeves", {})
    paths = sleeves_cfg.get("paths", {})
    xs_params = dict(params.get("sleeve_xsmom", {}))
    if "altcoin_season" in paths:
        xs_params.setdefault("altseason_path", paths["altcoin_season"])
    carry_params = dict(params.get("sleeve_carry", {}))
    if "funding_rates" in paths:
        carry_params.setdefault("funding_path", paths["funding_rates"])
    # True delta-neutral carry reads the OKX derivatives panel (perp funding).
    neutral_params = dict(params.get("sleeve_carry_neutral", {}))
    if "derivatives" in paths:
        neutral_params.setdefault("funding_path", paths["derivatives"])
    statarb_params = dict(params.get("sleeve_statarb", {}))
    defi_params = dict(params.get("sleeve_defi_yield", {}))
    if "defillama_yields" in paths:
        defi_params.setdefault("yields_path", paths["defillama_yields"])
    return {
        "sleeve_trend": TrendSleeve(params.get("sleeve_trend")),
        "sleeve_xsmom": XSMomSleeve(xs_params),
        "sleeve_carry": CarrySleeve(carry_params),
        "sleeve_technical": TechnicalEnsembleSleeve(params.get("sleeve_technical")),
        "sleeve_carry_neutral": CarryNeutralSleeve(neutral_params),
        "sleeve_statarb": StatArbSleeve(statarb_params),
        "sleeve_defi_yield": DefiYieldSleeve(defi_params),
    }


def ensure_papertrade_books(
    run_config_path: str | Path, sleeve_names, sleeves_cfg: dict, output_dir: str
) -> list:
    """Append missing sleeve books to papertrade.books (keeps existing intact).

    The run config is comment-rich, so we never round-trip it through
    yaml.dump. The papertrade.books list is the final block of the file, so a
    textual append of correctly indented entries is a valid YAML edit. The
    result is re-parsed to verify.
    """
    path = Path(run_config_path)
    cfg = yaml.safe_load(path.read_text())
    existing = {b.get("name") for b in (cfg.get("papertrade", {}).get("books") or [])}
    book_cfg = sleeves_cfg.get("papertrade_books", {})
    added = []
    blocks = []
    for name in sleeve_names:
        if name in existing:
            continue
        blocks.append(
            BOOK_TEMPLATE.format(
                name=name,
                weights_path=f"{output_dir}/{name}_weights.parquet",
                rebalance=book_cfg.get("rebalance", "weekly"),
                cost_bps=book_cfg.get("cost_bps", 20),
                starting_cash=book_cfg.get("starting_cash", 100000),
            )
        )
        added.append(name)
    if blocks:
        text = path.read_text()
        if not text.endswith("\n"):
            text += "\n"
        path.write_text(text + "".join(blocks))
        # verify the edit parses and every book is present
        check = yaml.safe_load(path.read_text())
        names = {b.get("name") for b in check["papertrade"]["books"]}
        missing = set(sleeve_names) - names
        if missing:
            raise RuntimeError(f"failed to register papertrade books: {missing}")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CHF sleeve target weights")
    parser.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--config", default=DEFAULT_RUN_CONFIG)
    parser.add_argument("--sleeves-config", default=DEFAULT_SLEEVES_CONFIG)
    parser.add_argument("--no-allocator", action="store_true")
    parser.add_argument("--no-books", action="store_true")
    args = parser.parse_args()

    sleeves_cfg = yaml.safe_load(Path(args.sleeves_config).read_text())
    paths = sleeves_cfg.get("paths", {})
    output_dir = paths.get("output_dir", "data/strategies")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    market_df = load_market_data(paths.get("market_data", "data/raw/market/market_ohlcv.parquet"))
    as_of = args.as_of or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    print(f"[sleeves] as_of={as_of} market_rows={len(market_df)}")

    regime_latest = run_regime(market_df, as_of=as_of, params=sleeves_cfg.get("regime"))
    print(f"[sleeves] regime={regime_latest['regime']} "
          f"(btc_trend_up={regime_latest['btc_trend_up']}, "
          f"breadth={regime_latest['breadth_pct_above_50d_ma']}, "
          f"fear_greed={regime_latest['fear_greed_bucket']})")

    macro = run_macro_overlay(as_of=as_of, params=sleeves_cfg.get("macro_overlay"))
    print(f"[sleeves] macro overlay gross_multiplier={macro['gross_multiplier']:.2f} "
          f"(regime={macro['regime']}, has_fred={macro['has_macro_fred']})")

    sleeves = build_sleeves(sleeves_cfg)
    for name, sleeve in sleeves.items():
        wdf = sleeve.generate(market_df, as_of)
        out_path = upsert_weights_parquet(wdf, Path(output_dir) / f"{name}_weights.parquet")
        print(f"[sleeves] {sleeve.describe_holdings(wdf)} -> {out_path}")

    if not args.no_allocator:
        proposal = run_allocator(
            sleeves, market_df, sleeves_cfg, as_of=as_of, regime_latest=regime_latest,
            macro_multiplier=macro["gross_multiplier"],
        )
        print(f"[sleeves] allocator PROPOSAL (regime={proposal['regime']}, "
              f"cash={proposal['cash_weight']:.3f}):")
        for name, info in proposal["sleeves"].items():
            print(f"    {name}: sortino={info['sortino_90d']:.2f} "
                  f"base={info['base_weight']:.3f} proposed={info['proposed_weight']:.3f} "
                  f"[{info['return_source']}]")

    if not args.no_books:
        added = ensure_papertrade_books(args.config, list(sleeves), sleeves_cfg, output_dir)
        if added:
            print(f"[sleeves] registered papertrade books: {', '.join(added)}")
        else:
            print("[sleeves] papertrade books already registered")


if __name__ == "__main__":
    main()
