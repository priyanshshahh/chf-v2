"""Offline synthetic tests for the short-side / market-neutral layer.

No network, no real data files. Covers: short fill math + margin guard + perp
funding-accrual sign + perp mark-to-market; carry-neutral paired (net-delta-0)
legs; statarb z-score entry/exit + dollar-neutral sizing; defi_yield ranking +
TVL filter; macro overlay multiplier; and backward-compat (a long-only book
behaves and persists exactly as before).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import yaml

import papertrade.engine as engine
from papertrade.engine import run_papertrade
from papertrade.simulator import InternalSimulatorBroker
from strategies.base import validate_signed_weights_frame
from strategies.carry_sleeve import CarryNeutralSleeve
from strategies.defi_yield_sleeve import DefiYieldSleeve
from strategies.macro_overlay import build_overlay
from strategies.statarb_sleeve import StatArbSleeve, rolling_hedge_z, walk_pair_position

UTC = "UTC"
PRICES = {"BTC": 100.0, "ETH": 10.0}


def make_market(close_map, start="2024-01-01"):
    rows = []
    for symbol, closes in close_map.items():
        closes = np.asarray(closes, dtype=float)
        dates = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
        for d, c in zip(dates, closes):
            rows.append({"date_ts": d, "symbol": symbol, "open": c, "high": c * 1.02,
                         "low": c * 0.98, "close": c, "volume": 1_000_000.0,
                         "dollar_volume_usd": c * 1_000_000.0})
    return pd.DataFrame(rows).sort_values(["symbol", "date_ts"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# short spot fill math + mark-to-market
# ---------------------------------------------------------------------------


def test_short_spot_fill_credits_cash_and_gains_when_price_falls(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "s", starting_cash=100_000.0, cost_bps=20.0,
                                allow_short=True, margin_requirement=0.5)
    fills = b.submit_target_legs([("BTC", "spot", -0.5)], PRICES, "2026-07-01")
    assert len(fills) == 1
    f = fills[0]
    assert f.side == "short" and f.notional == pytest.approx(-50_000.0)
    assert f.instrument_type == "spot"
    assert b.get_positions()["BTC"] == pytest.approx(-500.0)
    # short sale credits cash (minus the 20 bps fee)
    assert b.get_cash() == pytest.approx(100_000.0 + 50_000.0 - 100.0)
    # gain when price falls
    nav_flat = b.mark_to_market(PRICES, "2026-07-01").nav
    nav_down = b.mark_to_market({"BTC": 90.0}, "2026-07-02").nav
    assert nav_down > nav_flat


def test_margin_guard_blocks_oversized_short(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "s", starting_cash=100_000.0,
                                allow_short=True, margin_requirement=0.5)
    # short 3x equity -> gross short 300k, margin_used 150k > equity ~100k
    with pytest.raises(ValueError, match="margin_violation"):
        b.submit_target_legs([("BTC", "spot", -3.0)], PRICES, "2026-07-01")


# ---------------------------------------------------------------------------
# perp funding accrual sign + perp mark-to-market
# ---------------------------------------------------------------------------


def test_short_perp_receives_funding_when_positive(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "p", starting_cash=100_000.0, cost_bps=20.0,
                                allow_short=True, margin_requirement=0.5)
    fills = b.submit_target_legs([("BTC", "perp", -0.5)], PRICES, "2026-07-01")
    assert fills[0].instrument_type == "perp" and fills[0].side == "short"
    # perp opening moves no principal: only the fee leaves cash
    assert b.get_cash() == pytest.approx(100_000.0 - 100.0)
    assert b.get_perp_positions()["BTC"] == pytest.approx(-500.0)
    cash_before = b.get_cash()
    accrued = b.accrue_perp_funding({"BTC": 0.001}, PRICES, "2026-07-02")
    # short perp (qty<0), funding_rate>0 -> receives  (-qty*price*rate = +50)
    assert accrued == pytest.approx(50.0)
    assert b.get_cash() == pytest.approx(cash_before + 50.0)
    # idempotent: re-accruing the same date does nothing
    assert b.accrue_perp_funding({"BTC": 0.001}, PRICES, "2026-07-02") == 0.0


def test_long_perp_pays_funding_when_positive(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "p", starting_cash=100_000.0,
                                allow_short=True, margin_requirement=0.5)
    b.submit_target_legs([("BTC", "perp", 0.5)], PRICES, "2026-07-01")
    accrued = b.accrue_perp_funding({"BTC": 0.001}, PRICES, "2026-07-02")
    assert accrued == pytest.approx(-50.0)  # long pays


def test_short_perp_marks_gain_when_price_falls(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "p", starting_cash=100_000.0,
                                allow_short=True, margin_requirement=0.5)
    b.submit_target_legs([("BTC", "perp", -0.5)], PRICES, "2026-07-01")
    snap = b.mark_to_market({"BTC": 90.0}, "2026-07-02")
    # perp pnl = qty*(mark-entry) = -500*(90-100) = +5000
    assert snap.perp_pnl == pytest.approx(5000.0)
    assert snap.nav == pytest.approx(b.get_cash() + 5000.0)


# ---------------------------------------------------------------------------
# carry-neutral: net-delta-zero paired legs
# ---------------------------------------------------------------------------


def _funding_okx(symbol, last_day, rate, periods=60):
    times = pd.date_range(end=last_day, periods=periods, freq="8h", tz=UTC)
    return pd.DataFrame({"symbol": symbol, "ts_utc": times, "metric": "funding_rate",
                         "value": rate, "source": "okx"})


def test_carry_neutral_emits_net_delta_zero_pairs(tmp_path):
    n = 120
    closes = 100.0 * np.cumprod(1.0 + np.array([0.01 if i % 2 == 0 else -0.005 for i in range(n)]))
    market = make_market({"BTC": closes})
    last = market["date_ts"].max()
    fpath = tmp_path / "okx.parquet"
    # rate 0.0002/interval -> annualized 0.0002*3*365 = 0.219 > 0.10 entry
    _funding_okx("BTC", last, 0.0002).to_parquet(fpath)
    sleeve = CarryNeutralSleeve({"funding_path": str(fpath), "diagnostics_path": None})
    df = sleeve.generate(market, last)
    validate_signed_weights_frame(df)
    held = df[df["symbol"] == "BTC"]
    by_it = held.set_index("instrument_type")["weight"]
    assert by_it["spot"] == pytest.approx(0.3)     # gross_target 0.9 / 3 slots
    assert by_it["perp"] == pytest.approx(-0.3)
    assert (by_it["spot"] + by_it["perp"]) == pytest.approx(0.0)  # net delta ~ 0


# ---------------------------------------------------------------------------
# statarb: z-score entry/exit + dollar-neutral sizing
# ---------------------------------------------------------------------------


def _cointegrated(n, diverge):
    """Deterministic pair: a stable low-noise spread; when ``diverge`` the last
    few points ramp apart so the spread's z-score exits the band."""
    t = np.arange(n)
    log_b = np.log(100.0) + 0.0005 * t
    eps = 0.003 * ((-1.0) ** t)             # stable, mean-reverting spread
    if diverge:
        ramp = np.array([0.004, 0.006, 0.009, 0.013, 0.018, 0.024])
        eps[-len(ramp):] = eps[-len(ramp):] + ramp
    log_a = log_b + eps
    return np.exp(log_a), np.exp(log_b)


