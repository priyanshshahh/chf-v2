"""Manifest round-trip: coverage + counts fields persist and re-read equal."""
from pathlib import Path

from src.cmc.manifest import read_manifest, write_manifest


def test_manifest_round_trip(tmp_path: Path):
    write_manifest(
        out_dir=tmp_path,
        dataset="pro_api_global_metrics_historical",
        source="pro_api:/v1/global-metrics/quotes/historical",
        coverage_start="2023-07-02",
        coverage_end="2026-07-01",
        row_count=1096,
        symbol_count=None,
        generated_by="src/cmc/collectors/global_metrics.py",
        run={"achieved_full_history": False, "notes": "plan-limited"},
    )

    m = read_manifest(tmp_path)
    assert m is not None
    assert m["dataset"] == "pro_api_global_metrics_historical"
    assert m["coverage_start"] == "2023-07-02"
    assert m["coverage_end"] == "2026-07-01"
    assert m["row_count"] == 1096
    assert m["symbol_count"] is None
    assert m["run"]["achieved_full_history"] is False
    assert "generated_at_utc" in m


def test_read_missing_manifest_returns_none(tmp_path: Path):
    assert read_manifest(tmp_path / "does_not_exist") is None
