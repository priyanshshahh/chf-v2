"""Idempotency of storage.upsert (SC-004): same frame applied twice → equal rows."""
from pathlib import Path

import pandas as pd
import pytest

from src.cmc.storage import upsert


def _frame():
    return pd.DataFrame(
        {
            "cmc_id": [1, 1, 1027],
            "date": ["2024-01-01", "2024-01-02", "2024-01-01"],
            "price": [42000.0, 43000.0, 2300.0],
        }
    )


def test_upsert_twice_same_row_count(tmp_path: Path):
    path = tmp_path / "quotes.parquet"
    key = ["cmc_id", "date"]

    n1 = upsert(_frame(), key, path)
    first_bytes = path.read_bytes()

    n2 = upsert(_frame(), key, path)
    second_bytes = path.read_bytes()

    assert n1 == 3
    assert n2 == 3
    # Same input applied twice ⇒ identical file, identical row count.
    assert first_bytes == second_bytes
    assert len(pd.read_parquet(path)) == 3


def test_upsert_keep_last_corrects_values(tmp_path: Path):
    path = tmp_path / "quotes.parquet"
    key = ["cmc_id", "date"]
    upsert(_frame(), key, path)

    corrected = pd.DataFrame(
        {"cmc_id": [1], "date": ["2024-01-01"], "price": [99999.0]}
    )
    upsert(corrected, key, path)

    out = pd.read_parquet(path)
    assert len(out) == 3  # no new row, value corrected
    val = out[(out.cmc_id == 1) & (out.date == "2024-01-01")].price.iloc[0]
    assert val == 99999.0


def test_upsert_schema_mismatch_raises(tmp_path: Path):
    path = tmp_path / "quotes.parquet"
    key = ["cmc_id", "date"]
    upsert(_frame(), key, path)

    bad = _frame().drop(columns=["price"])
    bad["volume"] = [1.0, 2.0, 3.0]
    with pytest.raises(ValueError) as ei:
        upsert(bad, key, path)
    # Error lists both the added and the missing columns.
    assert "volume" in str(ei.value)
    assert "price" in str(ei.value)
    # File untouched by the failed upsert.
    assert list(pd.read_parquet(path).columns) == ["cmc_id", "date", "price"]


def test_upsert_allows_identical_columns_any_order(tmp_path: Path):
    path = tmp_path / "quotes.parquet"
    key = ["cmc_id", "date"]
    upsert(_frame(), key, path)

    reordered = _frame()[["price", "date", "cmc_id"]]
    assert upsert(reordered, key, path) == 3
