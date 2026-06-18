"""
Research-integrity tests for the daily universe membership builder
(scripts/build_membership_daily.py).

Guards:
  * No look-ahead: membership on date d derives only from a snapshot_date <= d.
  * Forward-hold continuity: every day in the span is covered exactly once per member.
  * Survivorship-free: a coin present in early snapshots but dropped later has a
    BOUNDED daily window (it does not leak into months after it left the top-N).
  * Uniqueness: no duplicate (date_ts, cmc_id).
  * end_date controls only the FINAL snapshot's hold window.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.build_membership_daily import (
    MembershipBuildError,
    build_membership_daily,
    write_membership_daily,
)


def _make_universe_monthly(tmp_path: Path) -> Path:
    """Three monthly snapshots; DEAD drops out after the 2nd month, NEW appears in the 3rd."""
    rows = []
    snaps = ["2021-01-01", "2021-02-01", "2021-03-01"]
    members = {
        "2021-01-01": [(1, "BTC"), (2, "ETH"), (3, "DEAD")],
        "2021-02-01": [(1, "BTC"), (2, "ETH"), (3, "DEAD")],
        "2021-03-01": [(1, "BTC"), (2, "ETH"), (4, "NEW")],
    }
    for snap in snaps:
        for rank, (cmc_id, sym) in enumerate(members[snap], start=1):
            rows.append(
                {
                    "snapshot_date": pd.Timestamp(snap, tz="UTC"),
                    "cmc_id": cmc_id,
                    "symbol": sym,
                    "name": sym.title(),
                    "market_cap_rank": rank,
                    "market_cap_usd": 1e9 / rank,
                    "is_eligible": True,
                    "source": "test",
                }
            )
    df = pd.DataFrame(rows)
    path = tmp_path / "universe_monthly.parquet"
    df.to_parquet(path, index=False)
    return path


def test_no_lookahead(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    m = build_membership_daily(uni, end_date="2021-03-31")
    assert (m["snapshot_date"] <= m["date_ts"]).all(), "membership must never derive from a future snapshot"


def test_forward_hold_continuity_and_member_count(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    m = build_membership_daily(uni, end_date="2021-03-31")
    per_day = m.groupby("date_ts")["cmc_id"].nunique()
    # Jan 1 -> Mar 31 inclusive = 90 days, 3 members each day.
    assert per_day.min() == 3 and per_day.max() == 3
    assert per_day.index.min() == pd.Timestamp("2021-01-01", tz="UTC")
    assert per_day.index.max() == pd.Timestamp("2021-03-31", tz="UTC")


def test_survivorship_dead_coin_is_bounded(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    m = build_membership_daily(uni, end_date="2021-03-31")
    dead = m[m["symbol"] == "DEAD"]
    # DEAD is a member only in Jan + Feb, never in March.
    assert dead["date_ts"].min() == pd.Timestamp("2021-01-01", tz="UTC")
    assert dead["date_ts"].max() == pd.Timestamp("2021-02-28", tz="UTC")
    assert (dead["date_ts"] >= pd.Timestamp("2021-03-01", tz="UTC")).sum() == 0
    # NEW appears only in March.
    new = m[m["symbol"] == "NEW"]
    assert new["date_ts"].min() == pd.Timestamp("2021-03-01", tz="UTC")


def test_no_duplicate_date_cmcid(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    m = build_membership_daily(uni, end_date="2021-03-31")
    assert not m.duplicated(subset=["date_ts", "cmc_id"]).any()


def test_interior_window_independent_of_end_date(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    m_short = build_membership_daily(uni, end_date="2021-03-05")
    m_long = build_membership_daily(uni, end_date="2021-03-31")
    # Jan/Feb (interior) windows are identical regardless of end_date.
    janfeb_short = m_short[m_short["date_ts"] < pd.Timestamp("2021-03-01", tz="UTC")]
    janfeb_long = m_long[m_long["date_ts"] < pd.Timestamp("2021-03-01", tz="UTC")]
    assert len(janfeb_short) == len(janfeb_long)
    # Only the final (March) window length differs.
    assert m_short["date_ts"].max() == pd.Timestamp("2021-03-05", tz="UTC")
    assert m_long["date_ts"].max() == pd.Timestamp("2021-03-31", tz="UTC")


def test_write_membership_daily_manifest(tmp_path):
    uni = _make_universe_monthly(tmp_path)
    out = tmp_path / "universe_membership_daily.parquet"
    manifest = write_membership_daily(uni, out, end_date="2021-03-31")
    assert out.exists()
    assert manifest["unique_cmc_id"] == 4
    assert manifest["snapshot_count"] == 3
    assert manifest["no_lookahead"] is True
    assert (tmp_path / "membership_daily_manifest.json").exists()


def test_empty_universe_raises(tmp_path):
    empty = tmp_path / "empty.parquet"
    pd.DataFrame({"snapshot_date": [], "cmc_id": [], "symbol": []}).to_parquet(empty, index=False)
    with pytest.raises(MembershipBuildError):
        build_membership_daily(empty)
