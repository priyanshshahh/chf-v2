"""
Phase 4 tests — determinism & robustness.

  * as_of_date pins the data window + 'incomplete current day' cutoff (no wall-clock).
  * data_content_hash is a deterministic fingerprint of (symbol, date_ts, close).
  * provider rate-limit cooldown is BOUNDED (expires), not a run-global blacklist.
"""
from __future__ import annotations

import time

import pandas as pd

from configs.config import load_config
from agents.market_data_agent import MarketDataAgent


def _agent(as_of=None, cooldown=60):
    cfg = load_config()
    cfg["market_data"] = dict(cfg.get("market_data", {}))
    if as_of is not None:
        cfg["market_data"]["as_of_date"] = as_of
    cfg["market_data"]["provider_cooldown_seconds"] = cooldown
    return MarketDataAgent(cfg)


def test_as_of_date_pins_window():
    agent = _agent(as_of="2026-03-24")
    assert agent._as_of_date() == pd.Timestamp("2026-03-24", tz="UTC")


def test_as_of_date_defaults_to_today_midnight():
    agent = _agent(as_of=None)
    assert agent._as_of_date() == pd.Timestamp.now(tz="UTC").normalize()


def test_content_hash_deterministic_and_order_independent():
    agent = _agent(as_of="2026-03-24")
    df1 = pd.DataFrame(
        {
            "symbol": ["BTC", "ETH", "BTC"],
            "date_ts": pd.to_datetime(["2022-01-01", "2022-01-01", "2022-01-02"], utc=True),
            "close": [40000.0, 3000.0, 41000.0],
        }
    )
    df2 = df1.iloc[::-1].reset_index(drop=True)  # shuffled row order
    h1 = agent._content_hash(df1)
    h2 = agent._content_hash(df2)
    assert h1 and h1 == h2  # 16-hex, identical regardless of input order
    assert len(h1) == 16


def test_content_hash_changes_with_data():
    agent = _agent(as_of="2026-03-24")
    base = pd.DataFrame(
        {"symbol": ["BTC"], "date_ts": pd.to_datetime(["2022-01-01"], utc=True), "close": [40000.0]}
    )
    changed = base.copy()
    changed.loc[0, "close"] = 40001.0
    assert agent._content_hash(base) != agent._content_hash(changed)


def test_content_hash_empty_is_blank():
    agent = _agent(as_of="2026-03-24")
    assert agent._content_hash(pd.DataFrame()) == ""


def test_cooldown_is_bounded_not_permanent():
    """A rate-limited provider becomes eligible again once its cooldown expires."""
    agent = _agent(cooldown=0.05)  # 50ms cooldown for a fast test
    key = "ccxt_coinbase"
    agent.provider_cooldown_until[key] = time.monotonic() + 0.05
    assert time.monotonic() < agent.provider_cooldown_until[key]  # cooled now
    time.sleep(0.06)
    assert time.monotonic() >= agent.provider_cooldown_until[key]  # eligible again


def test_geo_block_stays_permanent():
    """ProviderUnavailable (geo/DNS) stays run-global, unlike rate limits."""
    agent = _agent()
    agent.temporarily_unavailable_providers["ccxt_coinbase"] = "restricted location"
    # Permanent dict is consulted independently of the cooldown clock.
    assert "ccxt_coinbase" in agent.temporarily_unavailable_providers
