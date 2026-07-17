"""Maintenance orchestrator idempotency (T019) — offline, mocked client.

Runs ``maintain()`` twice for the same month with a stub client (no network,
no CMC_API_KEY): per-dataset row counts must be unchanged, manifests must stay
consistent with the parquet files, and — regression for the DATASET name
collision — fear_greed / altcoin_season must land in their own dataset dirs,
not clobber pro_api_index_historical.
"""
import json
from pathlib import Path

import pandas as pd

from src.cmc import maintain as maintain_mod
from src.cmc.manifest import read_manifest

FIXTURES = Path(__file__).parent / "fixtures"

FEAR_GREED_PAYLOAD = {
    "status": {"error_code": 0},
    "data": [
        {"timestamp": "1704067200", "value": 65, "value_classification": "Greed"},
        {"timestamp": "1704153600", "value": 70, "value_classification": "Greed"},
        {"timestamp": "1704240000", "value": 40, "value_classification": "Fear"},
    ],
}

ALTCOIN_PAYLOAD = {
    "status": {"error_code": 0},
    "data": {"points": [
        {"timestamp": "1704067200", "altcoinIndex": 55.0,
         "altcoinMarketcap": 800000000000.0},
        {"timestamp": "1704153600", "altcoinIndex": 57.5,
         "altcoinMarketcap": 812000000000.0},
    ]},
}


class FakeClient:
    """Stub CMCClient: serves each historical endpoint once, then empty."""

    def __init__(self):
        self._index_fixture = json.loads((FIXTURES / "index_sample.json").read_text())
        self._gm_fixture = json.loads(
            (FIXTURES / "global_metrics_sample.json").read_text())
        self._served = set()

    def _once(self, key, payload, empty):
        if key in self._served:
            return empty
        self._served.add(key)
        return payload

    def get(self, path, params=None):
        if "global-metrics" in path:
            return self._once("gm", self._gm_fixture,
                              {"status": {"error_code": 0}, "data": {"quotes": []}})
        if "fear-and-greed" in path:
            return self._once("fg", FEAR_GREED_PAYLOAD,
                              {"status": {"error_code": 0}, "data": []})
        if "altcoin-season-index" in path:
            return ALTCOIN_PAYLOAD  # single-shot endpoint (no paging)
        if "cmc100-latest" in path or "cmc20-latest" in path:
            return {"data": {"value": 99.8, "last_update": "2024-01-03T00:00:00Z"}}
        if "cmc100" in path:
            return self._once("c100", self._index_fixture["cmc100"],
                              {"status": {"error_code": 0}, "data": []})
        if "cmc20" in path:
            return self._once("c20", self._index_fixture["cmc20"],
                              {"status": {"error_code": 0}, "data": []})
        raise AssertionError(f"unexpected path {path}")


DATASETS = ["global_metrics", "fear_greed", "altcoin_season", "cmc_index"]


def _rows(base: Path, dataset: str, filename: str) -> int:
    return len(pd.read_parquet(base / dataset / filename))


def test_maintain_twice_same_month_no_duplicates(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(maintain_mod, "CMCClient", FakeClient)

    r1 = maintain_mod.maintain(datasets=DATASETS, month="2024-01",
                               base_dir=tmp_path)
    counts1 = {
        "global_metrics": _rows(tmp_path, "pro_api_global_metrics_historical",
                                "cmc_global_metrics.parquet"),
        "fear_greed": _rows(tmp_path, "pro_api_fear_greed",
                            "cmc_fear_greed.parquet"),
        "altcoin_season": _rows(tmp_path, "pro_api_altcoin_season",
                                "cmc_altcoin_season.parquet"),
        "cmc100": _rows(tmp_path, "pro_api_index_historical",
                        "cmc100_index.parquet"),
        "cmc20": _rows(tmp_path, "pro_api_index_historical",
                       "cmc20_index.parquet"),
    }
    assert counts1 == {"global_metrics": 3, "fear_greed": 3,
                       "altcoin_season": 2, "cmc100": 3, "cmc20": 3}

    r2 = maintain_mod.maintain(datasets=DATASETS, month="2024-01",
                               base_dir=tmp_path)
    counts2 = {
        "global_metrics": _rows(tmp_path, "pro_api_global_metrics_historical",
                                "cmc_global_metrics.parquet"),
        "fear_greed": _rows(tmp_path, "pro_api_fear_greed",
                            "cmc_fear_greed.parquet"),
        "altcoin_season": _rows(tmp_path, "pro_api_altcoin_season",
                                "cmc_altcoin_season.parquet"),
        "cmc100": _rows(tmp_path, "pro_api_index_historical",
                        "cmc100_index.parquet"),
        "cmc20": _rows(tmp_path, "pro_api_index_historical",
                       "cmc20_index.parquet"),
    }
    assert counts2 == counts1  # second run for the same month adds zero rows
    assert len(r1) == len(r2) == len(DATASETS)
    for a, b in zip(r1, r2):
        assert a["row_count"] == b["row_count"]


def test_maintain_manifests_consistent_and_dirs_split(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(maintain_mod, "CMCClient", FakeClient)
    maintain_mod.maintain(datasets=DATASETS, month="2024-01", base_dir=tmp_path)
    maintain_mod.maintain(datasets=DATASETS, month="2024-01", base_dir=tmp_path)

    # Regression: each index-ish dataset owns its directory (no clobbering).
    assert (tmp_path / "pro_api_fear_greed" / "manifest.json").exists()
    assert (tmp_path / "pro_api_altcoin_season" / "manifest.json").exists()
    assert not (tmp_path / "pro_api_index_historical" / "cmc_fear_greed.parquet").exists()
    assert not (tmp_path / "pro_api_index_historical" / "cmc_altcoin_season.parquet").exists()

    m_fg = read_manifest(tmp_path / "pro_api_fear_greed")
    assert m_fg["dataset"] == "pro_api_fear_greed"
    assert m_fg["row_count"] == _rows(tmp_path, "pro_api_fear_greed",
                                      "cmc_fear_greed.parquet")

    m_as = read_manifest(tmp_path / "pro_api_altcoin_season")
    assert m_as["dataset"] == "pro_api_altcoin_season"
    assert m_as["row_count"] == _rows(tmp_path, "pro_api_altcoin_season",
                                      "cmc_altcoin_season.parquet")

    m_gm = read_manifest(tmp_path / "pro_api_global_metrics_historical")
    assert m_gm["dataset"] == "pro_api_global_metrics_historical"
    assert m_gm["row_count"] == _rows(tmp_path, "pro_api_global_metrics_historical",
                                      "cmc_global_metrics.parquet")

    m100 = read_manifest(tmp_path / "pro_api_index_historical",
                         "cmc100_manifest.json")
    m20 = read_manifest(tmp_path / "pro_api_index_historical",
                        "cmc20_manifest.json")
    assert m100["row_count"] == 3 and m20["row_count"] == 3
    assert m100["coverage_end"] == "2024-01-03"


def test_registry_wires_new_collectors():
    """quotes / fiat_fx / cmc_index are registered for monthly maintenance."""
    for name in ("quotes", "fiat_fx", "cmc_index"):
        assert name in maintain_mod.REGISTRY
        module, _ = maintain_mod.REGISTRY[name]
        assert callable(getattr(module, "run"))
