"""Fiat FX collector tests (T020–T022) — offline, fixture-driven.

Asserts the (fiat_id, snapshot_date) key is unique, provenance fields (source)
are populated, the manifest is written, and a repeat run adds zero rows.
"""
import json
from pathlib import Path

import pandas as pd

from src.cmc.collectors import fiat_fx
from src.cmc.manifest import read_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "fiat_fx_sample.json"


class FakeClient:
    """Serves the recorded /v2/tools/price-conversion fixture."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, path, params=None):
        assert path == "/v2/tools/price-conversion"
        assert (params or {}).get("amount") == 1
        assert (params or {}).get("id") == fiat_fx.USD_ID
        return self._payload


def _write_fiat_map(base_dir: Path) -> None:
    """Minimal cmc_fiat_map.parquet under pro_api_fiat_map/ (real layout)."""
    fdir = base_dir / "pro_api_fiat_map"
    fdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"fiat_id": [2790, 2787], "symbol": ["EUR", "CNY"],
         "name": ["Euro", "Chinese Yuan"]}
    ).to_parquet(fdir / "cmc_fiat_map.parquet", index=False)


def test_fiat_fx_parses_rates_with_provenance(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    _write_fiat_map(tmp_path)

    res = fiat_fx.run(FakeClient(payload), tmp_path)

    df = pd.read_parquet(tmp_path / fiat_fx.DATASET / fiat_fx.FILENAME)
    assert set(["snapshot_date", "fiat_id", "fiat_symbol",
                "usd_to_fiat_rate", "source"]).issubset(df.columns)
    assert len(df) == 2
    assert not df.duplicated(subset=fiat_fx.KEY).any()  # (fiat_id, snapshot_date)

    eur = df[df.fiat_id == 2790].iloc[0]
    assert eur["fiat_symbol"] == "EUR"
    assert eur["usd_to_fiat_rate"] == 0.9231
    assert (df["source"] == fiat_fx.SOURCE).all()  # provenance populated
    assert df["snapshot_date"].str.match(r"\d{4}-\d{2}-\d{2}").all()

    assert res["row_count"] == 2
    m = read_manifest(tmp_path / fiat_fx.DATASET)
    assert m is not None
    assert m["dataset"] == fiat_fx.DATASET
    assert m["symbol_count"] == 2
    assert (tmp_path / fiat_fx.DATASET / "raw_api_samples"
            / "fiat_fx_sample.json").exists()


def test_fiat_fx_run_twice_adds_zero_rows(tmp_path: Path):
    payload = json.loads(FIXTURE.read_text())
    _write_fiat_map(tmp_path)

    r1 = fiat_fx.run(FakeClient(payload), tmp_path)
    r2 = fiat_fx.run(FakeClient(payload), tmp_path)

    assert r1["row_count"] == r2["row_count"] == 2
    df = pd.read_parquet(tmp_path / fiat_fx.DATASET / fiat_fx.FILENAME)
    assert len(df) == 2
