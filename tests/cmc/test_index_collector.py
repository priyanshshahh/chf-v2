"""CMC index collector tests (T026, T028) — offline, fixture-driven.

Asserts each index series is unique and continuous on ``date``, constituents
round-trip through JSON, and the combined ``run()`` collects both series into
the shared pro_api_index_historical/ dir with separate manifests.
"""
import json
from pathlib import Path

import pandas as pd

from src.cmc.collectors import cmc_index
from src.cmc.manifest import read_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "index_sample.json"


class FakeClient:
    """Dispatches cmc100/cmc20 historical fixtures; serves each once."""

    def __init__(self, fixture):
        self._fixture = fixture
        self._served = set()

    def get(self, path, params=None):
        if path.endswith("-latest"):
            key = "cmc100" if "cmc100" in path else "cmc20"
            last = self._fixture[key]["data"][-1]
            return {"data": {"value": last["value"],
                             "last_update": last["update_time"]}}
        key = "cmc100" if "cmc100" in path else "cmc20"
        if key in self._served:
            return {"status": {"error_code": 0}, "data": []}
        self._served.add(key)
        return self._fixture[key]


def test_cmc100_unique_continuous_dates(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text())
    res = cmc_index.run_cmc100(FakeClient(fixture), tmp_path,
                               start="2024-01-01", end="2024-01-03")

    df = pd.read_parquet(tmp_path / cmc_index.DATASET / "cmc100_index.parquet")
    assert set(["date", "value", "num_constituents",
                "constituents_json"]).issubset(df.columns)
    assert df["date"].is_unique
    # Continuous daily series (T028 / FR-005).
    dates = pd.to_datetime(df["date"]).sort_values()
    assert (dates.diff().dropna() == pd.Timedelta(days=1)).all()
    assert list(df.sort_values("date")["value"]) == [100.0, 101.2, 99.8]

    csts = json.loads(df.sort_values("date").iloc[0]["constituents_json"])
    assert {c["symbol"] for c in csts} == {"BTC", "ETH"}
    assert "priceUsd" not in csts[0]  # cmc100 stores weights only

    assert res["row_count"] == 3
    assert res["coverage_start"] == "2024-01-01"
    assert res["coverage_end"] == "2024-01-03"


def test_cmc20_carries_price_usd(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text())
    cmc_index.run_cmc20(FakeClient(fixture), tmp_path,
                        start="2024-01-01", end="2024-01-03")

    df = pd.read_parquet(tmp_path / cmc_index.DATASET / "cmc20_index.parquet")
    assert df["date"].is_unique and len(df) == 3
    csts = json.loads(df.sort_values("date").iloc[0]["constituents_json"])
    assert csts[0]["priceUsd"] == 42000.0  # USD constituent pricing retained
    assert csts[0]["units"] is not None


def test_run_collects_both_series_with_own_manifests(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text())
    res = cmc_index.run(FakeClient(fixture), tmp_path,
                        start="2024-01-01", end="2024-01-03")

    out = tmp_path / cmc_index.DATASET
    assert (out / "cmc100_index.parquet").exists()
    assert (out / "cmc20_index.parquet").exists()
    assert res["row_count"] == 6  # 3 days x 2 series
    assert res["coverage_start"] == "2024-01-01"
    assert res["coverage_end"] == "2024-01-03"

    m100 = read_manifest(out, "cmc100_manifest.json")
    m20 = read_manifest(out, "cmc20_manifest.json")
    assert m100 is not None and m100["row_count"] == 3
    assert m20 is not None and m20["row_count"] == 3
    # maintain.py reads incremental coverage via cmc_index.MANIFEST.
    assert cmc_index.MANIFEST == "cmc100_manifest.json"


def test_run_twice_adds_zero_rows(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text())
    r1 = cmc_index.run(FakeClient(fixture), tmp_path,
                       start="2024-01-01", end="2024-01-03")
    r2 = cmc_index.run(FakeClient(fixture), tmp_path,
                       start="2024-01-01", end="2024-01-03")
    assert r1["row_count"] == r2["row_count"] == 6
