"""Offline tests for the papertrade package (no network, no real config)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

import papertrade.engine as engine
from papertrade.alpaca_broker import AlpacaPaperBroker, AlpacaUnavailableError
from papertrade.broker import Fill
from papertrade.engine import BookConfig, load_book_configs, load_target_weights, run_papertrade
from papertrade.simulator import InternalSimulatorBroker

PRICES = {"BTC": 100.0, "ETH": 10.0, "SOL": 5.0}


# -- fixtures / helpers --------------------------------------------------------


def _write_alloc_parquet(path: Path, rows) -> Path:
    df = pd.DataFrame(rows)
    df["execution_date"] = pd.to_datetime(df["execution_date"], utc=True)
    df["date_ts"] = df["execution_date"]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def _write_config(tmp_path: Path, books: list) -> Path:
    cfg = {
        "papertrade": {
            "output_dir": str(tmp_path / "papertrade_out"),
            "broker": "simulator",
            "books": books,
        }
    }
    cfg_path = tmp_path / "run_config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    return cfg_path


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Default price stub: any test hitting fetch_prices gets fixed marks."""
    monkeypatch.setattr(engine, "fetch_prices", lambda symbols: dict(PRICES))


# -- simulator: fill math ------------------------------------------------------


def test_simulator_buy_fill_math(tmp_path):
    broker = InternalSimulatorBroker(tmp_path / "book", starting_cash=100_000.0, cost_bps=20.0)
    fills = broker.submit_target_weights({"BTC": 0.5, "ETH": 0.3}, PRICES, "2026-07-01")
    by_symbol = {f.symbol: f for f in fills}
    assert set(by_symbol) == {"BTC", "ETH"}
    assert by_symbol["BTC"].side == "buy"
    assert by_symbol["BTC"].notional == pytest.approx(50_000.0)
    assert by_symbol["BTC"].qty == pytest.approx(500.0)
    # cost = 20 bps of traded notional
    assert by_symbol["BTC"].cost_usd == pytest.approx(50_000.0 * 20 / 10_000.0)
    assert by_symbol["ETH"].cost_usd == pytest.approx(30_000.0 * 20 / 10_000.0)
    expected_cash = 100_000.0 - 80_000.0 - (100.0 + 60.0)
    assert broker.get_cash() == pytest.approx(expected_cash)
    assert broker.get_positions() == pytest.approx({"BTC": 500.0, "ETH": 3000.0})


def test_simulator_sells_before_buys_and_liquidates_dropped(tmp_path):
    broker = InternalSimulatorBroker(tmp_path / "book", starting_cash=100_000.0, cost_bps=20.0)
    broker.submit_target_weights({"BTC": 0.8}, PRICES, "2026-07-01")
    fills = broker.submit_target_weights({"ETH": 0.5}, PRICES, "2026-07-02")
    sides = [f.side for f in fills]
    assert sides == ["sell", "buy"], "sells must execute before buys"
    assert fills[0].symbol == "BTC" and fills[0].notional < 0
    assert fills[1].symbol == "ETH" and fills[1].notional > 0
    # dropped symbol fully liquidated
    assert "BTC" not in broker.get_positions()


def test_simulator_sell_delta_math(tmp_path):
    broker = InternalSimulatorBroker(tmp_path / "book", starting_cash=100_000.0, cost_bps=20.0)
    broker.submit_target_weights({"BTC": 0.6}, PRICES, "2026-07-01")
    # NAV after costs: 100000 - 60000*0.002 = 99880; target BTC 0.2 => 19976
    fills = broker.submit_target_weights({"BTC": 0.2}, PRICES, "2026-07-02")
    assert len(fills) == 1
    fill = fills[0]
    assert fill.side == "sell"
    assert fill.notional == pytest.approx(0.2 * 99_880.0 - 60_000.0)
    assert fill.cost_usd == pytest.approx(abs(fill.notional) * 20 / 10_000.0)


def test_simulator_rejects_weights_sum_gt_one(tmp_path):
    broker = InternalSimulatorBroker(tmp_path / "book", starting_cash=100_000.0)
    with pytest.raises(ValueError, match="weights_sum_gt_1"):
        broker.submit_target_weights({"BTC": 0.7, "ETH": 0.5}, PRICES, "2026-07-01")


