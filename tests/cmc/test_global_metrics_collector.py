"""Global metrics collector tests (T023, T025) — offline, fixture-driven.

Asserts USD fields are present, ``date`` is unique, and a repeat run is
idempotent (zero new rows).
"""
import json
from pathlib import Path

import pandas as pd

from src.cmc.collectors import global_metrics
from src.cmc.manifest import read_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "global_metrics_sample.json"

USD_FIELDS = ["total_market_cap", "total_volume_24h", "altcoin_market_cap"]


class FakeClient:
    """Serves the fixture once, then empty pages (ends the paging loop)."""

    def __init__(self, payload):
        self._payload = payload
        self._served = False

    def get(self, path, params=None):
        assert path == "/v1/global-metrics/quotes/historical"
        if not self._served:
            self._served = True
            return self._payload
        return {"status": {"error_code": 0}, "data": {"quotes": []}}


def test_global_metrics_usd_fields_and_unique_dates(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    res = global_metrics.run(FakeClient(payload), tmp_path,
                             start="2024-01-01", end="2024-01-03")

    df = pd.read_parquet(tmp_path / global_metrics.DATASET / global_metrics.FILENAME)
    assert set(USD_FIELDS + ["date", "btc_dominance", "eth_dominance",
                             "active_cryptocurrencies"]).issubset(df.columns)
    assert df["date"].is_unique
    assert len(df) == 3
    for col in USD_FIELDS + ["btc_dominance", "eth_dominance"]:
        assert df[col].notna().all()

    day1 = df[df.date == "2024-01-01"].iloc[0]
    assert day1["total_market_cap"] == 1700000000000.0
    assert day1["btc_dominance"] == 52.1
    assert day1["active_cryptocurrencies"] == 8800

    assert res["row_count"] == 3
    assert res["coverage_start"] == "2024-01-01"
    assert res["coverage_end"] == "2024-01-03"
    m = read_manifest(tmp_path / global_metrics.DATASET)
    assert m is not None and m["dataset"] == global_metrics.DATASET
    assert (tmp_path / global_metrics.DATASET / "raw_api_samples"
            / "global_metrics_sample.json").exists()


def test_global_metrics_run_twice_adds_zero_rows(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    r1 = global_metrics.run(FakeClient(payload), tmp_path,
                            start="2024-01-01", end="2024-01-03")
    r2 = global_metrics.run(FakeClient(payload), tmp_path,
                            start="2024-01-01", end="2024-01-03")

    assert r1["row_count"] == r2["row_count"] == 3
    df = pd.read_parquet(tmp_path / global_metrics.DATASET / global_metrics.FILENAME)
    assert len(df) == 3
