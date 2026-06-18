"""
Phase 3 tests — synthetic forward-filled OHLC integrity ("no fake data").

Forward-filled gap days carry a fabricated bar (high=low=close). These must be:
  * flagged is_synthetic_ohlc=True (and is_forward_filled=True),
  * retain a legitimate carried-forward close,
  * counted in forward_filled_days,
and on the feature side excluded from range/ATR features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from configs.config import load_config
from agents.market_data_agent import MarketDataAgent, AssetRequest, CANONICAL_COLUMNS


def _agent_with_fill(gap_days_allowed=3):
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"]["forward_fill_missing_days"] = True
    cfg["market_data"]["max_forward_fill_gap_days"] = gap_days_allowed
    cfg["market_data"]["min_history_days"] = 365
    agent = MarketDataAgent(cfg)
    agent.generate_snapshot_id("test")
    return agent


def _raw_with_gap():
    # 400 contiguous days, then drop 2 interior days to force a forward-fill gap.
    dates = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "date_ts": dates,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 10.0,
        }
    )
    # remove 2022-03-01 and 2022-03-02 → a 2-day gap that gets forward-filled
    drop = df["date_ts"].isin(pd.to_datetime(["2022-03-01", "2022-03-02"], utc=True))
    return df[~drop].reset_index(drop=True), dates


def test_synthetic_ohlc_flagged_and_close_carried():
    agent = _agent_with_fill()
    raw, dates = _raw_with_gap()
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    norm, qa = agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used="ccxt_coinbase",
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD",
    )
    assert "is_synthetic_ohlc" in CANONICAL_COLUMNS
    assert qa["passed_qa"], qa["failure_reason"]
    synth = norm[norm["is_synthetic_ohlc"] == True]  # noqa: E712
    # The 2 filled gap days are synthetic.
    assert len(synth) == 2
    assert (synth["is_forward_filled"] == True).all()  # noqa: E712
    # Close is carried (positive), volume zeroed on synthetic days.
    assert (pd.to_numeric(synth["close"]) > 0).all()
    assert (pd.to_numeric(synth["volume"]) == 0).all()
    # Real (non-synthetic) rows are the vast majority.
    assert (norm["is_synthetic_ohlc"] == False).sum() >= 398  # noqa: E712


def test_no_synthetic_when_no_gaps():
    agent = _agent_with_fill()
    dates = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    raw = pd.DataFrame(
        {"date_ts": dates, "open": 100.0, "high": 105.0, "low": 98.0, "close": 100.0, "volume": 10.0}
    )
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    norm, qa = agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used="ccxt_coinbase",
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD",
    )
    assert (norm["is_synthetic_ohlc"] == False).all()  # noqa: E712


def test_feature_range_excludes_synthetic_bars():
    """hl_range_pct / atr_proxy must be NA on synthetic bars (no fake range)."""
    from agents.feature_agent import FeatureAgent

    cfg = load_config()
    agent = FeatureAgent(cfg)
    dates = pd.date_range("2022-01-01", periods=60, freq="D", tz="UTC")
    market = pd.DataFrame(
        {
            "date_ts": dates,
            "symbol": "TEST",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 100.0,
            "volume": 10.0,
            "is_forward_filled": [False] * 30 + [True] + [False] * 29,
            "is_synthetic_ohlc": [False] * 30 + [True] + [False] * 29,
        }
    )
    feats = agent._build_market_features(market)
    synth_row = feats[feats["date_ts"] == dates[30]]
    assert synth_row["hl_range_pct"].isna().all(), "synthetic bar must not contribute real range"
