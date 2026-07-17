"""DeFiLlama yield-rotation sleeve (long-only, uncorrelated-return; Arca/Apollo
style).

Ranks DeFiLlama pools by risk-adjusted APY subject to a TVL floor and a
stablecoin / blue-chip symbol filter, then allocates to the top pools. The
paper proxy holds each pool's *base asset* (mapped to a tradeable symbol)
weighted by allocation. The pool APY is NOT price P&L — it is written to a
diagnostics parquet as ``virtual_daily_yield`` (apy / 365 while held). This is
a yield proxy, not principal-at-risk modeling: read the book equity together
with the diagnostics to evaluate the sleeve honestly.

Risk-adjusted APY = apy * clip(tvl / tvl_reference, 0, 1): deeper pools are
treated as safer, so a high APY on a thin pool is discounted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .base import Sleeve, build_weights_frame, signal_date_for

# Pool symbol -> tradeable base asset the paper book can price.
BASE_ASSET_MAP = {
    "BTC": "BTC", "WBTC": "BTC", "CBBTC": "BTC", "BTCB": "BTC", "BTC.B": "BTC", "TBTC": "BTC",
    "ETH": "ETH", "WETH": "ETH", "STETH": "ETH", "WSTETH": "ETH", "CBETH": "ETH",
    "ETHX": "ETH", "EZETH": "ETH", "WEETH": "ETH", "RETH": "ETH",
    "SOL": "SOL", "JITOSOL": "SOL", "BSOL": "SOL", "MSOL": "SOL", "BNSOL": "SOL", "JUPSOL": "SOL",
    "USDC": "USDC", "USDT": "USDT", "DAI": "DAI", "USDS": "USDC", "SUSDS": "USDC",
    "USDE": "USDC", "SUSDE": "USDC",
}

DEFAULT_PARAMS = {
    "yields_path": "data/external/defillama/yields.parquet",
    "tvl_floor_usd": 5.0e7,
    "tvl_reference_usd": 1.0e9,
    "top_k": 5,
    "gross_target": 0.9,
    "min_apy": 0.005,          # ignore ~0% pools
    "diagnostics_path": "data/strategies/defi_yield_diagnostics.parquet",
}


class DefiYieldSleeve(Sleeve):
    name = "sleeve_defi_yield"

    def __init__(self, params: dict | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def load_yields(self) -> Optional[pd.DataFrame]:
        path = Path(self.params["yields_path"])
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        needed = {"pool_id", "project", "symbol", "apy", "tvl_usd"}
        if not needed.issubset(df.columns):
            return None
        return df

    def rank_pools(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        d = df.copy()
        d["symbol_u"] = d["symbol"].astype(str).str.upper()
        d["base_asset"] = d["symbol_u"].map(BASE_ASSET_MAP)
        d = d[d["base_asset"].notna()]
        d = d[d["tvl_usd"] >= float(p["tvl_floor_usd"])]
        d = d[d["apy"] >= float(p["min_apy"])]
        if d.empty:
            return d
        tvl_w = (d["tvl_usd"] / float(p["tvl_reference_usd"])).clip(upper=1.0)
        d["risk_adjusted_apy"] = d["apy"] * tvl_w
        d = d.sort_values(["risk_adjusted_apy", "tvl_usd", "pool_id"], ascending=False)
        return d.head(int(p["top_k"]))

    def generate(self, market_df: pd.DataFrame, as_of) -> pd.DataFrame:
        p = self.params
        signal_date = signal_date_for(market_df, as_of)
        raw = self.load_yields()

        weights: Dict[str, float] = {}
        if raw is not None and len(raw):
            top = self.rank_pools(raw)
            if len(top):
                total = float(top["risk_adjusted_apy"].sum())
                if total > 0:
                    gross = float(p["gross_target"])
                    # allocate proportional to risk-adjusted APY, aggregate by
                    # base asset (multiple pools can share a base)
                    for _, r in top.iterrows():
                        base = r["base_asset"]
                        w = gross * float(r["risk_adjusted_apy"]) / total
                        weights[base] = weights.get(base, 0.0) + w
                self._write_diagnostics(top, signal_date)
        return build_weights_frame(weights, signal_date, self.name)

    def _write_diagnostics(self, top: pd.DataFrame, signal_date) -> None:
        raw_path = self.params.get("diagnostics_path")
        if not raw_path:
            return
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        diag = top[["pool_id", "project", "symbol", "base_asset", "apy",
                    "tvl_usd", "risk_adjusted_apy"]].copy()
        diag["date_ts"] = pd.Timestamp(signal_date)
        diag["virtual_daily_yield"] = diag["apy"] / 365.0
        diag = diag.sort_values("risk_adjusted_apy", ascending=False).reset_index(drop=True)
        diag.to_parquet(path, index=False)