def test_walk_pair_position_hysteresis():
    z = pd.Series([0.0, 2.5, 1.0, 0.3, 2.2])  # enter short, hold, exit, re-enter
    assert walk_pair_position(z, 2.0, 0.5, 4.0) == -1
    z2 = pd.Series([0.0, 2.5, 5.0])  # stop-out on |z|>4 -> flat
    assert walk_pair_position(z2, 2.0, 0.5, 4.0) == 0
    z3 = pd.Series([0.0, -2.5, -1.0])  # z<<0 -> long A / short B
    assert walk_pair_position(z3, 2.0, 0.5, 4.0) == 1


def test_statarb_dollar_neutral_entry(tmp_path):
    a, b = _cointegrated(220, diverge=True)
    market = make_market({"AAA": a, "BBB": b})
    last = market["date_ts"].max()
    sleeve = StatArbSleeve({"configured_pairs": [["AAA", "BBB"]], "gross_target": 1.0})
    df = sleeve.generate(market, last)
    validate_signed_weights_frame(df)
    w = df.set_index("symbol")["weight"]
    # exactly one long leg / one short leg (market-neutral), equal-dollar sized
    assert set(w.index) == {"AAA", "BBB"}
    assert w["AAA"] * w["BBB"] < 0                       # opposite signs
    assert abs(w["AAA"]) == pytest.approx(abs(w["BBB"]))  # dollar-neutral
    assert (df["weight"].abs().sum()) == pytest.approx(1.0)


