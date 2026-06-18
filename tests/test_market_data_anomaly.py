"""
Phase 5 tests — price-outlier / anomaly guard.

Key property: only a SPIKE-AND-REVERT round-trip is flagged (bad print). A legitimate
large sustained move is NOT flagged, so real crypto rallies/crashes survive. Synthetic
forward-filled bars are never flagged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from configs.config import load_config
from agents.market_data_agent import MarketDataAgent, AssetRequest, CANONICAL_COLUMNS


def _agent(policy="flag_only"):
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"]["anomaly_policy"] = policy
    cfg["market_data"]["forward_fill_missing_days"] = False  # isolate anomaly logic
    cfg["market_data"]["min_history_days"] = 5
    agent = MarketDataAgent(cfg)
    agent.generate_snapshot_id("test")
    return agent


def _normalize(agent, closes):
    dates = pd.date_range("2022-01-01", periods=len(closes), freq="D", tz="UTC")
    raw = pd.DataFrame(
        {
            "date_ts": dates,
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": 10.0,
        }
    )
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    return agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used="ccxt_coinbase",
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD",
    )


def test_spike_and_revert_is_flagged():
    agent = _agent()
    # Day 3 spikes 100 -> 1000 (10x) then reverts to 100 — classic bad print.
    closes = [100, 100, 100, 1000, 100, 100, 100, 100]
    norm, qa = _normalize(agent, closes)
    assert "is_price_anomaly" in CANONICAL_COLUMNS
    flagged = norm[norm["is_price_anomaly"] == True]  # noqa: E712
    assert len(flagged) == 1
    assert float(flagged["close"].iloc[0]) == 1000.0


def test_sustained_move_is_not_flagged():
    agent = _agent()
    # A real 10x rally that STAYS up — must NOT be flagged (no revert).
    closes = [100, 100, 100, 1000, 1000, 1000, 1000, 1000]
    norm, qa = _normalize(agent, closes)
    assert (norm["is_price_anomaly"] == False).all()  # noqa: E712


def test_normal_data_has_no_anomalies():
    agent = _agent()
    closes = [100, 102, 101, 103, 105, 104, 106, 108]
    norm, qa = _normalize(agent, closes)
    assert (norm["is_price_anomaly"] == False).all()  # noqa: E712
    assert qa["passed_qa"], qa["failure_reason"]


def test_drop_policy_removes_anomaly():
    agent = _agent(policy="drop")
    closes = [100, 100, 100, 1000, 100, 100, 100, 100]
    norm, qa = _normalize(agent, closes)
    assert (norm["is_price_anomaly"] == False).all()  # noqa: E712
    assert (pd.to_numeric(norm["close"]) == 1000.0).sum() == 0  # the spike row is gone


def test_winsorize_policy_neutralizes_spike():
    agent = _agent(policy="winsorize")
    closes = [100, 100, 100, 1000, 100, 100, 100, 100]
    norm, qa = _normalize(agent, closes)
    # The spike close is replaced by the prior close (100); no 1000 remains.
    assert (pd.to_numeric(norm["close"]) == 1000.0).sum() == 0
    assert qa["passed_qa"], qa["failure_reason"]
