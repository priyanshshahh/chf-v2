"""
Phase 8 tests — remaining register items.
  E: price_basis tagging (venue_close vs composite_index)
  F: long_gap_policy=segment_and_flag keeps the most-recent contiguous segment + flags it
  G: min_history_days_policy=membership_aware lowers the floor for short-lived coins
  J: market_cap VALUE (not just rank) filled from the daily membership mask
"""
from __future__ import annotations

import pandas as pd
import pytest

from configs.config import load_config
from agents.market_data_agent import (
    MarketDataAgent,
    AssetRequest,
    CANONICAL_COLUMNS,
    price_basis_for_source,
    _largest_segment_after_long_gap,
)
from scripts.build_membership_daily import write_membership_daily


# ---------- E: price_basis ----------

@pytest.mark.parametrize(
    "source,expected",
    [
        ("ccxt_coinbase", "venue_close"),
        ("ccxt_kraken", "venue_close"),
        ("cryptocompare", "composite_index"),
        ("coingecko", "composite_index"),
        ("coinmarketcap", "composite_index"),
        ("", "unknown"),
    ],
)
def test_price_basis_mapping(source, expected):
    assert price_basis_for_source(source) == expected
    assert "price_basis" in CANONICAL_COLUMNS


def _agent(**overrides):
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    cfg["market_data"].update(overrides)
    a = MarketDataAgent(cfg)
    a.generate_snapshot_id("test")
    return a


def _normalize(agent, closes, source="ccxt_coinbase"):
    dates = pd.date_range("2022-01-01", periods=len(closes), freq="D", tz="UTC")
    # A real exchange simply omits missing days → build raw from non-gap rows only.
    recs = [(d, c) for d, c in zip(dates, closes) if c is not None]
    rdates = [d for d, _ in recs]
    rcloses = [float(c) for _, c in recs]
    raw = pd.DataFrame(
        {"date_ts": rdates, "open": rcloses, "high": [c * 1.001 for c in rcloses],
         "low": [c * 0.999 for c in rcloses], "close": rcloses, "volume": 10.0}
    )
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    return agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used=source,
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD",
    )


def test_price_basis_stamped_on_output():
    agent = _agent(min_history_days=5, forward_fill_missing_days=False)
    norm, qa = _normalize(agent, [100.0] * 10)
    assert (norm["price_basis"] == "venue_close").all()


# ---------- F: segment_and_flag ----------

def test_segment_helper_keeps_after_last_long_gap():
    # present(2), gap of 5 (>3), present(4): cutoff should land after the gap.
    missing = [False, False] + [True] * 5 + [False] * 4
    cutoff = _largest_segment_after_long_gap(missing, max_allowed_gap=3)
    assert cutoff == 7  # index where the post-gap segment begins


def test_long_gap_reject_is_default():
    agent = _agent(min_history_days=5, forward_fill_missing_days=True, max_forward_fill_gap_days=3)
    # 100 days, then a 10-day hole, then 100 days → default rejects the whole asset.
    closes = [100.0] * 100 + [None] * 10 + [100.0] * 100
    norm, qa = _normalize(agent, closes)
    assert norm.empty
    assert "missing_gap_exceeds" in qa["failure_reason"]


def test_segment_and_flag_keeps_recent_segment():
    agent = _agent(min_history_days=5, forward_fill_missing_days=True,
                   max_forward_fill_gap_days=3, long_gap_policy="segment_and_flag")
    closes = [100.0] * 100 + [None] * 10 + [200.0] * 100
    norm, qa = _normalize(agent, closes)
    assert not norm.empty
    assert (norm["has_long_gap"] == True).all()  # noqa: E712
    # Only the post-gap segment survives (close ~200), not the pre-gap (~100).
    assert (pd.to_numeric(norm["close"]) == 200.0).all()


# ---------- G: membership_aware floor ----------

def test_membership_aware_floor_keeps_short_history():
    # 120 days only — below the absolute 365 floor, above the 90 membership floor.
    closes = [100.0] * 120
    rejected = _agent(min_history_days=365, forward_fill_missing_days=False)
    norm_r, qa_r = _normalize(rejected, closes)
    assert norm_r.empty  # absolute policy rejects

    kept = _agent(min_history_days=365, forward_fill_missing_days=False,
                  min_history_days_policy="membership_aware", min_history_days_floor=90)
    norm_k, qa_k = _normalize(kept, closes)
    assert not norm_k.empty  # membership_aware keeps it


# ---------- J: market_cap value fill ----------

def test_market_cap_value_filled_from_mask(tmp_path):
    rows = [{
        "snapshot_date": pd.Timestamp("2022-01-01", tz="UTC"),
        "cmc_id": 1, "symbol": "BTC", "name": "Bitcoin",
        "market_cap_rank": 1, "market_cap_usd": 8.0e11,
        "is_eligible": True, "source": "test",
    }]
    uni = tmp_path / "universe_monthly.parquet"
    pd.DataFrame(rows).to_parquet(uni, index=False)
    mask = tmp_path / "universe_membership_daily.parquet"
    write_membership_daily(uni, mask, end_date="2022-01-31")

    agent = _agent(attach_membership_mask=True, membership_daily_path=str(mask))
    market = pd.DataFrame({
        "date_ts": pd.to_datetime(["2022-01-15"], utc=True),
        "symbol": ["BTC"], "cmc_id": [1], "close": [40000.0], "market_cap": [pd.NA],
    })
    out = agent._attach_membership_mask(market)
    assert float(out["market_cap"].iloc[0]) == 8.0e11   # value filled, not just rank
    assert int(out["market_cap_rank"].iloc[0]) == 1
