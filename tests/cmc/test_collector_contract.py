"""Collector interface contract tests (T032 review follow-ups) — offline.

Covers:
* is_ts=True collectors honor the requested [start, end] fetch range
  (base.CollectorRun contract), with fear_greed / altcoin_season as the two
  documented exceptions;
* systemic batch failures raise (all_batches_failed) instead of reporting
  success (ohlcv, exchange_info, info, categories — quotes has its own test);
* altcoin_season records gap_detected/gap_range when the fixed 90d window has
  moved past the previous coverage_end;
* categories membership uses a 30-day freshness rule (scanned_at_utc), not
  "presence = done forever".
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.cmc import maintain
from src.cmc.collectors import altcoin_season, categories, exchange_info, info, ohlcv
from src.cmc.manifest import read_manifest, write_manifest

END = "2026-06-30"

# Documented in base.CollectorRun: fear_greed always re-pages full history
# (cheap + self-healing); altcoin_season is pinned to a fixed 90d window and
# records gaps in its manifest instead.
START_EXEMPT = {"fear_greed", "altcoin_season"}

TS_COLLECTORS = [name for name, (_, is_ts) in maintain.REGISTRY.items() if is_ts]


class CaptureClient:
    """Stub client capturing every (path, params) request; canned responses."""

    def __init__(self):
        self.requests = []

    def get(self, path, params=None):
        self.requests.append((path, dict(params or {})))
        if path == "/v1/key/info":
            return {"data": {"usage": {"current_month": {"credits_used": 0}}}}
        if "cryptocurrency/quotes/historical" in path:
            return {"status": {"error_code": 0}, "data": {}}
        if "ohlcv/historical" in path:
            return {"status": {"error_code": 0}, "data": {}}
        if "global-metrics" in path:
            return {"status": {"error_code": 0}, "data": {"quotes": [
                {"timestamp": f"{END}T00:00:00Z",
                 "btc_dominance": 50.0, "eth_dominance": 17.0,
                 "active_cryptocurrencies": 9000,
                 "quote": {"USD": {"total_market_cap": 1.0,
                                   "total_volume_24h": 1.0,
                                   "altcoin_market_cap": 1.0}}}]}}
        if "fear-and-greed" in path:
            return {"status": {"error_code": 0}, "data": []}
        if "altcoin-season-index" in path:
            return {"status": {"error_code": 0}, "data": {"points": []}}
        if path.endswith("-latest"):
            return {"data": {}}
        if "cmc100-historical" in path or "cmc20-historical" in path:
            return {"status": {"error_code": 0}, "data": [
                {"update_time": f"{END}T00:00:00Z", "value": 100.0,
                 "constituents": []}]}
        raise AssertionError(f"unexpected path {path}")


def _write_map(base_dir: Path) -> None:
    map_dir = base_dir / "pro_api_map"
    map_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "cmc_id": [1, 1027],
            "symbol": ["BTC", "ETH"],
            "name": ["Bitcoin", "Ethereum"],
            "is_active": [True, True],
            "first_historical_data": ["2010-07-13", "2015-08-07"],
            "last_historical_data": [END, END],
        }
    ).to_parquet(map_dir / "cmc_map.parquet", index=False)


@pytest.mark.parametrize("name", TS_COLLECTORS)
def test_ts_collector_honors_requested_start(name, tmp_path: Path):
    """run(start=X) must move the requested fetch range (CollectorRun contract)."""
    if name in START_EXEMPT:
        pytest.skip(f"{name}: documented exception — does not honor start "
                    "(see base.CollectorRun)")
    module, _ = maintain.REGISTRY[name]
    _write_map(tmp_path)

    def requested_start(start: str) -> str:
        client = CaptureClient()
        module.run(client, tmp_path, start=start, end=END)
        starts = [p["time_start"] for _, p in client.requests
                  if "time_start" in p]
        assert starts, f"{name}: no request carried time_start"
        return str(starts[0])

    assert requested_start("2026-05-01").startswith("2026-05-01")
    assert requested_start("2026-06-10").startswith("2026-06-10")


class AllFailClient:
    """key/info and the category directory work; every data call raises."""

    def get(self, path, params=None):
        if path == "/v1/key/info":
            return {"data": {"usage": {"current_month": {"credits_used": 0}}}}
        if path == "/v1/cryptocurrency/categories":
            return {"data": [{"id": "cat-1", "name": "DeFi", "title": "DeFi",
                              "num_tokens": 1, "avg_price_change": 1.0,
                              "market_cap": 1.0, "volume": 1.0,
                              "last_updated": "2026-07-01T00:00:00Z"}]}
        raise RuntimeError("HTTP 500 simulated outage")


def test_ohlcv_raises_when_all_fetches_fail(tmp_path: Path):
    with pytest.raises(RuntimeError, match="all_batches_failed"):
        ohlcv.run(AllFailClient(), tmp_path, end=END)


def test_exchange_info_raises_when_all_batches_fail(tmp_path: Path):
    em_path = tmp_path / "cmc_exchange_map.parquet"
    pd.DataFrame({"exchange_id": [270]}).to_parquet(em_path, index=False)
    with pytest.raises(RuntimeError, match="all_batches_failed"):
        exchange_info.run(AllFailClient(), tmp_path, em_path)


def test_info_raises_when_all_batches_fail(tmp_path: Path):
    _write_map(tmp_path)
    with pytest.raises(RuntimeError, match="all_batches_failed"):
        info.run(AllFailClient(), tmp_path,
                 tmp_path / "pro_api_map" / "cmc_map.parquet")


def test_categories_raises_when_all_membership_calls_fail(tmp_path: Path):
    with pytest.raises(RuntimeError, match="all_batches_failed"):
        categories.run(AllFailClient(), tmp_path)


# ---------------------------------------------------------------- altcoin gap

class AltSeasonClient:
    def __init__(self, day: str):
        self._day = day

    def get(self, path, params=None):
        assert "altcoin-season-index" in path
        return {"status": {"error_code": 0}, "data": {"points": [
            {"timestamp": f"{self._day}T00:00:00Z",
             "altcoinIndex": 50.0, "altcoinMarketcap": 1.0}]}}


def _alt_manifest(tmp_path: Path, coverage_end: str) -> Path:
    out_dir = tmp_path / altcoin_season.DATASET
    write_manifest(out_dir=out_dir, dataset=altcoin_season.DATASET,
                   source=altcoin_season.SOURCE, coverage_start="2024-01-01",
                   coverage_end=coverage_end, row_count=1, symbol_count=None,
                   generated_by="test")
    return out_dir


def test_altcoin_season_detects_unrecoverable_gap(tmp_path: Path):
    out_dir = _alt_manifest(tmp_path, coverage_end="2024-01-02")
    today = datetime.now(timezone.utc).date()
    altcoin_season.run(AltSeasonClient(today.isoformat()), tmp_path)

    m = read_manifest(out_dir)
    assert m["run"]["gap_detected"] is True
    window_start = (today - timedelta(days=90)).isoformat()
    assert m["run"]["gap_range"] == ["2024-01-02", window_start]


def test_altcoin_season_no_gap_when_coverage_recent(tmp_path: Path):
    today = datetime.now(timezone.utc).date()
    out_dir = _alt_manifest(tmp_path, coverage_end=today.isoformat())
    altcoin_season.run(AltSeasonClient(today.isoformat()), tmp_path)

    m = read_manifest(out_dir)
    assert "gap_detected" not in m["run"]
    assert "gap_range" not in m["run"]


# --------------------------------------------------------- categories refresh

class CategoriesClient:
    """Serves one category; counts membership calls."""

    def __init__(self):
        self.member_calls = 0

    def get(self, path, params=None):
        if path == "/v1/key/info":
            return {"data": {"usage": {"current_month": {"credits_used": 0}}}}
        if path == "/v1/cryptocurrency/categories":
            return {"data": [{"id": "cat-1", "name": "DeFi", "title": "DeFi",
                              "num_tokens": 1, "avg_price_change": 1.0,
                              "market_cap": 1.0, "volume": 1.0,
                              "last_updated": "2026-07-01T00:00:00Z"}]}
        if path == "/v1/cryptocurrency/category":
            self.member_calls += 1
            return {"data": {"coins": [{"id": 1}]}}
        raise AssertionError(f"unexpected path {path}")


def test_categories_freshness_rule(tmp_path: Path):
    client = CategoriesClient()
    members_path = tmp_path / categories.DATASET / categories.MEMBERS_FILE

    categories.run(client, tmp_path)
    assert client.member_calls == 1
    members = pd.read_parquet(members_path)
    assert "scanned_at_utc" in members.columns
    assert members["scanned_at_utc"].notna().all()

    # Fresh (< 30 days old) -> skipped on the next run.
    categories.run(client, tmp_path)
    assert client.member_calls == 1

    # Stale (> 30 days old) -> rescanned.
    members["scanned_at_utc"] = (
        datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    members.to_parquet(members_path, index=False)
    categories.run(client, tmp_path)
    assert client.member_calls == 2


def test_categories_legacy_rows_without_timestamp_are_stale(tmp_path: Path):
    members_path = tmp_path / categories.DATASET / categories.MEMBERS_FILE
    members_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"category_id": ["cat-1"], "cmc_id": [1]}).to_parquet(
        members_path, index=False)

    client = CategoriesClient()
    categories.run(client, tmp_path)
    assert client.member_calls == 1  # presence alone is NOT "done forever"
    members = pd.read_parquet(members_path)
    assert "scanned_at_utc" in members.columns
    assert members["scanned_at_utc"].notna().all()