def test_simulator_missing_price_raises(tmp_path):
    broker = InternalSimulatorBroker(tmp_path / "book", starting_cash=100_000.0)
    with pytest.raises(ValueError, match="missing_price"):
        broker.submit_target_weights({"XXX": 0.5}, PRICES, "2026-07-01")
    broker.submit_target_weights({"BTC": 0.5}, PRICES, "2026-07-01")
    with pytest.raises(ValueError, match="missing_price:BTC"):
        broker.mark_to_market({"ETH": 10.0}, "2026-07-02")


def test_simulator_state_persists_across_instances(tmp_path):
    book_dir = tmp_path / "book"
    first = InternalSimulatorBroker(book_dir, starting_cash=100_000.0, cost_bps=20.0)
    first.submit_target_weights({"BTC": 0.5}, PRICES, "2026-07-01")
    second = InternalSimulatorBroker(book_dir, starting_cash=999.0)  # ignored: state exists
    assert second.get_cash() == pytest.approx(first.get_cash())
    assert second.get_positions() == pytest.approx(first.get_positions())
    assert second.last_rebalance == "2026-07-01"


# -- config / weights loading --------------------------------------------------


def test_load_book_configs_defaults_and_validation():
    cfg = {
        "papertrade": {
            "books": [
                {"name": "a", "weights_path": "x.parquet"},
                {"name": "b", "static_weights": {"BTC": 1.0}, "rebalance": "on_change",
                 "cost_bps": 10, "starting_cash": 5000},
            ]
        }
    }
    books = load_book_configs(cfg)
    assert [b.name for b in books] == ["a", "b"]
    assert books[0].rebalance == "weekly"
    assert books[0].cost_bps == 20.0
    assert books[0].starting_cash == 100_000.0
    assert books[1].static_weights == {"BTC": 1.0}
    assert books[1].cost_bps == 10.0

    with pytest.raises(ValueError):
        load_book_configs({"papertrade": {"books": [{"name": "x"}]}})  # no source
    with pytest.raises(ValueError):
        load_book_configs({"papertrade": {"books": [
            {"name": "x", "static_weights": {"BTC": 1.0}, "rebalance": "hourly"}]}})


def test_load_target_weights_latest_date_and_positive_only(tmp_path):
    path = _write_alloc_parquet(tmp_path / "alloc.parquet", [
        {"execution_date": "2026-06-01", "symbol": "BTC", "weight": 1.0, "strategy_name": "s"},
        {"execution_date": "2026-06-08", "symbol": "BTC", "weight": 0.6, "strategy_name": "s"},
        {"execution_date": "2026-06-08", "symbol": "ETH", "weight": 0.4, "strategy_name": "s"},
        {"execution_date": "2026-06-08", "symbol": "SOL", "weight": 0.0, "strategy_name": "s"},
    ])
    book = BookConfig(name="t", weights_path=str(path))
    assert load_target_weights(book) == pytest.approx({"BTC": 0.6, "ETH": 0.4})


def test_load_target_weights_strategy_filter(tmp_path):
    path = _write_alloc_parquet(tmp_path / "alloc.parquet", [
        {"execution_date": "2026-06-08", "symbol": "BTC", "weight": 1.0, "strategy_name": "alpha"},
        {"execution_date": "2026-06-08", "symbol": "ETH", "weight": 1.0, "strategy_name": "beta"},
    ])
    book = BookConfig(name="t", weights_path=str(path), strategy_name="beta")
    assert load_target_weights(book) == pytest.approx({"ETH": 1.0})


def test_load_target_weights_refuses_missing_weight_column(tmp_path):
    df = pd.DataFrame([
        {"execution_date": pd.Timestamp("2026-06-08", tz="UTC"), "symbol": "BTC",
         "target_weight": 1.0},
    ])
    path = tmp_path / "noweight.parquet"
    df.to_parquet(path, index=False)
    book = BookConfig(name="t", weights_path=str(path))
    with pytest.raises(ValueError, match="no 'weight' column"):
        load_target_weights(book)


def test_load_target_weights_static_passthrough():
    book = BookConfig(name="t", static_weights={"BTC": 0.5, "ETH": 0.0})
    assert load_target_weights(book) == {"BTC": 0.5}


