"""
Tests for scripts/probe_pit_coverage.py — honest local-coverage inspection for the
union (PIT) universe. The probe reports only observed on-disk data; never fabricates.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.probe_pit_coverage import probe_pit_coverage, _write_report


def _universe(tmp_path: Path) -> Path:
    rows = []
    for snap in ["2022-01-01", "2022-02-01"]:
        for cid, sym in [(1, "BTC"), (2, "DEADCO"), (3, "CACHEDCO")]:
            rows.append({
                "snapshot_date": pd.Timestamp(snap, tz="UTC"),
                "cmc_id": cid, "symbol": sym, "is_eligible": True,
            })
    p = tmp_path / "universe_monthly.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    return p


def test_probe_classifies_each_coin(tmp_path):
    uni = _universe(tmp_path)
    market_dir = tmp_path / "market"
    cache_dir = tmp_path / "cache"
    (market_dir / "by_symbol").mkdir(parents=True)
    (cache_dir / "ccxt_coinbase").mkdir(parents=True)

    # BTC: ingested full
    pd.DataFrame({
        "date_ts": pd.to_datetime(["2022-01-01"], utc=True),
        "close": [40000.0], "is_full_ohlcv": [True],
    }).to_parquet(market_dir / "by_symbol" / "BTC_ohlcv.parquet", index=False)
    # CACHEDCO: only a CCXT cache file (not yet ingested)
    (cache_dir / "ccxt_coinbase" / "ohlcv_CACHEDCO_USD_2022-01-01_1d.json").write_text("[]")
    # DEADCO: nothing anywhere

    report = probe_pit_coverage(uni, market_dir, cache_dir)
    by_sym = {r["symbol"]: r["status"] for r in report["rows"]}
    assert report["union_coins"] == 3
    assert by_sym["BTC"] == "ingested_full"
    assert by_sym["CACHEDCO"] == "cache_only"
    assert by_sym["DEADCO"] == "no_local_data"
    assert report["no_local_data"] == 1
    assert "DEADCO" in report["no_local_data_symbols"]


def test_probe_writes_reports(tmp_path):
    uni = _universe(tmp_path)
    report = probe_pit_coverage(uni, tmp_path / "market", tmp_path / "cache")
    out = tmp_path / "readiness"
    _write_report(report, out)
    assert (out / "pit_coverage.json").exists()
    assert (out / "pit_coverage.md").exists()
    # all 3 coins have no local data here → risk list lists them
    assert report["no_local_data"] == 3
