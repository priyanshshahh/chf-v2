"""Quotes collector tests (T016) — offline, fixture-driven.

Covers parse() correctness (USD normalization fields), the standard
``run(client, base_dir)`` interface (map-driven universe incl. a delisted coin),
and idempotent upsert via storage: running twice adds zero rows.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from src.cmc.collectors import quotes
from src.cmc.manifest import read_manifest
from src.cmc.storage import upsert

FIXTURE = Path(__file__).parent / "fixtures" / "quotes_sample.json"


class FakeClient:
    """Serves the recorded quotes fixture; key/info reports zero credits."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        if path == "/v1/key/info":
            return {"data": {"usage": {"current_month": {"credits_used": 0}}}}
        if "quotes/historical" in path:
            return self._payload
        raise AssertionError(f"unexpected path {path}")


def _write_map(base_dir: Path) -> None:
    """Minimal cmc_map.parquet under pro_api_map/ (layout maintain.py uses)."""
    map_dir = base_dir / "pro_api_map"
    map_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "cmc_id": [1, 4172],
            "symbol": ["BTC", "LUNC"],
            "name": ["Bitcoin", "Terra Classic"],
            "is_active": [True, False],
            "first_historical_data": ["2010-07-13", "2019-07-26"],
            # LUNC inactive but inside the 36-month window -> must stay in universe
            "last_historical_data": ["2026-06-02", "2026-06-02"],
        }
    ).to_parquet(map_dir / "cmc_map.parquet", index=False)


def test_parse_usd_normalization():
    payload = json.loads(FIXTURE.read_text())
    recs = quotes.parse(payload)

    assert len(recs) == 4  # 2 coins x 2 days
    for r in recs:
        assert set(r) == set(quotes.COLUMNS)
        assert r["date"].count("-") == 2 and len(r["date"]) == 10  # YYYY-MM-DD
        assert isinstance(r["price"], float)
        assert isinstance(r["market_cap"], float)
        assert isinstance(r["volume_24h"], float)

    btc = [r for r in recs if r["cmc_id"] == 1 and r["date"] == "2026-06-01"][0]
    assert btc["symbol"] == "BTC"
    assert btc["price"] == 68000.5
    assert btc["market_cap"] == 1340000000000.0
    assert btc["volume_24h"] == 25000000000.0


def test_run_writes_parquet_manifest_and_retains_delisted(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    _write_map(tmp_path)

    res = quotes.run(FakeClient(payload), tmp_path)

    path = tmp_path / quotes.DATASET / quotes.FILENAME
    df = pd.read_parquet(path)
    assert set(quotes.COLUMNS).issubset(df.columns)
    assert len(df) == 4
    assert not df.duplicated(subset=quotes.KEY).any()  # (cmc_id, date) unique
    assert set(df["cmc_id"].unique()) == {1, 4172}  # delisted LUNC retained

    # Standard interface returns the manifest dict maintain.py consumes.
    assert res["row_count"] == 4
    assert res["coverage_start"] == "2026-06-01"
    assert res["coverage_end"] == "2026-06-02"
    m = read_manifest(tmp_path / quotes.DATASET)
    assert m is not None and m["row_count"] == 4
    assert (tmp_path / quotes.DATASET / "raw_api_samples"
            / "quotes_daily_sample.json").exists()


def _payload_for_day(day: str) -> dict:
    """Fixture payload rewritten to a single (later) day for both coins."""
    payload = json.loads(FIXTURE.read_text())
    for entry in payload["data"].values():
        q = entry["quotes"][0]
        q["timestamp"] = f"{day}T00:00:00.000Z"
        q["quote"]["USD"]["timestamp"] = f"{day}T00:00:00.000Z"
        entry["quotes"] = [q]
    return payload


def test_run_twice_is_idempotent_and_later_end_appends(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    _write_map(tmp_path)

    r1 = quotes.run(FakeClient(payload), tmp_path)
    r2 = quotes.run(FakeClient(payload), tmp_path)

    path = tmp_path / quotes.DATASET / quotes.FILENAME
    assert r1["row_count"] == r2["row_count"] == 4
    assert len(pd.read_parquet(path)) == 4

    # Regression (monthly maintain): a later [start, end] window must APPEND
    # new dates for coins already present in the parquet — the old
    # presence-based resume skipped every known cmc_id and never appended.
    r3 = quotes.run(FakeClient(_payload_for_day("2026-06-03")), tmp_path,
                    start="2026-06-03", end="2026-06-03")
    df = pd.read_parquet(path)
    assert r3["row_count"] == len(df) == 6  # 4 existing + 1 new day x 2 coins
    assert set(df[df["cmc_id"] == 1]["date"]) == {
        "2026-06-01", "2026-06-02", "2026-06-03"}
    assert r3["coverage_end"] == "2026-06-03"


class AllFailClient:
    """key/info works; every data call raises (systemic outage)."""

    def get(self, path, params=None):
        if path == "/v1/key/info":
            return {"data": {"usage": {"current_month": {"credits_used": 0}}}}
        raise RuntimeError("HTTP 500 simulated outage")


def test_run_raises_when_all_batches_fail(tmp_path: Path):
    _write_map(tmp_path)
    with pytest.raises(RuntimeError, match="all_batches_failed"):
        quotes.run(AllFailClient(), tmp_path)
    # Nothing was written: the run must not masquerade as completed.
    assert not (tmp_path / quotes.DATASET / quotes.FILENAME).exists()


def test_upsert_idempotent_on_quotes_key(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    df = pd.DataFrame(quotes.parse(payload), columns=quotes.COLUMNS)
    path = tmp_path / "q.parquet"

    n1 = upsert(df, key=quotes.KEY, path=path)
    n2 = upsert(df, key=quotes.KEY, path=path)
    assert n1 == n2 == 4