# -- engine --------------------------------------------------------------------


def test_engine_run_and_idempotency(tmp_path):
    alloc = _write_alloc_parquet(tmp_path / "alloc.parquet", [
        {"execution_date": "2026-06-08", "symbol": "BTC", "weight": 0.5, "strategy_name": "s"},
        {"execution_date": "2026-06-08", "symbol": "ETH", "weight": 0.3, "strategy_name": "s"},
    ])
    cfg_path = _write_config(tmp_path, [
        {"name": "bookA", "weights_path": str(alloc), "rebalance": "weekly",
         "cost_bps": 20, "starting_cash": 100000},
    ])
    manifest = run_papertrade(str(cfg_path), as_of="2026-07-01")
    entry = manifest["books"]["bookA"]
    assert entry["status"] == "ok"
    assert entry["rebalanced"] is True
    assert entry["n_fills"] == 2
    assert entry["nav"] == pytest.approx(100_000.0 - 160.0)  # 20 bps on 80k traded

    book_dir = tmp_path / "papertrade_out" / "bookA"
    fills_before = pd.read_parquet(book_dir / "fills.parquet")
    assert len(fills_before) == 2

    # Re-run the same as_of: no duplicate equity row, fills unchanged.
    manifest2 = run_papertrade(str(cfg_path), as_of="2026-07-01")
    assert manifest2["books"]["bookA"]["status"] == "ok"
    assert manifest2["books"]["bookA"]["rebalanced"] is False  # weekly, same day
    equity = pd.read_parquet(book_dir / "equity.parquet")
    assert (equity["date"] == "2026-07-01").sum() == 1
    assert len(equity) == 1
    fills_after = pd.read_parquet(book_dir / "fills.parquet")
    assert len(fills_after) == len(fills_before)

    # Equity row semantics.
    row = equity.iloc[0]
    assert row["daily_return"] == pytest.approx(0.0)
    assert row["cum_return"] == pytest.approx(row["nav"] / 100_000.0 - 1.0)
    assert row["nav"] == pytest.approx(row["cash"] + row["positions_value"])

    # Manifest written to disk and returned.
    on_disk = json.loads((tmp_path / "papertrade_out" / "papertrade_manifest.json").read_text())
    assert on_disk["books"]["bookA"]["status"] == "ok"


def test_engine_weekly_rebalance_window(tmp_path):
    alloc = _write_alloc_parquet(tmp_path / "alloc.parquet", [
        {"execution_date": "2026-06-08", "symbol": "BTC", "weight": 0.5, "strategy_name": "s"},
    ])
    cfg_path = _write_config(tmp_path, [
        {"name": "bookA", "weights_path": str(alloc), "rebalance": "weekly"},
    ])
    m0 = run_papertrade(str(cfg_path), as_of="2026-07-01")
    assert m0["books"]["bookA"]["rebalanced"] is True

    # 3 days later: within the week -> mark-to-market only.
    m1 = run_papertrade(str(cfg_path), as_of="2026-07-04")
    assert m1["books"]["bookA"]["status"] == "ok"
    assert m1["books"]["bookA"]["rebalanced"] is False
    assert m1["books"]["bookA"]["n_fills"] == 0

    # 7 days later: due again.
    m2 = run_papertrade(str(cfg_path), as_of="2026-07-08")
    assert m2["books"]["bookA"]["rebalanced"] is True

    equity = pd.read_parquet(tmp_path / "papertrade_out" / "bookA" / "equity.parquet")
    assert list(equity["date"]) == ["2026-07-01", "2026-07-04", "2026-07-08"]
    # Flat prices: daily_return only reflects costs (<= 0), never fabricated gains.
    assert (equity["daily_return"] <= 1e-12).all()


