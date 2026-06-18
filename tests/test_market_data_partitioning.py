"""
Phase 6 tests — storage & access layer (Hive partitioning + DuckDB views).

  * MarketDataAgent writes partitioned/symbol=…/year=… IN ADDITION to the flat file.
  * The partitioned dataset round-trips to the same rows as the source.
  * DuckDBEngine.create_market_views registers v_market_ohlcv (+ v_market_members
    when the PIT column is present).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from configs.config import load_config
from agents.market_data_agent import MarketDataAgent
from pipelines.duckdb_engine import DuckDBEngine


def _market_df():
    dates = pd.date_range("2022-12-15", periods=400, freq="D", tz="UTC")  # spans 2022 + 2023
    frames = []
    for sym in ["BTC", "ETH"]:
        frames.append(
            pd.DataFrame(
                {
                    "date_ts": dates,
                    "symbol": sym,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 100.0,
                    "volume": 10.0,
                    "is_universe_member": [True] * 200 + [False] * 200,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_partitioned_write_creates_hive_tree(tmp_path):
    cfg = load_config()
    agent = MarketDataAgent(cfg)
    out_dir = tmp_path / "market"
    out_dir.mkdir(parents=True)
    df = _market_df()
    part_dir = agent._write_partitioned_market(df, out_dir)
    assert part_dir is not None and part_dir.exists()
    # Hive structure: symbol=BTC/year=2022/...
    syms = {p.name for p in part_dir.glob("symbol=*")}
    assert syms == {"symbol=BTC", "symbol=ETH"}
    years = {p.name for p in part_dir.glob("symbol=BTC/year=*")}
    assert "year=2022" in years and "year=2023" in years


def test_partitioned_roundtrip_matches_source(tmp_path):
    cfg = load_config()
    agent = MarketDataAgent(cfg)
    out_dir = tmp_path / "market"
    out_dir.mkdir(parents=True)
    df = _market_df()
    part_dir = agent._write_partitioned_market(df, out_dir)
    con = duckdb.connect(":memory:")
    n = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{(part_dir / '**' / '*.parquet').as_posix()}', hive_partitioning=true)"
    ).fetchone()[0]
    assert n == len(df)


def test_partitioned_write_disabled_returns_none_when_empty(tmp_path):
    cfg = load_config()
    agent = MarketDataAgent(cfg)
    out_dir = tmp_path / "market"
    out_dir.mkdir(parents=True)
    assert agent._write_partitioned_market(pd.DataFrame(), out_dir) is None


def test_create_market_views(tmp_path):
    cfg = load_config()
    cfg["paths"] = dict(cfg["paths"])
    cfg["paths"]["raw"] = str(tmp_path / "raw")
    market_dir = tmp_path / "raw" / "market"
    market_dir.mkdir(parents=True)
    _market_df().to_parquet(market_dir / "market_ohlcv.parquet", index=False)

    engine = DuckDBEngine(cfg)
    created = engine.create_market_views()
    assert "v_market_ohlcv" in created
    assert "v_market_members" in created  # PIT column present
    # The member view returns only is_universe_member=True rows.
    members = engine.query("SELECT COUNT(*) AS n FROM v_market_members")["n"].iloc[0]
    total = engine.query("SELECT COUNT(*) AS n FROM v_market_ohlcv")["n"].iloc[0]
    assert 0 < members < total


def test_create_market_views_noop_without_file(tmp_path):
    cfg = load_config()
    cfg["paths"] = dict(cfg["paths"])
    cfg["paths"]["raw"] = str(tmp_path / "empty_raw")
    engine = DuckDBEngine(cfg)
    assert engine.create_market_views() == []
