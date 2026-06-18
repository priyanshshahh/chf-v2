"""
Phase 9 hardening tests.
  #1  content hash covers full OHLCV+volume (not just close)
  #2  anomaly guard catches MODERATE round-trip-to-origin bad prints (old rule missed them)
  #3  winsorized anomaly bars are marked is_synthetic_ohlc (no unflagged fake data)
  #4  membership symbol-fallback is collision-safe (ambiguous tickers excluded)
  #7  volume_scope tag (single_venue vs global)
  #9  stale-price (frozen-feed) detection
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from configs.config import load_config
from agents.market_data_agent import (
    MarketDataAgent, AssetRequest, CANONICAL_COLUMNS, volume_scope_for_source,
)


def _agent(**overrides):
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"].update(overrides)
    a = MarketDataAgent(cfg)
    a.generate_snapshot_id("test")
    return a


def _normalize(agent, closes, highs=None, source="ccxt_coinbase"):
    dates = pd.date_range("2022-01-01", periods=len(closes), freq="D", tz="UTC")
    highs = highs or [c * 1.001 for c in closes]
    raw = pd.DataFrame({"date_ts": dates, "open": closes, "high": highs,
                        "low": [c * 0.999 for c in closes], "close": closes, "volume": 10.0})
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    return agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used=source,
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD")


# ---------- #1 content hash ----------

def test_content_hash_covers_volume_and_ohlc():
    agent = _agent()
    base = pd.DataFrame({"symbol": ["BTC"], "date_ts": pd.to_datetime(["2022-01-01"], utc=True),
                         "open": [100.0], "high": [110.0], "low": [90.0], "close": [105.0], "volume": [5.0]})
    h0 = agent._content_hash(base)
    vol_changed = base.copy(); vol_changed.loc[0, "volume"] = 6.0
    high_changed = base.copy(); high_changed.loc[0, "high"] = 111.0
    assert h0 and len(h0) == 16
    assert agent._content_hash(vol_changed) != h0      # volume-only change now detected
    assert agent._content_hash(high_changed) != h0     # high-only change now detected
    assert agent._content_hash(base.copy()) == h0      # identical → identical


# ---------- #2 / #3 anomaly ----------

def test_moderate_roundtrip_is_flagged():
    # 3x spike that fully reverts — below the old ln(5) both-legs rule, caught by round-trip.
    agent = _agent(min_history_days=5, forward_fill_missing_days=False)
    norm, qa = _normalize(agent, [100, 100, 100, 300, 100, 100, 100, 100])
    flagged = norm[norm["is_price_anomaly"] == True]  # noqa: E712
    assert len(flagged) == 1 and float(flagged["close"].iloc[0]) == 300.0


def test_sustained_move_still_not_flagged():
    agent = _agent(min_history_days=5, forward_fill_missing_days=False)
    norm, qa = _normalize(agent, [100, 100, 100, 300, 300, 300, 300, 300])
    assert (norm["is_price_anomaly"] == False).all()  # noqa: E712


def test_winsorized_anomaly_marked_synthetic():
    agent = _agent(min_history_days=5, forward_fill_missing_days=False, anomaly_policy="winsorize")
    norm, qa = _normalize(agent, [100, 100, 100, 1000, 100, 100, 100, 100])
    # the neutralized bar must be flagged synthetic so range features exclude it
    assert (norm["is_synthetic_ohlc"] == True).any()  # noqa: E712
    assert (pd.to_numeric(norm["close"]) == 1000.0).sum() == 0


# ---------- #7 volume_scope ----------

def test_volume_scope_mapping_and_stamp():
    assert volume_scope_for_source("ccxt_kraken") == "single_venue"
    assert volume_scope_for_source("cryptocompare") == "global"
    assert volume_scope_for_source("") == "unknown"
    assert "volume_scope" in CANONICAL_COLUMNS
    agent = _agent(min_history_days=5, forward_fill_missing_days=False)
    norm, qa = _normalize(agent, [100.0] * 8)
    assert (norm["volume_scope"] == "single_venue").all()


# ---------- #9 stale-price ----------

def test_stale_price_flagged_on_frozen_feed():
    agent = _agent(min_history_days=5, forward_fill_missing_days=False, max_flat_close_days=10)
    # 20 identical REAL closes → frozen feed
    norm, qa = _normalize(agent, [100.0] * 20)
    assert "is_stale_price" in CANONICAL_COLUMNS
    assert (norm["is_stale_price"] == True).any()  # noqa: E712


def test_short_flat_run_not_stale():
    agent = _agent(min_history_days=5, forward_fill_missing_days=False, max_flat_close_days=10)
    # 5 identical then varied → run length 5 < 10, not stale
    norm, qa = _normalize(agent, [100, 100, 100, 100, 100, 101, 102, 103, 104, 105])
    assert (norm["is_stale_price"] == False).all()  # noqa: E712


# ---------- #4 collision-safe membership ----------

def test_membership_symbol_fallback_is_collision_safe(tmp_path):
    # Mask where ticker DUP maps to TWO cmc_ids on the same date; UNI maps to one.
    d = pd.Timestamp("2022-01-15", tz="UTC")
    mask = pd.DataFrame({
        "date_ts": [d, d, d],
        "cmc_id": [1, 2, 3],
        "symbol": ["DUP", "DUP", "UNI"],
        "market_cap_rank": [1, 2, 3],
        "market_cap_usd": [9e11, 8e11, 7e11],
    })
    mpath = tmp_path / "universe_membership_daily.parquet"
    mask.to_parquet(mpath, index=False)
    agent = _agent(attach_membership_mask=True, membership_daily_path=str(mpath))
    market = pd.DataFrame({
        "date_ts": [d, d],
        "symbol": ["DUP", "UNI"],
        "cmc_id": [pd.NA, pd.NA],   # force the symbol fallback path
        "close": [1.0, 2.0], "market_cap": [pd.NA, pd.NA],
    })
    out = agent._attach_membership_mask(market)
    flags = dict(zip(out["symbol"], out["is_universe_member"]))
    assert flags["DUP"] == False   # ambiguous ticker NOT credited via symbol fallback
    assert flags["UNI"] == True    # unambiguous ticker is