def test_engine_static_book_and_on_change(tmp_path):
    books = [
        {"name": "bench", "static_weights": {"BTC": 0.5, "ETH": 0.5}, "rebalance": "on_change"},
    ]
    cfg_path = _write_config(tmp_path, books)
    m0 = run_papertrade(str(cfg_path), as_of="2026-07-01")
    assert m0["books"]["bench"]["rebalanced"] is True
    assert m0["books"]["bench"]["n_fills"] == 2

    # Unchanged weights the next day: on_change must not trade.
    m1 = run_papertrade(str(cfg_path), as_of="2026-07-02")
    assert m1["books"]["bench"]["rebalanced"] is False
    assert m1["books"]["bench"]["n_fills"] == 0

    # Changed weights: on_change trades again.
    books[0]["static_weights"] = {"BTC": 1.0}
    cfg_path = _write_config(tmp_path, books)
    m2 = run_papertrade(str(cfg_path), as_of="2026-07-03")
    assert m2["books"]["bench"]["rebalanced"] is True
    assert m2["books"]["bench"]["n_fills"] >= 1
    broker = InternalSimulatorBroker(tmp_path / "papertrade_out" / "bench")
    assert set(broker.get_positions()) == {"BTC"}


def test_engine_skips_book_when_held_price_missing(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path, [
        {"name": "bench", "static_weights": {"BTC": 1.0}, "rebalance": "daily"},
    ])
    run_papertrade(str(cfg_path), as_of="2026-07-01")
    equity_path = tmp_path / "papertrade_out" / "bench" / "equity.parquet"
    rows_before = len(pd.read_parquet(equity_path))

    # Next day the provider cannot price BTC: the book must be skipped, not marked.
    monkeypatch.setattr(engine, "fetch_prices", lambda symbols: {"ETH": 10.0})
    manifest = run_papertrade(str(cfg_path), as_of="2026-07-02")
    entry = manifest["books"]["bench"]
    assert entry["status"] == "skipped"
    assert "missing_prices_for_held" in entry["skip_reason"]
    assert entry["nav"] is None
    assert len(pd.read_parquet(equity_path)) == rows_before  # no fabricated row


def test_engine_books_filter_and_unknown_book(tmp_path):
    cfg_path = _write_config(tmp_path, [
        {"name": "a", "static_weights": {"BTC": 1.0}},
        {"name": "b", "static_weights": {"ETH": 1.0}},
    ])
    manifest = run_papertrade(str(cfg_path), as_of="2026-07-01", books=["b"])
    assert set(manifest["books"]) == {"b"}
    with pytest.raises(ValueError, match="unknown papertrade book"):
        run_papertrade(str(cfg_path), as_of="2026-07-01", books=["nope"])


def test_engine_daily_return_across_price_move(tmp_path, monkeypatch):
    cfg_path = _write_config(tmp_path, [
        {"name": "bench", "static_weights": {"BTC": 1.0}, "rebalance": "on_change"},
    ])
    run_papertrade(str(cfg_path), as_of="2026-07-01")
    monkeypatch.setattr(engine, "fetch_prices", lambda symbols: {"BTC": 110.0})
    run_papertrade(str(cfg_path), as_of="2026-07-02")
    equity = pd.read_parquet(tmp_path / "papertrade_out" / "bench" / "equity.parquet")
    equity = equity.sort_values("date").reset_index(drop=True)
    nav0, nav1 = float(equity.loc[0, "nav"]), float(equity.loc[1, "nav"])
    assert float(equity.loc[1, "daily_return"]) == pytest.approx(nav1 / nav0 - 1.0)
    assert float(equity.loc[1, "cum_return"]) == pytest.approx(nav1 / 100_000.0 - 1.0)
    assert nav1 > nav0  # BTC up 10%, fully invested


# -- alpaca adapter (offline) ----------------------------------------------------


def test_alpaca_broker_unavailable_without_keys(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    broker = AlpacaPaperBroker()
    with pytest.raises(AlpacaUnavailableError, match="alpaca-py"):
        broker.get_cash()


def test_alpaca_symbol_mapping():
    from papertrade.alpaca_broker import _from_alpaca_symbol, _to_alpaca_symbol
    assert _to_alpaca_symbol("BTC") == "BTC/USD"
    assert _to_alpaca_symbol("BTC/USD") == "BTC/USD"
    assert _from_alpaca_symbol("BTC/USD") == "BTC"
    assert _from_alpaca_symbol("BTCUSD") == "BTC"
    assert _from_alpaca_symbol("SOL") == "SOL"


def test_fill_dataclass_roundtrip():
    fill = Fill(date="2026-07-01", symbol="BTC", side="buy", qty=1.0,
                price=100.0, notional=100.0, cost_usd=0.2)
    assert fill.reason == "rebalance"