def test_statarb_flat_when_spread_in_band(tmp_path):
    a, b = _cointegrated(220, diverge=False)  # no divergence -> |z| small
    market = make_market({"AAA": a, "BBB": b})
    last = market["date_ts"].max()
    sleeve = StatArbSleeve({"configured_pairs": [["AAA", "BBB"]]})
    df = sleeve.generate(market, last)
    assert list(df["symbol"]) == ["CASH"]  # all cash


# ---------------------------------------------------------------------------
# defi_yield: ranking + TVL / blue-chip filters
# ---------------------------------------------------------------------------


def test_defi_yield_ranking_and_filters(tmp_path):
    yields = pd.DataFrame([
        {"pool_id": "p1", "project": "aave", "symbol": "WBTC", "chain": "Ethereum",
         "apy": 0.05, "tvl_usd": 2.0e8},
        {"pool_id": "p2", "project": "x", "symbol": "XYZ", "chain": "Ethereum",
         "apy": 0.90, "tvl_usd": 5.0e8},                    # not blue-chip -> filtered
        {"pool_id": "p3", "project": "sky", "symbol": "USDC", "chain": "Ethereum",
         "apy": 0.10, "tvl_usd": 1.0e8},
        {"pool_id": "p4", "project": "y", "symbol": "WETH", "chain": "Ethereum",
         "apy": 0.50, "tvl_usd": 1.0e6},                    # below TVL floor -> filtered
    ])
    ypath = tmp_path / "yields.parquet"
    yields.to_parquet(ypath)
    diag = tmp_path / "diag.parquet"
    market = make_market({"BTC": np.full(40, 100.0)})
    sleeve = DefiYieldSleeve({"yields_path": str(ypath), "diagnostics_path": str(diag)})
    df = sleeve.generate(market, market["date_ts"].max())
    held = set(df.loc[df["weight"] > 0, "symbol"])
    assert held <= {"BTC", "USDC"}       # WETH (thin) and XYZ (not blue-chip) excluded
    assert "BTC" in held
    assert df["weight"].sum() == pytest.approx(0.9)
    d = pd.read_parquet(diag)
    assert "virtual_daily_yield" in d.columns and (d["virtual_daily_yield"] > 0).any()


# ---------------------------------------------------------------------------
# macro overlay
# ---------------------------------------------------------------------------


def test_macro_overlay_multiplier_from_regime(tmp_path):
    reg = pd.DataFrame({
        "date_ts": pd.date_range("2026-01-01", periods=4, freq="D", tz=UTC),
        "regime": ["risk_on", "neutral", "risk_off", "stress"],
    })
    rpath = tmp_path / "regime.parquet"
    reg.to_parquet(rpath)
    table = build_overlay({"regime_path": str(rpath),
                           "macro_fred_path": "does/not/exist.parquet"})
    m = table.set_index("regime")["gross_multiplier"]
    assert m["risk_on"] == pytest.approx(1.0)
    assert m["stress"] == pytest.approx(0.35)
    assert m["risk_off"] == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# backward-compat: long-only book unchanged + no new state keys
# ---------------------------------------------------------------------------


