"""
Phase 2 tests — canonical USD dollar-volume (unit consistency across sources).

The bug: exchange (CCXT) candle volume is in BASE-asset units, but aggregator sources
(CryptoCompare/CoinGecko/CoinPaprika/CMC) report volume already in USD. Computing
`dollar_volume = volume * close` is correct ONLY for base-unit sources; for USD sources
it double-counts price. `dollar_volume_usd` fixes this per source.
"""
from __future__ import annotations

import pandas as pd
import pytest

from agents.market_data_agent import volume_basis_for_source


@pytest.mark.parametrize(
    "source,expected",
    [
        ("ccxt_coinbase", "base"),
        ("ccxt_kraken", "base"),
        ("cryptocompare", "quote_usd"),
        ("coingecko", "quote_usd"),
        ("coinpaprika", "quote_usd"),
        ("coinmarketcap", "quote_usd"),
        ("coincap", "none"),
        ("", "none"),
    ],
)
def test_volume_basis_mapping(source, expected):
    assert volume_basis_for_source(source) == expected


def _dollar_volume_usd(volume, close, basis):
    """Mirror of the agent's per-source rule for an isolated unit check."""
    if basis == "base":
        return volume * close
    if basis == "quote_usd":
        return volume
    return None


def test_base_source_multiplies_by_close():
    # Exchange: 10 BTC traded at $40,000 => $400,000 dollar-volume.
    assert _dollar_volume_usd(10.0, 40000.0, "base") == 400000.0


def test_usd_source_does_not_multiply():
    # CryptoCompare volumeto is already USD ($400,000). Must NOT multiply by close.
    assert _dollar_volume_usd(400000.0, 40000.0, "quote_usd") == 400000.0


def test_usd_source_double_count_would_be_wrong():
    # Demonstrate the bug the fix prevents: naive close*volume on a USD source.
    naive = 400000.0 * 40000.0  # 1.6e10 — absurd
    correct = _dollar_volume_usd(400000.0, 40000.0, "quote_usd")
    assert correct == 400000.0
    assert naive != correct


def test_normalize_emits_dollar_volume_usd_columns():
    """_normalize_asset_frame should stamp volume_basis + dollar_volume_usd."""
    from configs.config import load_config
    from agents.market_data_agent import MarketDataAgent, AssetRequest, CANONICAL_COLUMNS

    cfg = load_config()
    agent = MarketDataAgent(cfg)
    agent.generate_snapshot_id("test")
    # 400 days of synthetic full-OHLC exchange data (passes min_history_days).
    dates = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    raw = pd.DataFrame(
        {
            "date_ts": dates,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 100.0,
            "volume": 10.0,  # base units
        }
    )
    req = AssetRequest(symbol="TEST", coin_id="test", exchange="coinbase", exchange_symbol="TEST/USD", cmc_id=1)
    norm, qa = agent._normalize_asset_frame(
        raw_df=raw, request=req, source_used="ccxt_coinbase",
        requested_start=dates[0], requested_end=dates[-1],
        data_type="exchange_ohlcv", is_full_ohlcv=True,
        exchange_name="coinbase", exchange_symbol="TEST/USD",
    )
    assert "dollar_volume_usd" in CANONICAL_COLUMNS and "volume_basis" in CANONICAL_COLUMNS
    assert (norm["volume_basis"] == "base").all()
    # base source: dollar_volume_usd == volume * close == 10 * 100 == 1000
    assert (pd.to_numeric(norm["dollar_volume_usd"]) == 1000.0).all()
