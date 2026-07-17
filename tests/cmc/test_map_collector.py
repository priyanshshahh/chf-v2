"""Map collector parse test (offline, fixture-driven).

Uses a FakeClient that serves the recorded /v1/cryptocurrency/map fixture for the
first listing_status and empty pages thereafter, then asserts the delisted coin
(Terra Classic, cmc_id=4172) is retained with is_active=False.
"""
import json
from pathlib import Path

import pandas as pd

from src.cmc.collectors import map as coin_map

FIXTURE = Path(__file__).parent / "fixtures" / "map_sample.json"


class FakeClient:
    """Returns the fixture once (active), empty for inactive/untracked pages."""

    def __init__(self, payload):
        self._payload = payload
        self._served = False

    def get(self, path, params=None):
        if not self._served and (params or {}).get("listing_status") == "active":
            self._served = True
            return self._payload
        return {"status": {"error_code": 0}, "data": []}


def test_map_parses_and_retains_delisted(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    res = coin_map.run(FakeClient(payload), tmp_path)

    df = pd.read_parquet(tmp_path / coin_map.DATASET / coin_map.FILENAME)
    assert set(["cmc_id", "symbol", "name", "slug", "is_active",
                "first_historical_data", "last_historical_data"]).issubset(df.columns)
    assert df["cmc_id"].is_unique

    lunc = df[df.cmc_id == 4172].iloc[0]
    assert lunc["symbol"] == "LUNC"
    assert bool(lunc["is_active"]) is False  # delisted retained, flagged inactive
    assert lunc["first_historical_data"] == "2019-07-26"  # ISO normalized to date

    btc = df[df.cmc_id == 1].iloc[0]
    assert bool(btc["is_active"]) is True
    assert res["row_count"] == 2