def test_long_only_state_has_no_short_keys(tmp_path):
    b = InternalSimulatorBroker(tmp_path / "b", starting_cash=100_000.0, cost_bps=20.0)
    b.submit_target_weights({"BTC": 0.5, "ETH": 0.3}, PRICES, "2026-07-01")
    state = json.loads((tmp_path / "b" / "state.json").read_text())
    assert set(state) == {"cash", "positions", "last_rebalance"}  # byte-compatible
    assert b.get_positions() == pytest.approx({"BTC": 500.0, "ETH": 3000.0})


def test_allow_short_book_long_leg_matches_long_only(tmp_path):
    # A short-capable book fed only long spot legs must match the long-only path.
    short_b = InternalSimulatorBroker(tmp_path / "sh", starting_cash=100_000.0,
                                      cost_bps=20.0, allow_short=True, margin_requirement=0.5)
    short_b.submit_target_legs([("BTC", "spot", 0.5)], PRICES, "2026-07-01")
    long_b = InternalSimulatorBroker(tmp_path / "lo", starting_cash=100_000.0, cost_bps=20.0)
    long_b.submit_target_weights({"BTC": 0.5}, PRICES, "2026-07-01")
    assert short_b.get_cash() == pytest.approx(long_b.get_cash())
    assert short_b.get_positions() == pytest.approx(long_b.get_positions())


# ---------------------------------------------------------------------------
# engine end-to-end: opens a short/perp book, accrues funding, stays neutral
# ---------------------------------------------------------------------------


def _write_signed_parquet(path, exec_date, legs):
    rows = [{"execution_date": pd.Timestamp(exec_date, tz=UTC),
             "date_ts": pd.Timestamp(exec_date, tz=UTC), "symbol": s,
             "weight": w, "instrument_type": it} for s, it, w in legs]
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def test_engine_opens_short_perp_book_and_accrues_funding(tmp_path, monkeypatch):
    wpath = tmp_path / "carry_neutral_weights.parquet"
    _write_signed_parquet(wpath, "2026-06-08",
                          [("BTC", "spot", 0.3), ("BTC", "perp", -0.3)])
    cfg = {"papertrade": {"output_dir": str(tmp_path / "out"), "broker": "simulator",
                          "books": [{"name": "cn", "weights_path": str(wpath),
                                     "rebalance": "weekly", "cost_bps": 20,
                                     "starting_cash": 100000, "allow_short": True,
                                     "margin_requirement": 0.5}]}}
    cfg_path = tmp_path / "run.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    monkeypatch.setattr(engine, "fetch_prices", lambda symbols: {"BTC": 100.0})
    monkeypatch.setattr(engine, "daily_funding_rates",
                        lambda path, as_of, symbols: {"BTC": 0.001})

    m0 = run_papertrade(str(cfg_path), as_of="2026-07-01")
    entry = m0["books"]["cn"]
    assert entry["status"] == "ok" and entry["rebalanced"] is True
    assert entry["n_fills"] == 2                       # spot long + perp short
    # perp qty = -0.3 * 100000 / 100 = -300 ; funding = -qty*price*rate = +30
    assert entry.get("funding_accrued") == pytest.approx(30.0, rel=1e-3)

    book_dir = tmp_path / "out" / "cn"
    fills = pd.read_parquet(book_dir / "fills.parquet")
    assert set(fills["instrument_type"]) == {"spot", "perp"}
    assert "short" in set(fills["side"])

    # next day BTC falls 10%: spot loses ~ perp gains -> near delta-neutral book
    monkeypatch.setattr(engine, "fetch_prices", lambda symbols: {"BTC": 90.0})
    m1 = run_papertrade(str(cfg_path), as_of="2026-07-02")
    eq = pd.read_parquet(book_dir / "equity.parquet").sort_values("date")
    nav0, nav1 = float(eq.iloc[0]["nav"]), float(eq.iloc[1]["nav"])
    # a 10% BTC move must not move a delta-neutral book by more than ~1%
    assert abs(nav1 / nav0 - 1.0) < 0.01
