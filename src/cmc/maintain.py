#!/usr/bin/env python3
"""maintain.py — monthly maintenance orchestrator (idempotent incremental upsert).

MIGRATION NOTE: fear_greed and altcoin_season used to share the
``pro_api_index_historical`` dataset dir with the CMC100/CMC20 indices; they now
write to their own dirs (``pro_api_fear_greed``, ``pro_api_altcoin_season``).
Old combined dirs named ``pro_api_index_historical`` may therefore contain mixed
datasets (cmc_fear_greed.parquet, cmc_altcoin_season.parquet and their alternate
manifest files) from before the split.

For each dataset: read the manifest's ``coverage_end``, fetch only newer data
(default: through the latest complete data), upsert by primary key, and update the
manifest. Re-running for the same period adds zero rows and changes no existing
rows (the upsert dedups by key with keep="last").

CLI:  python -m src.cmc.maintain [--datasets map global_metrics ...] [--month YYYY-MM]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from .client import CMCClient
from .collectors import (
    altcoin_season,
    cmc_index,
    exchange_map,
    fear_greed,
    fiat_fx,
    fiat_map,
    global_metrics,
    map as coin_map,
    ohlcv,
    quotes,
)
from .manifest import read_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "cmc_complete" / "data" / "coinmarketcap_data"

# name -> (module, is_time_series). Time-series collectors accept start=coverage_end+1.
REGISTRY: Dict[str, tuple] = {
    "map": (coin_map, False),
    "fiat_map": (fiat_map, False),
    "exchange_map": (exchange_map, False),
    "fiat_fx": (fiat_fx, False),
    "global_metrics": (global_metrics, True),
    "fear_greed": (fear_greed, True),
    "altcoin_season": (altcoin_season, True),
    "cmc_index": (cmc_index, True),
    "ohlcv": (ohlcv, True),
    "quotes": (quotes, True),
}


def _next_day(iso: str) -> str:
    return (pd.to_datetime(iso) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _month_end(month: str) -> str:
    start = pd.to_datetime(month + "-01")
    return (start + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")


def maintain(
    datasets: Optional[List[str]] = None,
    month: Optional[str] = None,
    base_dir: Path = BASE_DIR,
) -> List[dict]:
    names = datasets or list(REGISTRY)
    client = CMCClient()
    end = _month_end(month) if month else datetime.now(timezone.utc).date().isoformat()

    results: List[dict] = []
    failures: List[str] = []
    for name in names:
        module, is_ts = REGISTRY[name]
        out_dir = base_dir / module.DATASET
        start = None
        if is_ts:
            m = read_manifest(out_dir, getattr(module, "MANIFEST", "manifest.json"))
            if m and m.get("coverage_end"):
                start = _next_day(m["coverage_end"])
        try:
            res = module.run(client, base_dir, start=start, end=end)
            print(f"[maintain] {name}: rows={res['row_count']} "
                  f"coverage={res['coverage_start']}..{res['coverage_end']}")
            results.append(res)
        except Exception as exc:  # record + continue; exit non-zero at the end
            print(f"[maintain] {name}: FAILED {exc}", file=sys.stderr)
            failures.append(name)

    if failures:
        raise SystemExit(f"maintenance failed for: {', '.join(failures)}")
    return results


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="CMC monthly maintenance (idempotent).")
    p.add_argument("--datasets", nargs="*", choices=list(REGISTRY), default=None,
                   help="Subset of datasets to maintain (default: all).")
    p.add_argument("--month", default=None, help="Target month YYYY-MM (default: today).")
    args = p.parse_args(argv)
    maintain(datasets=args.datasets, month=args.month)
    return 0


if __name__ == "__main__":
    sys.exit(main())
