"""Offline synthetic tests for the CHF multi-strategy sleeve layer.

No network, no real data files: every fixture is constructed in-test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.allocator import (
    allocate_proportional,
    apply_regime_multipliers,
    sortino_ratio,
)
from strategies.base import (
    WEIGHTS_SCHEMA,
    build_weights_frame,
    inverse_vol_weights,
    realized_vol,
    upsert_weights_parquet,
    validate_weights_frame,
)
from strategies.carry_sleeve import (
    CarrySleeve,
    annualize_funding,
    daily_annualized_funding,
    walk_active_slots,
)
from strategies.regime import build_regime_daily, classify_row, fear_greed_bucket
from strategies.technical_ensemble import (
    TechnicalEnsembleSleeve,
    hurst_rs,
    mean_reversion_score,
    momentum_score,
    stat_arb_score,
    trend_score,
    volatility_regime_score,
)
from strategies.trend_sleeve import TrendSleeve
from strategies.xsmom_sleeve import XSMomSleeve, load_altseason_index

UTC = "UTC"


# ---------------------------------------------------------------------------
# synthetic market builders
# ---------------------------------------------------------------------------


def closes_from_returns(returns, start_price=100.0):
    return start_price * np.cumprod(1.0 + np.asarray(returns, dtype=float))


def alternating(n, up, down):
    """Deterministic alternating return stream [up, down, up, down, ...]."""
    return np.array([up if i % 2 == 0 else down for i in range(n)])


def make_market(close_map, volume_map=None, start="2024-01-01"):
    """Long-format market panel like data/raw/market/market_ohlcv.parquet."""
    rows = []
    for symbol, closes in close_map.items():
        closes = np.asarray(closes, dtype=float)
        dates = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
        volume = (
            np.asarray(volume_map[symbol], dtype=float)
            if volume_map and symbol in volume_map
            else np.full(len(closes), 1_000_000.0)
        )
        for d, c, v in zip(dates, closes, volume):
            rows.append(
                {
                    "date_ts": d,
                    "symbol": symbol,
                    "open": c,
                    "high": c * 1.02,
                    "low": c * 0.98,
                    "close": c,
                    "volume": v,
                    "dollar_volume_usd": c * v,
                }
            )
    return pd.DataFrame(rows).sort_values(["symbol", "date_ts"]).reset_index(drop=True)


def _series(closes, start="2024-01-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
    return pd.Series(np.asarray(closes, dtype=float), index=dates)


# ---------------------------------------------------------------------------
# base helpers
# ---------------------------------------------------------------------------


def test_build_weights_frame_schema_and_cash_placeholder():
    signal = pd.Timestamp("2025-01-10", tz=UTC)
    df = build_weights_frame({"BTC": 0.6, "ETH": 0.3}, signal, "sleeve_test")
    assert list(df.columns[:4]) == WEIGHTS_SCHEMA
    assert (df["execution_date"] - df["date_ts"]).eq(pd.Timedelta(days=1)).all()
    assert df["weight"].sum() == pytest.approx(0.9)

    cash = build_weights_frame({}, signal, "sleeve_test")
    assert len(cash) == 1
    assert cash.iloc[0]["symbol"] == "CASH"
    assert cash.iloc[0]["weight"] == 0.0


def test_validate_weights_frame_rejects_bad_frames():
    signal = pd.Timestamp("2025-01-10", tz=UTC)
    good = build_weights_frame({"BTC": 0.5}, signal, "s")
    validate_weights_frame(good)

    over = good.copy()
    over["weight"] = 1.5
    with pytest.raises(ValueError):
        validate_weights_frame(over)

    neg = good.copy()
    neg["weight"] = -0.1
    with pytest.raises(ValueError):
        validate_weights_frame(neg)


def test_upsert_weights_parquet_is_idempotent(tmp_path):
    signal = pd.Timestamp("2025-01-10", tz=UTC)
    df = build_weights_frame({"BTC": 0.5}, signal, "s")
    path = tmp_path / "w.parquet"
    upsert_weights_parquet(df, path)
    upsert_weights_parquet(df, path)  # same date again
    out = pd.read_parquet(path)
    assert len(out) == 1


def test_inverse_vol_weights_math():
    w = inverse_vol_weights({"A": 0.2, "B": 0.4}, gross=0.9)
    # 1/0.2 : 1/0.4 == 2 : 1
    assert w["A"] == pytest.approx(0.6)
    assert w["B"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# trend sleeve
# ---------------------------------------------------------------------------


def _trend_market():
    n = 200
    btc = closes_from_returns(alternating(n, 0.012, -0.004))       # up everywhere
    eth = closes_from_returns(alternating(n, -0.012, 0.004))       # down everywhere
    sol_rets = np.concatenate(
        [alternating(160, -0.04, 0.005), alternating(40, 0.03, -0.005)]
    )  # 10d/30d up, 90d down -> 2 of 3 votes
    sol = closes_from_returns(sol_rets)
    return make_market({"BTC": btc, "ETH": eth, "SOL": sol})


def test_trend_votes_select_two_of_three_and_exclude_downtrend():
    market = _trend_market()
    sleeve = TrendSleeve()
    df = sleeve.generate(market, market["date_ts"].max())
    held = set(df.loc[df["weight"] > 0, "symbol"])
    assert held == {"BTC", "SOL"}


def test_trend_inverse_vol_weights_and_cash_scaling():
    market = _trend_market()
    sleeve = TrendSleeve()
    df = sleeve.generate(market, market["date_ts"].max())
    held = df[df["weight"] > 0].set_index("symbol")["weight"]

    closes = market.pivot_table(index="date_ts", columns="symbol", values="close")
    vols = {s: realized_vol(closes[s], 30) for s in ("BTC", "SOL")}
    expected = inverse_vol_weights(vols, gross=2.0 / 3.0)  # 2 active of 3 universe
    assert held["BTC"] == pytest.approx(expected["BTC"])
    assert held["SOL"] == pytest.approx(expected["SOL"])
    assert held["BTC"] > held["SOL"]  # lower vol -> larger weight
    assert held.sum() == pytest.approx(2.0 / 3.0)


# ---------------------------------------------------------------------------
# xsmom sleeve
# ---------------------------------------------------------------------------


def _xsmom_market(btc_up: bool):
    n = 150
    btc_rets = alternating(n, 0.009, -0.001) if btc_up else alternating(n, -0.009, 0.001)
    closes = {"BTC": closes_from_returns(btc_rets)}
    drifts = {"A1": 0.004, "A2": 0.008, "A3": 0.012, "A4": 0.016, "A5": 0.020}
    for sym, drift in drifts.items():
        closes[sym] = closes_from_returns(alternating(n, drift, -0.001))
    return make_market(closes)


def test_xsmom_btc_gate_closed_means_all_cash():
    market = _xsmom_market(btc_up=False)
    sleeve = XSMomSleeve({"altseason_path": "does/not/exist.parquet"})
    df = sleeve.generate(market, market["date_ts"].max())
    assert df["weight"].sum() == 0.0
    assert list(df["symbol"]) == ["CASH"]


def test_xsmom_gate_open_holds_top_quintile_with_capped_gross():
    market = _xsmom_market(btc_up=True)
    sleeve = XSMomSleeve({"altseason_path": "does/not/exist.parquet"})
    df = sleeve.generate(market, market["date_ts"].max())
    held = df[df["weight"] > 0]
    # 5 alts -> quintile of 5 -> hold exactly 1: the best 90d ratio vs BTC (A5)
    assert list(held["symbol"]) == ["A5"]
    # altseason data missing -> conservative 50% gross cap
    assert held["weight"].sum() == pytest.approx(0.5)


def test_xsmom_altseason_boost_allows_full_gross(tmp_path):
    market = _xsmom_market(btc_up=True)
    signal = market["date_ts"].max()
    alt_path = tmp_path / "altseason.parquet"
    pd.DataFrame(
        {"date": [signal.strftime("%Y-%m-%d")], "altcoin_index": [60.0]}
    ).to_parquet(alt_path)
    sleeve = XSMomSleeve({"altseason_path": str(alt_path)})
    df = sleeve.generate(market, signal)
    assert df.loc[df["weight"] > 0, "weight"].sum() == pytest.approx(1.0)


def test_altseason_staleness_returns_none(tmp_path):
    signal = pd.Timestamp("2025-06-30", tz=UTC)
    alt_path = tmp_path / "altseason.parquet"
    pd.DataFrame({"date": ["2025-05-01"], "altcoin_index": [80.0]}).to_parquet(alt_path)
    assert load_altseason_index(alt_path, signal, max_staleness_days=14) is None
    assert load_altseason_index(alt_path, signal, max_staleness_days=90) == 80.0


# ---------------------------------------------------------------------------
# carry sleeve
# ---------------------------------------------------------------------------


def test_annualize_funding_formula():
    assert annualize_funding(0.0001) == pytest.approx(0.0001 * 3 * 365)


def test_daily_annualized_funding_constant_rate():
    times = pd.date_range("2025-01-01", periods=30, freq="8h", tz=UTC)
    df = pd.DataFrame(
        {"symbol": "BTC", "funding_time_utc": times, "funding_rate": 0.0002}
    )
    ann = daily_annualized_funding(df, trailing_days=7)
    assert ann["BTC"].dropna().iloc[-1] == pytest.approx(0.0002 * 3 * 365)


def test_carry_entry_exit_hysteresis():
    days = pd.date_range("2025-01-01", periods=6, freq="D", tz=UTC)
    ann = pd.DataFrame({"BTC": [0.02, 0.20, 0.07, 0.07, 0.04, 0.07]}, index=days)
    mem = walk_active_slots(ann, entry=0.10, exit_=0.05, max_slots=3)
    # day0 below entry, day1 enters (>10%), days2-3 stay (hysteresis: >=5%),
    # day4 exits (<5%), day5 does NOT re-enter (7% < entry 10%)
    assert list(mem["BTC"]) == [False, True, True, True, False, False]


def test_carry_max_three_slots_prefers_highest_funding():
    days = pd.date_range("2025-01-01", periods=1, freq="D", tz=UTC)
    ann = pd.DataFrame(
        {"A": [0.12], "B": [0.30], "C": [0.20], "D": [0.15]}, index=days
    )
    mem = walk_active_slots(ann, entry=0.10, exit_=0.05, max_slots=3)
    active = set(mem.columns[mem.iloc[0]])
    assert active == {"B", "C", "D"}  # top 3 by annualized funding; A left out


def test_carry_sleeve_generate_slot_weights(tmp_path):
    n = 100
    market = make_market({"BTC": closes_from_returns(alternating(n, 0.01, -0.005))})
    last_day = market["date_ts"].max()
    times = pd.date_range(end=last_day, periods=45, freq="8h", tz=UTC)
    funding_path = tmp_path / "funding.parquet"
    pd.DataFrame(
        {"symbol": "BTC", "funding_time_utc": times, "funding_rate": 0.0002}
    ).to_parquet(funding_path)
    diag_path = tmp_path / "diag.parquet"
    sleeve = CarrySleeve(
        {"funding_path": str(funding_path), "diagnostics_path": str(diag_path)}
    )
    df = sleeve.generate(market, last_day)
    held = df[df["weight"] > 0]
    assert list(held["symbol"]) == ["BTC"]
    assert held["weight"].iloc[0] == pytest.approx(1.0 / 3.0)  # one slot of three
    diag = pd.read_parquet(diag_path)
    assert "virtual_daily_funding_yield" in diag.columns
    assert diag["virtual_daily_funding_yield"].max() > 0


# ---------------------------------------------------------------------------
# technical ensemble sub-signals
# ---------------------------------------------------------------------------


def _wiggle_trend(n, drift):
    rets = np.array([drift + (0.002 if i % 2 == 0 else -0.002) for i in range(n)])
    return closes_from_returns(rets)


def test_trend_score_ema_ordering():
    up = _series(_wiggle_trend(150, 0.01))
    down = _series(_wiggle_trend(150, -0.01))
    s_up = trend_score(up, up * 1.02, up * 0.98)
    s_down = trend_score(down, down * 1.02, down * 0.98)
    assert s_up > 0  # ema8 > ema21 > ema55 with ADX confidence
    assert s_down < 0


def test_mean_reversion_score_extremes():
    base = 100.0 + 0.1 * alternating(100, 1.0, -1.0)
    crashed = base.copy()
    crashed[-1] = 80.0  # far below 50d MA -> z << -2, %B < 0.2
    assert mean_reversion_score(_series(crashed)) > 0
    spiked = base.copy()
    spiked[-1] = 120.0
    assert mean_reversion_score(_series(spiked)) < 0
    assert mean_reversion_score(_series(base)) == 0.0  # no extreme -> neutral


def test_momentum_score_volume_confirmation():
    closes = closes_from_returns(np.full(200, 0.005))
    vol_confirm = np.full(200, 1_000_000.0)
    vol_confirm[-1] = 2_000_000.0  # last volume > 21d MA
    vol_weak = np.full(200, 1_000_000.0)
    vol_weak[-1] = 400_000.0
    s_confirmed = momentum_score(_series(closes), _series(vol_confirm))
    s_unconfirmed = momentum_score(_series(closes), _series(vol_weak))
    assert s_confirmed == pytest.approx(1.0)  # mom*5 clipped at +1
    assert s_unconfirmed == pytest.approx(0.5)  # halved without volume confirm


def test_volatility_regime_score_low_vol_bullish():
    rets = np.concatenate([alternating(120, 0.04, -0.04), alternating(40, 0.005, -0.005)])
    assert volatility_regime_score(_series(closes_from_returns(rets))) > 0


def test_hurst_rs_orders_antipersistent_below_trending():
    antipersistent = _series(closes_from_returns(alternating(256, 0.01, -0.01)))
    rng = np.random.default_rng(42)
    walk = _series(closes_from_returns(rng.normal(0.002, 0.01, 256)))
    h_anti = hurst_rs(antipersistent)
    h_walk = hurst_rs(walk)
    assert h_anti is not None and h_walk is not None
    assert h_anti < 0.4
    assert h_anti < h_walk


def test_stat_arb_score_mean_reverting_positive_skew():
    rets = alternating(256, 0.01, -0.01)
    rets[-63::8] = 0.06  # sparse positive spikes in the last 63d -> skew > 1
    s = _series(closes_from_returns(rets))
    h = hurst_rs(s)
    skew = s.pct_change().dropna().tail(63).skew()
    assert h is not None and h < 0.4
    assert skew > 1.0
    assert stat_arb_score(s) == pytest.approx(min((0.5 - h) * 2.0, 1.0))


def test_stat_arb_score_zero_for_trending_series():
    rng = np.random.default_rng(7)
    s = _series(closes_from_returns(rng.normal(0.003, 0.01, 256)))
    h = hurst_rs(s)
    if h is not None and h >= 0.4:
        assert stat_arb_score(s) == 0.0


# ---------------------------------------------------------------------------
# regime classifier
# ---------------------------------------------------------------------------

_NO_EXTERNAL = {
    "global_metrics_path": "does/not/exist.parquet",
    "fear_greed_path": "does/not/exist.parquet",
}


def test_classify_row_mapping():
    assert classify_row(False, 0.2, True) == "stress"
    assert classify_row(False, 0.2, False) == "risk_off"
    assert classify_row(True, 0.7, False) == "risk_on"
    assert classify_row(True, 0.7, True) == "risk_on"  # corr spike alone isn't stress
    assert classify_row(True, 0.3, False) == "neutral"


def test_fear_greed_buckets():
    assert fear_greed_bucket(10) == "extreme_fear"
    assert fear_greed_bucket(30) == "fear"
    assert fear_greed_bucket(50) == "neutral"
    assert fear_greed_bucket(70) == "greed"
    assert fear_greed_bucket(90) == "extreme_greed"
    assert fear_greed_bucket(None) == "unknown"


def test_regime_stress_on_correlated_downtrend():
    n = 160
    rets = alternating(n, -0.02, 0.001)
    market = make_market(
        {s: closes_from_returns(rets) for s in ("BTC", "ETH", "SOL")}
    )
    daily = build_regime_daily(market, _NO_EXTERNAL)
    last = daily.iloc[-1]
    assert not last["btc_trend_up"]
    assert last["corr_spike"]  # identical return streams -> pairwise corr ~1
    assert last["regime"] == "stress"
    assert last["fear_greed_bucket"] == "unknown"


def test_regime_risk_on_broad_uptrend():
    n = 160
    rets = alternating(n, 0.02, -0.001)
    market = make_market(
        {s: closes_from_returns(rets) for s in ("BTC", "ETH", "SOL")}
    )
    daily = build_regime_daily(market, _NO_EXTERNAL)
    last = daily.iloc[-1]
    assert last["btc_trend_up"]
    assert last["breadth_pct_above_50d_ma"] >= 0.5
    assert last["regime"] == "risk_on"


# ---------------------------------------------------------------------------
# allocator
# ---------------------------------------------------------------------------


def test_allocator_proportional_with_floor_and_cap():
    scores = {"a": 4.0, "b": 1.0, "c": 0.5, "d": -2.0}
    w = allocate_proportional(scores, sortino_floor=0.1, weight_floor=0.05, weight_cap=0.40)
    assert sum(w.values()) == pytest.approx(1.0)
    for v in w.values():
        assert 0.05 - 1e-9 <= v <= 0.40 + 1e-9
    assert w["a"] == pytest.approx(0.40)   # dominant sleeve hits the cap
    assert w["d"] >= 0.05                  # negative sortino floored via max(s, 0.1)
    # sleeves not pinned at a bound stay proportional to their raw scores
    assert w["b"] / w["c"] == pytest.approx(1.0 / 0.5)


def test_allocator_dominant_sleeve_capped_not_rescaled_past_cap():
    # Regression: one dominant Sortino + three floored raws must end at
    # cap + equal split of the remainder, never above the cap.
    scores = {"carry": 0.0, "technical": 2.08, "trend": -2.2, "xsmom": -0.37}
    w = allocate_proportional(scores, sortino_floor=0.1, weight_floor=0.05, weight_cap=0.40)
    assert w["technical"] == pytest.approx(0.40)
    for k in ("carry", "trend", "xsmom"):
        assert w[k] == pytest.approx(0.20)
    assert sum(w.values()) == pytest.approx(1.0)


def test_allocator_equal_scores_equal_weights():
    w = allocate_proportional({"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0})
    for v in w.values():
        assert v == pytest.approx(0.25)


def test_regime_multiplier_application():
    multipliers = {
        "stress": {"sleeve_trend": 0.5, "sleeve_xsmom": 0.0, "sleeve_carry": 1.0}
    }
    base = {"sleeve_trend": 0.4, "sleeve_xsmom": 0.3, "sleeve_carry": 0.3}
    out = apply_regime_multipliers(base, "stress", multipliers)
    assert out["sleeve_trend"] == pytest.approx(0.2)
    assert out["sleeve_xsmom"] == 0.0
    assert out["sleeve_carry"] == pytest.approx(0.3)
    assert sum(out.values()) <= sum(base.values())  # freed mass -> cash


def test_sortino_ratio_signs_and_cap():
    up = pd.Series([0.01] * 60)  # no downside -> capped
    assert sortino_ratio(up) == 10.0
    mixed = pd.Series([0.01, -0.02] * 30)
    assert sortino_ratio(mixed) < 0
    assert sortino_ratio(pd.Series(dtype=float)) == 0.0


# ---------------------------------------------------------------------------
# engine compatibility: every sleeve emits a valid weights frame
# ---------------------------------------------------------------------------


def _shared_panel():
    n = 250
    closes = {
        "BTC": closes_from_returns(alternating(n, 0.010, -0.004)),
        "ETH": closes_from_returns(alternating(n, 0.008, -0.003)),
        "SOL": closes_from_returns(alternating(n, 0.012, -0.006)),
        "AAA": closes_from_returns(alternating(n, 0.014, -0.006)),
        "BBB": closes_from_returns(alternating(n, -0.010, 0.004)),
        "CCC": closes_from_returns(alternating(n, 0.002, -0.001)),
    }
    return make_market(closes)


def test_all_sleeves_engine_compatible(tmp_path):
    market = _shared_panel()
    as_of = market["date_ts"].max()

    funding_path = tmp_path / "funding.parquet"
    times = pd.date_range(end=as_of, periods=60, freq="8h", tz=UTC)
    pd.DataFrame(
        {"symbol": "BTC", "funding_time_utc": times, "funding_rate": 0.0002}
    ).to_parquet(funding_path)

    sleeves = [
        TrendSleeve(),
        XSMomSleeve({"altseason_path": "does/not/exist.parquet"}),
        CarrySleeve({"funding_path": str(funding_path), "diagnostics_path": None}),
        TechnicalEnsembleSleeve(),
    ]
    for sleeve in sleeves:
        df = sleeve.generate(market, as_of)
        validate_weights_frame(df)  # schema + non-negative + sum <= 1
        assert set(WEIGHTS_SCHEMA).issubset(df.columns)
        assert (df["weight"] >= 0).all()
        assert df["weight"].sum() <= 1.0 + 1e-6
        assert (df["execution_date"] - df["date_ts"]).eq(pd.Timedelta(days=1)).all()
        assert (df["date_ts"] == as_of).all()  # signal = last completed candle
