"""
Phase 1 integration tests — the survivorship-free universe handoff.

These exercise the *gated* PIT paths that fix the two collapse sites:
  * MarketDataAgent._attach_membership_mask  (market_data.attach_membership_mask)
  * FeatureAgent._apply_membership_filter     (features.membership_mode = pit_daily)

They prove a member (date,symbol) is flagged/kept and a non-member is flagged/dropped,
using a small real daily mask built by scripts/build_membership_daily.py.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from configs.config import load_config
from scripts.build_membership_daily import write_membership_daily


def _make_universe_and_mask(tmp_path: Path) -> Path:
    """Two snapshots; DEAD is a member only in Jan, BTC in both. Returns daily-mask path."""
    rows = []
    members = {
        "2022-01-01": [(1, "BTC"), (2, "DEAD")],
        "2022-02-01": [(1, "BTC"), (3, "NEWC")],
    }
    for snap, mem in members.items():
        for rank, (cid, sym) in enumerate(mem, start=1):
            rows.append(
                {
                    "snapshot_date": pd.Timestamp(snap, tz="UTC"),
                    "cmc_id": cid,
                    "symbol": sym,
                    "name": sym,
                    "market_cap_rank": rank,
                    "market_cap_usd": 1e9 / rank,
                    "is_eligible": True,
                    "source": "test",
                }
            )
    uni = tmp_path / "universe_monthly.parquet"
    pd.DataFrame(rows).to_parquet(uni, index=False)
    mask = tmp_path / "universe_membership_daily.parquet"
    write_membership_daily(uni, mask, end_date="2022-02-28")
    return mask


def test_market_agent_flags_members_and_nonmembers(tmp_path):
    from agents.market_data_agent import MarketDataAgent

    mask_path = _make_universe_and_mask(tmp_path)
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"]["attach_membership_mask"] = True
    cfg["market_data"]["membership_daily_path"] = str(mask_path)

    agent = MarketDataAgent(cfg)
    # Synthetic panel: BTC member on 2022-01-15; DEAD member on 2022-01-15 but NOT on 2022-02-15.
    market = pd.DataFrame(
        {
            "date_ts": pd.to_datetime(
                ["2022-01-15", "2022-01-15", "2022-02-15"], utc=True
            ),
            "symbol": ["BTC", "DEAD", "DEAD"],
            "cmc_id": [1, 2, 2],
            "close": [40000.0, 1.0, 0.5],
            "market_cap": [pd.NA, pd.NA, pd.NA],
        }
    )
    out = agent._attach_membership_mask(market)
    flags = dict(zip(zip(out["symbol"], out["date_ts"].dt.strftime("%Y-%m-%d")), out["is_universe_member"]))
    assert flags[("BTC", "2022-01-15")] is True
    assert flags[("DEAD", "2022-01-15")] is True
    # DEAD left the universe after January → not a member in February.
    assert flags[("DEAD", "2022-02-15")] is False


def test_market_agent_mask_off_by_default(tmp_path):
    from agents.market_data_agent import MarketDataAgent

    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"]["attach_membership_mask"] = False
    agent = MarketDataAgent(cfg)
    market = pd.DataFrame(
        {
            "date_ts": pd.to_datetime(["2022-01-15"], utc=True),
            "symbol": ["BTC"],
            "cmc_id": [1],
            "close": [40000.0],
            "market_cap": [pd.NA],
        }
    )
    out = agent._attach_membership_mask(market)
    # Column exists but is left NA when the mask is disabled (legacy behavior preserved).
    assert "is_universe_member" in out.columns
    assert out["is_universe_member"].isna().all()


def test_feature_agent_pit_filter_keeps_only_members():
    from agents.feature_agent import FeatureAgent

    cfg = load_config()
    agent = FeatureAgent(cfg)
    agent.membership_mode = "pit_daily"
    agent._member_pairs = {
        (pd.Timestamp("2022-01-15", tz="UTC"), "BTC"),
        (pd.Timestamp("2022-01-15", tz="UTC"), "DEAD"),
    }
    df = pd.DataFrame(
        {
            "date_ts": pd.to_datetime(
                ["2022-01-15", "2022-01-15", "2022-02-15"], utc=True
            ),
            "symbol": ["BTC", "DEAD", "DEAD"],
            "value": [1.0, 2.0, 3.0],
        }
    )
    out = agent._apply_membership_filter(df)
    pairs = set(zip(out["symbol"], out["date_ts"].dt.strftime("%Y-%m-%d")))
    assert ("BTC", "2022-01-15") in pairs
    assert ("DEAD", "2022-01-15") in pairs
    assert ("DEAD", "2022-02-15") not in pairs  # non-member day dropped


def test_feature_agent_filter_noop_in_latest_snapshot_mode():
    from agents.feature_agent import FeatureAgent

    cfg = load_config()
    agent = FeatureAgent(cfg)
    agent.membership_mode = "latest_snapshot"
    agent._member_pairs = set()
    df = pd.DataFrame(
        {"date_ts": pd.to_datetime(["2022-01-15"], utc=True), "symbol": ["BTC"], "value": [1.0]}
    )
    out = agent._apply_membership_filter(df)
    assert len(out) == 1  # unchanged
