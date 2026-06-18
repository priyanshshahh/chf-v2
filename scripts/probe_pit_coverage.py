#!/usr/bin/env python3
"""
probe_pit_coverage.py
=====================
De-risk the survivorship-free (`market_data_pit`) ingest BEFORE committing to the
multi-hour live run.

For every coin in the **union** universe (every `cmc_id` ever eligible across all monthly
snapshots — including since-delisted names), this reports whether usable market data is
already available **locally** (on disk), so you know up front which dead coins are at risk
of returning nothing. It reports only real, observed availability — it never fabricates
prices or coverage.

Signals checked per coin (offline, cache-first):
  1. `data/raw/market/by_symbol/<SYM>_ohlcv.parquet` — already-ingested rows (full vs partial).
  2. CCXT OHLCV cache (`data/cache/market/ccxt_*/ohlcv_*<SYM>*`).
  3. Fallback-provider cache (`data/cache/market/{cryptocompare,coingecko,coincap,coinpaprika}_market/*`).

Output: a per-coin table + summary written to `data/readiness/pit_coverage.{json,md}`, and a
printed risk list of union coins with **no local data** (the ones to watch in the live run).

Usage:
  python scripts/probe_pit_coverage.py [--config configs/run_config.yaml]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _sanitize(symbol: str) -> str:
    return str(symbol).replace("/", "_").replace("-", "_")


def probe_pit_coverage(
    universe_path: Path | str,
    market_dir: Path | str,
    cache_dir: Path | str,
) -> Dict[str, Any]:
    """Return a coverage report dict for the union universe. Pure disk inspection."""
    universe_path = Path(universe_path)
    market_dir = Path(market_dir)
    cache_dir = Path(cache_dir)
    if not universe_path.exists():
        raise FileNotFoundError(f"universe_monthly.parquet missing: {universe_path}")

    uni = pd.read_parquet(universe_path)
    if "is_eligible" in uni.columns:
        uni = uni[uni["is_eligible"].fillna(False).astype(bool)].copy()
    # Union: one row per stable cmc_id (latest appearance for identity).
    uni["symbol"] = uni["symbol"].astype(str).str.upper()
    key = "cmc_id" if "cmc_id" in uni.columns and uni["cmc_id"].notna().any() else "symbol"
    union = uni.sort_values("snapshot_date").groupby(key, as_index=False).last()

    by_symbol_dir = market_dir / "by_symbol"
    rows: List[Dict[str, Any]] = []
    for _, r in union.iterrows():
        sym = str(r["symbol"]).upper()
        san = _sanitize(sym)
        status, source, n_rows, full = "no_local_data", "", 0, False

        bs = by_symbol_dir / f"{sym}_ohlcv.parquet"
        if bs.exists():
            try:
                df = pd.read_parquet(bs)
                n_rows = int(len(df))
                full = bool(df.get("is_full_ohlcv", pd.Series([False])).fillna(False).astype(bool).any())
                status = "ingested_full" if full else "ingested_partial"
                source = "by_symbol"
            except Exception:
                status, source = "unreadable", "by_symbol"
        else:
            # cache-only signals
            ccxt_hits = list(cache_dir.glob(f"ccxt_*/ohlcv_{san}_*.json"))
            fb_hits = []
            for prov in ["cryptocompare_market", "coingecko_market", "coincap_market", "coinpaprika_market"]:
                fb_hits += list(cache_dir.glob(f"{prov}/{sym}_*.json")) + list(cache_dir.glob(f"{prov}/*{san}*.json"))
            if ccxt_hits:
                status, source = "cache_only", f"ccxt:{ccxt_hits[0].parent.name}"
            elif fb_hits:
                status, source = "cache_only", f"fallback:{fb_hits[0].parent.name}"

        rows.append({
            "cmc_id": int(r["cmc_id"]) if key == "cmc_id" and pd.notna(r.get("cmc_id")) else None,
            "symbol": sym,
            "status": status,
            "source": source,
            "rows": n_rows,
            "is_full_ohlcv": full,
        })

    df = pd.DataFrame(rows)
    status_counts = df["status"].value_counts().to_dict()
    no_data = df[df["status"] == "no_local_data"]["symbol"].tolist()
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "union_coins": int(len(df)),
        "ingested_full": int((df["status"] == "ingested_full").sum()),
        "ingested_partial": int((df["status"] == "ingested_partial").sum()),
        "cache_only": int((df["status"] == "cache_only").sum()),
        "no_local_data": int(len(no_data)),
        "status_counts": status_counts,
        "no_local_data_symbols": sorted(no_data),
        "rows": rows,
    }
    return report


def _write_report(report: Dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pit_coverage.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    lines = [
        "# PIT-mode coverage probe",
        "",
        f"- Union coins: **{report['union_coins']}**",
        f"- Ingested (full OHLCV): {report['ingested_full']}",
        f"- Ingested (partial): {report['ingested_partial']}",
        f"- Cache-only (not yet ingested): {report['cache_only']}",
        f"- **No local data (risk set): {report['no_local_data']}**",
        "",
        "## Coins with no local data (verify these in the live run)",
        "",
        (", ".join(report["no_local_data_symbols"]) or "None — every union coin has local data"),
    ]
    (out_dir / "pit_coverage.md").write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local coverage for the union (PIT) universe")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    from configs.config import load_config, resolve_path

    cfg = load_config(Path(args.config) if args.config else None)
    universe_path = resolve_path(cfg, "raw") / "universe" / "universe_monthly.parquet"
    market_dir = resolve_path(cfg, "raw") / "market"
    cache_raw = cfg.get("market_data", {}).get("cache_dir", "data/cache/market")
    cache_dir = Path(cache_raw)
    if not cache_dir.is_absolute():
        cache_dir = Path(cfg["_project_root"]) / cache_dir
    report = probe_pit_coverage(universe_path, market_dir, cache_dir)
    out_dir = Path(cfg["_project_root"]) / "data" / "readiness"
    _write_report(report, out_dir)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    print(f"\n[probe] Report written to {out_dir / 'pit_coverage.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
