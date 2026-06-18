"""
FeatureAgent production-hardening tests: the survivorship-free pit_daily path (previously
untested), the content hash, and the is_forward_filled latent-crash fix.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from configs.config import load_config
from agents.feature_agent import FeatureAgent


def _agent(tmp_path: Path, **fcfg_overrides) -> FeatureAgent:
    cfg = copy.deepcopy(load_config())
    cfg["_project_root"] = str(tmp_path)
    cfg["features"] = {**cfg["features"], **fcfg_overrides}
    cfg["mlflow"] = {**cfg.get("mlflow", {}), "log_feature_run": False}
    return FeatureAgent(cfg)


# ---------- pit_daily masking ----------

def test_membership_filter_keeps_only_member_pairs(tmp_path):
    agent = _agent(tmp_path)
    agent.membership_mode = "pit_daily"
    agent._member_pairs = {
        (pd.Timestamp("2026-03-01", tz="UTC"), "BTC"),
        (pd.Timestamp("2026-03-02", tz="UTC"), "ETH"),
    }
    df = pd.DataFrame({
        "date_ts": pd.to_datetime(["2026-03-01", "2026-03-01", "2026-03-02", "2026-03-02"], utc=True),
        "symbol": ["BTC", "ETH", "BTC", "ETH"],
        "log_ret_1d": [0.1, 0.2, 0.3, 0.4],
    })
    out = agent._apply_membership_filter(df)
    pairs = set(zip(out["symbol"], out["date_ts"].dt.strftime("%Y-%m-%d")))
    assert pairs == {("BTC", "2026-03-01"), ("ETH", "2026-03-02")}  # only members kept


def test_membership_filter_noop_in_latest_snapshot(tmp_path):
    agent = _agent(tmp_path)
    agent.membership_mode = "latest_snapshot"
    agent._member_pairs = set()
    df = pd.DataFrame({"date_ts": pd.to_datetime(["2026-03-01"], utc=True), "symbol": ["BTC"], "x": [1.0]})
    assert len(agent._apply_membership_filter(df)) == 1  # unchanged


def test_load_pit_membership_builds_union_and_pairs(tmp_path):
    # daily mask with a dead coin only on day 1
    mdir = tmp_path / "data" / "raw" / "universe"
    mdir.mkdir(parents=True)
    mask = pd.DataFrame({
        "date_ts": pd.to_datetime(["2026-03-01", "2026-03-01", "2026-03-02"], utc=True),
        "symbol": ["BTC", "DEAD", "BTC"],
    })
    mask.to_parquet(mdir / "universe_membership_daily.parquet", index=False)
    agent = _agent(tmp_path, membership_mode="pit_daily")
    symbols, snap = agent._load_pit_membership(pd.DataFrame())
    assert set(symbols) == {"BTC", "DEAD"}                # union incl. dead coin
    assert (pd.Timestamp("2026-03-01", tz="UTC"), "DEAD") in agent._member_pairs
    assert (pd.Timestamp("2026-03-02", tz="UTC"), "DEAD") not in agent._member_pairs  # dead coin bounded
    assert snap.startswith("pit_daily:")


def test_pit_membership_missing_mask_raises_when_required(tmp_path):
    agent = _agent(tmp_path, membership_mode="pit_daily", require_pit_membership=True)
    with pytest.raises(Exception):
        agent._load_pit_membership(pd.DataFrame())


def test_pit_membership_missing_mask_falls_back_when_not_required(tmp_path):
    # universe with a latest snapshot to fall back to
    agent = _agent(tmp_path, membership_mode="pit_daily", require_pit_membership=False)
    universe = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-03-01", "2026-03-01"], utc=True),
        "is_eligible": [True, True], "symbol": ["BTC", "ETH"],
        "market_cap_rank": [1, 2], "snapshot_id": ["u1", "u1"],
    })
    symbols, _ = agent._load_pit_membership(universe)
    assert set(symbols) == {"BTC", "ETH"}
    assert agent.membership_mode == "latest_snapshot"     # fell back


# ---------- content hash ----------

def test_content_hash_deterministic_and_order_independent(tmp_path):
    agent = _agent(tmp_path)
    df = pd.DataFrame({
        "symbol": ["BTC", "ETH"], "date_ts": pd.to_datetime(["2026-03-01", "2026-03-01"], utc=True),
        "log_ret_1d": [0.1, 0.2], "feature_set": "full", "feature_version": "v",
        "snapshot_id": "s", "run_id": "r", "created_at_utc": "t",
    })
    h1 = agent._content_hash(df)
    h2 = agent._content_hash(df.iloc[::-1].reset_index(drop=True))
    assert h1 and len(h1) == 16 and h1 == h2
    changed = df.copy(); changed.loc[0, "log_ret_1d"] = 0.11
    assert agent._content_hash(changed) != h1


# ---------- is_forward_filled latent-crash fix ----------

def test_market_features_build_without_is_forward_filled_column(tmp_path):
    agent = _agent(tmp_path)
    agent.snapshot_id = "s"
    dates = pd.date_range("2026-01-01", periods=60, freq="D", tz="UTC")
    rows = []
    for sym in ["BTC", "ALT"]:
        rows.append(pd.DataFrame({
            "date_ts": dates, "symbol": sym,
            "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0 + np.arange(60), "volume": 10.0,
        }))
    market = pd.concat(rows, ignore_index=True)
    # No is_forward_filled column → previously crashed with AttributeError on the scalar default.
    out = agent._build_market_features(market)
    assert "is_forward_filled_market" in out.columns
    assert (out["is_forward_filled_market"] == False).all()  # noqa: E712
