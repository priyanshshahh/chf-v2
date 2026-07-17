"""Offline synthetic tests for the CHF portfolio risk layer.

Run: .venv/bin/python -m pytest tests/test_risk_layer.py -q
No network, no repo data files: everything is synthetic or tmp_path-local.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from portfolio import drawdown_control as dd
from portfolio import liquidity, risk_limits, risk_pipeline, vol_target

TOL = 1e-6


# ---------------------------------------------------------------------------
# risk_limits
# ---------------------------------------------------------------------------


class TestRiskLimits:
    def test_single_asset_cap_clips_and_reports(self):
        res = risk_limits.apply_risk_limits(
            {"A": 0.5, "B": 0.3, "C": 0.2},
            {"max_single_asset_weight": 0.2, "max_top5_concentration": 0.6,
             "max_gross_exposure": 1.0},
        )
        w = res["weights"]
        assert all(v <= 0.2 + TOL for v in w.values())
        assert res["converged"]
        # everyone capped at 0.2; remainder is cash
        assert w == pytest.approx({"A": 0.2, "B": 0.2, "C": 0.2})
        assert res["cash_weight"] == pytest.approx(0.4)
        reasons = {a["reason"] for a in res["adjustments"]}
        assert "max_single_asset_weight" in reasons
        for adj in res["adjustments"]:
            assert set(adj) == {"symbol", "reason", "before", "after"}

    def test_iterative_convergence_when_renormalize_pushes_over(self):
        # clipping A frees mass that pushes B over the cap -> second iteration
        res = risk_limits.apply_risk_limits(
            {"A": 0.4, "B": 0.25, "C": 0.15, "D": 0.1, "E": 0.1},
            {"max_single_asset_weight": 0.28, "max_top5_concentration": 1.0,
             "max_gross_exposure": 1.0},
        )
        w = res["weights"]
        assert res["converged"]
        assert res["iterations"] > 1
        assert all(v <= 0.28 + TOL for v in w.values())
        # feasible: 5 * 0.28 = 1.4 >= 1, so full gross should be recovered
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-6)
        reasons = {a["reason"] for a in res["adjustments"]}
        assert "max_single_asset_weight" in reasons
        assert "renormalize" in reasons

    def test_top5_concentration_cap(self):
        weights = {f"S{i}": 1.0 / 7.0 for i in range(7)}
        res = risk_limits.apply_risk_limits(
            weights,
            {"max_single_asset_weight": 0.2, "max_top5_concentration": 0.6,
             "max_gross_exposure": 1.0},
        )
        w = res["weights"]
        assert res["converged"]
        top5 = sum(sorted(w.values(), reverse=True)[:5])
        assert top5 <= 0.6 + 1e-6
        assert sum(w.values()) + res["cash_weight"] == pytest.approx(1.0)

    def test_category_cap_and_redistribution(self):
        res = risk_limits.apply_risk_limits(
            {"A": 0.3, "B": 0.3, "C": 0.4},
            {"max_single_asset_weight": 0.5, "max_top5_concentration": 1.0,
             "max_category_weight": 0.4, "max_gross_exposure": 1.0},
            categories={"A": ["layer-1"], "B": ["layer-1"]},
        )
        w = res["weights"]
        assert res["converged"]
        assert res["category_checks"]
        assert w["A"] + w["B"] <= 0.4 + TOL
        assert w["C"] <= 0.5 + TOL
        cat_adjs = [a for a in res["adjustments"]
                    if a["reason"].startswith("max_category_weight")]
        assert {a["symbol"] for a in cat_adjs} == {"A", "B"}

    def test_no_categories_degrades_gracefully(self):
        res = risk_limits.apply_risk_limits({"A": 0.1, "B": 0.1}, categories=None)
        assert not res["category_checks"]
        assert res["weights"] == pytest.approx({"A": 0.1, "B": 0.1})
        assert res["adjustments"] == []

    def test_gross_exposure_scaled_down(self):
        res = risk_limits.apply_risk_limits(
            {"A": 0.8, "B": 0.7},
            {"max_single_asset_weight": 1.0, "max_top5_concentration": 2.0,
             "max_gross_exposure": 1.0},
        )
        assert sum(res["weights"].values()) <= 1.0 + TOL
        assert {a["reason"] for a in res["adjustments"]} == {"gross_exposure_cap"}

    def test_negative_weights_dropped(self):
        res = risk_limits.apply_risk_limits({"A": 0.1, "B": -0.2})
        assert "B" not in res["weights"]
        assert any(a["reason"] == "negative_weight_dropped"
                   for a in res["adjustments"])


# ---------------------------------------------------------------------------
# vol_target
# ---------------------------------------------------------------------------


def _synthetic_prices(sigmas, n_days=400, seed=7, start=100.0):
    rng = np.random.default_rng(seed)
    cols = {}
    for i, sig in enumerate(sigmas):
        rets = rng.normal(0.0, sig, size=n_days)
        cols[f"S{i}"] = start * np.exp(np.cumsum(rets))
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D")
    return pd.DataFrame(cols, index=idx)


class TestVolTarget:
    def test_forecast_recovers_known_vol_and_hits_target(self):
        sig_daily = 0.02  # true annual vol ~ 0.382
        prices = _synthetic_prices([sig_daily], n_days=600, seed=3)
        rets = vol_target.daily_log_returns(prices)
        cov = vol_target.ewma_covariance(rets, halflife_days=30)
        fvol = vol_target.forecast_portfolio_vol({"S0": 1.0}, cov, 365)
        true_vol = sig_daily * math.sqrt(365)
        assert fvol == pytest.approx(true_vol, rel=0.25)
        mult = vol_target.exposure_multiplier(fvol, 0.15, 0.2, 1.0)
        # unclipped here (0.2 < 0.15/fvol < 1.0): scaled book hits the target
        assert 0.2 < mult < 1.0
        assert mult * fvol == pytest.approx(0.15, abs=1e-12)

    def test_exposure_multiplier_clipped(self):
        assert vol_target.exposure_multiplier(0.05, 0.15, 0.2, 1.0) == 1.0
        assert vol_target.exposure_multiplier(2.0, 0.15, 0.2, 1.0) == 0.2
        assert vol_target.exposure_multiplier(0.0, 0.15, 0.2, 1.0) == 1.0

    def test_inverse_vol_weights(self):
        w = vol_target.inverse_vol_weights({"A": 0.1, "B": 0.2})
        assert sum(w.values()) == pytest.approx(1.0)
        assert w["A"] == pytest.approx(2.0 * w["B"])
        assert vol_target.inverse_vol_weights({"A": 0.0, "B": -1.0}) == {}

    def test_risk_contributions_sum_to_variance(self):
        prices = _synthetic_prices([0.01, 0.02, 0.03], n_days=300, seed=11)
        rets = vol_target.daily_log_returns(prices)
        cov = vol_target.ewma_covariance(rets, 30)
        weights = {"S0": 0.5, "S1": 0.3, "S2": 0.2}
        rc = vol_target.risk_contributions(weights, cov)
        # fractional contributions sum to 1 <=> raw contributions sum to w'Sigma w
        assert sum(rc.values()) == pytest.approx(1.0, abs=1e-12)
        w = np.array([0.5, 0.3, 0.2])
        sigma = cov.loc[["S0", "S1", "S2"], ["S0", "S1", "S2"]].to_numpy()
        var = float(w @ sigma @ w)
        raw = {s: rc[s] * var for s in rc}
        assert sum(raw.values()) == pytest.approx(var, rel=1e-12)

    def test_risk_contribution_capper_trims_and_renormalizes(self):
        prices = _synthetic_prices([0.05, 0.01, 0.01, 0.01, 0.01],
                                   n_days=400, seed=5)
        rets = vol_target.daily_log_returns(prices)
        cov = vol_target.ewma_covariance(rets, 30)
        weights = {f"S{i}": 0.2 for i in range(5)}
        before_rc = vol_target.risk_contributions(weights, cov)
        assert before_rc["S0"] > 0.25
        capped, adjs = vol_target.cap_risk_contributions(weights, cov, 0.25)
        after_rc = vol_target.risk_contributions(capped, cov)
        assert after_rc["S0"] <= 0.25 + 1e-4
        assert sum(capped.values()) == pytest.approx(1.0, abs=1e-9)  # gross kept
        assert any(a["symbol"] == "S0" and a["reason"] == "max_risk_contribution"
                   and a["after"] < a["before"] for a in adjs)

    def test_insufficient_history_flags_and_passes_through(self):
        prices = _synthetic_prices([0.02], n_days=5, seed=1)
        rep = vol_target.vol_target_report({"S0": 1.0}, prices,
                                           {"min_observations": 20})
        assert rep["insufficient_history"]
        assert rep["multiplier"] == 1.0
        assert rep["weights"] == {"S0": 1.0}


# ---------------------------------------------------------------------------
# drawdown_control
# ---------------------------------------------------------------------------


def _dates(n, start="2025-01-01"):
    return [str(d)[:10] for d in pd.date_range(start, periods=n, freq="D")]


class TestDrawdownControl:
    CFG = {"state_file_template": ""}  # overwritten per test via tmp_path

    def test_full_cycle_normal_derisked_flat_reentry(self):
        navs = [100.0, 98.0, 89.0,  # -11% -> DERISKED
                85.0, 79.0,          # -21% -> FLAT
                75.0, 80.0,          # trough 75; recovery target 87.5
                88.0,                # >= 87.5 -> step up to DERISKED
                90.0]                # still >= 87.5 -> step up to NORMAL
        series = list(zip(_dates(len(navs)), navs))
        state, transitions = dd.replay(None, series)
        seq = [(t["from"], t["to"]) for t in transitions]
        assert seq == [("NORMAL", "DERISKED"), ("DERISKED", "FLAT"),
                       ("FLAT", "DERISKED"), ("DERISKED", "NORMAL")]
        assert state["state"] == "NORMAL"
        # peak/trough re-anchored on NORMAL re-entry: no instant re-breach
        assert state["peak_nav"] == pytest.approx(90.0)
        assert state["trough_nav"] == pytest.approx(90.0)
        reasons = [t["reason"] for t in transitions]
        assert reasons[0] == "soft_breach" and reasons[1] == "hard_breach"
        assert reasons[2] == "recovered_half_drawdown"

    def test_multipliers(self):
        assert dd.multiplier_for_state("NORMAL") == 1.0
        assert dd.multiplier_for_state("DERISKED") == 0.5
        assert dd.multiplier_for_state("FLAT") == 0.0

    def test_no_flat_flipflop_after_stepdown(self):
        navs = [100.0, 79.0, 75.0, 88.0, 87.0]  # FLAT, trough 75, step up, dip
        series = list(zip(_dates(len(navs)), navs))
        state, transitions = dd.replay(None, series)
        # 87 still -13% from peak (soft zone) but must NOT re-enter FLAT
        # without a new low below the re-entry floor (75)
        assert state["state"] == "DERISKED"
        navs2 = [74.0]  # new low below floor -> back to FLAT
        state2, trans2 = dd.replay(state, list(zip(_dates(1, "2025-02-01"), navs2)))
        assert state2["state"] == "FLAT"
        assert trans2[0]["reason"] == "new_low_below_reentry_floor"

    def test_time_based_reentry_above_ma(self):
        navs = [100.0, 89.0]                      # DERISKED on day 2
        navs += [89.0 + 0.02 * i for i in range(1, 40)]  # slow grind up
        series = list(zip(_dates(len(navs)), navs))
        state, transitions = dd.replay(None, series)
        assert any(t["reason"] == "time_and_above_ma" for t in transitions)
        assert state["state"] == "NORMAL"

    def test_state_persistence_across_instances(self, tmp_path):
        cfg = {"state_file_template": str(tmp_path / "dd_{book}.json")}
        navs1 = [100.0, 95.0, 89.0]
        st, _ = dd.replay(None, list(zip(_dates(3), navs1)))
        path = dd.save_state("bookx", st, cfg)
        assert path.exists()

        # new "instance": load and continue the same path
        loaded = dd.load_state("bookx", cfg)
        assert loaded == st
        assert loaded["state"] == "DERISKED"
        st2, trans = dd.replay(loaded, [("2025-01-04", 79.0)])
        assert st2["state"] == "FLAT"
        dd.save_state("bookx", st2, cfg)
        again = dd.load_state("bookx", cfg)
        assert again["state"] == "FLAT"
        # transition history survives persistence
        assert [t["to"] for t in again["transitions"]] == ["DERISKED", "FLAT"]

    def test_stale_observations_ignored(self):
        st, _ = dd.replay(None, [("2025-01-01", 100.0), ("2025-01-02", 99.0)])
        st2, trans = dd.replay(st, [("2025-01-02", 50.0)])  # not after last_date
        assert trans == []
        assert st2["state"] == "NORMAL"


# ---------------------------------------------------------------------------
# liquidity
# ---------------------------------------------------------------------------


def _market_frame(specs, n_days=40, start="2025-01-01"):
    """specs: {symbol: (close, volume)} constant over n_days."""
    rows = []
    for date in pd.date_range(start, periods=n_days, freq="D"):
        for sym, (close, volume) in specs.items():
            rows.append({"symbol": sym, "date_ts": date,
                         "close": float(close), "volume": float(volume)})
    return pd.DataFrame(rows)


class TestLiquidity:
    def test_compute_adv_exact_on_constant_series(self):
        market = _market_frame({"AAA": (10.0, 1000.0), "BBB": (2.0, 50.0)})
        adv = liquidity.compute_adv(market, window_days=30)
        assert adv["AAA"] == pytest.approx(10_000.0)
        assert adv["BBB"] == pytest.approx(100.0)

    def test_adv_cap_arithmetic(self):
        # ADV 100k, participation 5%, NAV 100k -> max weight 0.05
        capped, adjs = liquidity.cap_weights_by_adv(
            {"AAA": 0.5, "BBB": 0.01}, {"AAA": 100_000.0, "BBB": 100_000.0},
            nav_usd=100_000.0, max_adv_participation=0.05)
        assert capped["AAA"] == pytest.approx(0.05)
        assert capped["BBB"] == pytest.approx(0.01)  # under cap: untouched
        assert len(adjs) == 1 and adjs[0]["symbol"] == "AAA"
        assert adjs[0]["reason"] == "adv_participation_cap"
        assert adjs[0]["before"] == pytest.approx(0.5)
        assert adjs[0]["after"] == pytest.approx(0.05)

    def test_missing_adv_symbol_zeroed(self):
        capped, adjs = liquidity.cap_weights_by_adv({"NEW": 0.2}, {})
        assert capped == {}
        assert adjs[0]["reason"] == "no_adv_data"

    def test_days_to_liquidate_arithmetic(self):
        # position notional = 0.05 * 100k = 5k; capacity = 0.25 * 100k = 25k/day
        rep = liquidity.days_to_liquidate(
            {"AAA": 0.05}, {"AAA": 100_000.0}, nav_usd=100_000.0,
            participation=0.25, book_fractions=(0.25, 0.5, 1.0),
            stressed_volume_divisor=3.0)
        norm = rep["scenarios"]["normal"]
        assert norm["1"]["per_symbol_days"]["AAA"] == pytest.approx(0.2)
        assert norm["0.5"]["per_symbol_days"]["AAA"] == pytest.approx(0.1)
        assert norm["0.25"]["per_symbol_days"]["AAA"] == pytest.approx(0.05)
        stressed = rep["scenarios"]["stressed"]
        assert stressed["1"]["per_symbol_days"]["AAA"] == pytest.approx(0.6)
        assert norm["1"]["book_days"] == pytest.approx(0.2)

    def test_liquidity_report_end_to_end(self):
        market = _market_frame({"AAA": (10.0, 200_000.0)})  # ADV = $2m
        rep = liquidity.liquidity_report(
            {"AAA": 0.5}, market,
            {"adv_window_days": 30, "max_adv_participation": 0.05,
             "nav_usd": 100_000.0})
        # cap = 0.05 * 2m / 100k = 1.0 -> not binding
        assert rep["weights"]["AAA"] == pytest.approx(0.5)
        assert rep["adjustments"] == []
        assert rep["adv_usd"]["AAA"] == pytest.approx(2_000_000.0)


# ---------------------------------------------------------------------------
# risk_pipeline composition
# ---------------------------------------------------------------------------


def _pipeline_cfg(tmp_path):
    return {
        "limits": {"max_single_asset_weight": 0.5, "max_top5_concentration": 1.0,
                   "max_gross_exposure": 1.0, "category_sources": []},
        "liquidity": {"adv_window_days": 30, "max_adv_participation": 0.05,
                      "nav_usd": 100_000.0, "dtl_participation": 0.25,
                      "dtl_book_fractions": [0.25, 0.5, 1.0],
                      "stressed_volume_divisor": 3},
        "vol_target": {"target_vol_annual": 0.15, "ewma_halflife_days": 30,
                       "annualization_days": 365, "min_exposure": 0.2,
                       "max_exposure": 1.0, "min_observations": 20,
                       "max_risk_contribution": 0.25},
        "drawdown": {"state_file_template": str(tmp_path / "dd_{book}.json")},
        "paths": {"output_dir": str(tmp_path)},
    }


class TestRiskPipeline:
    def test_multipliers_combine_via_min_and_audit_complete(self, tmp_path):
        # high-vol prices -> m_vol < 1; NAV path with -15% dd -> m_dd = 0.5
        prices = _synthetic_prices([0.04, 0.04], n_days=200, seed=9)
        market = pd.DataFrame({
            "symbol": np.repeat(["S0", "S1"], len(prices)),
            "date_ts": list(prices.index) * 2,
            "close": np.concatenate([prices["S0"], prices["S1"]]),
            "volume": 1e9,  # effectively unlimited liquidity
        })
        nav = [(d, n) for d, n in zip(
            _dates(4), [100_000.0, 95_000.0, 88_000.0, 85_000.0])]
        res = risk_pipeline.apply_risk_pipeline(
            {"S0": 0.5, "S1": 0.5}, nav, prices, market,
            _pipeline_cfg(tmp_path), book="t1")
        audit = res["audit"]
        m_vol = audit["multipliers"]["vol_target"]
        m_dd = audit["multipliers"]["drawdown"]
        assert m_dd == 0.5  # DERISKED at -15%
        assert 0.2 <= m_vol < 1.0  # 4% daily vol >> 15% target
        assert audit["multipliers"]["combined_min"] == min(m_vol, m_dd)
        # final weights = vol-stage weights * combined multiplier
        w_vol = audit["stages"]["vol_target"]["weights"]
        for s, w in res["weights"].items():
            assert w == pytest.approx(w_vol[s] * min(m_vol, m_dd))
        # audit completeness
        assert set(audit["stages"]) == {"risk_limits", "liquidity",
                                        "vol_target", "drawdown"}
        for stage in ("risk_limits", "liquidity", "vol_target"):
            assert "adjustments" in audit["stages"][stage]
            assert "weights" in audit["stages"][stage]
        assert audit["stages"]["drawdown"]["state"] == "DERISKED"
        assert "days_to_liquidate" in audit["stages"]["liquidity"]
        assert audit["final_cash_weight"] == pytest.approx(
            1.0 - audit["final_gross_exposure"])

    def test_drawdown_dominates_when_flat(self, tmp_path):
        prices = _synthetic_prices([0.005, 0.005], n_days=200, seed=2)
        market = pd.DataFrame({
            "symbol": np.repeat(["S0", "S1"], len(prices)),
            "date_ts": list(prices.index) * 2,
            "close": np.concatenate([prices["S0"], prices["S1"]]),
            "volume": 1e9,
        })
        nav = list(zip(_dates(3), [100_000.0, 90_000.0, 78_000.0]))  # -22%
        res = risk_pipeline.apply_risk_pipeline(
            {"S0": 0.5, "S1": 0.5}, nav, prices, market,
            _pipeline_cfg(tmp_path), book="t2")
        assert res["audit"]["multipliers"]["drawdown"] == 0.0
        assert res["audit"]["multipliers"]["combined_min"] == 0.0
        assert all(w == 0.0 for w in res["weights"].values())
        assert res["audit"]["final_cash_weight"] == pytest.approx(1.0)

    def test_hard_limits_applied_before_scaling(self, tmp_path):
        prices = _synthetic_prices([0.005, 0.005, 0.005], n_days=200, seed=4)
        market = pd.DataFrame({
            "symbol": np.repeat(["S0", "S1", "S2"], len(prices)),
            "date_ts": list(prices.index) * 3,
            "close": np.concatenate([prices[c] for c in ["S0", "S1", "S2"]]),
            "volume": 1e9,
        })
        nav = list(zip(_dates(2), [100_000.0, 100_500.0]))
        cfg = _pipeline_cfg(tmp_path)
        cfg["limits"]["max_single_asset_weight"] = 0.2
        res = risk_pipeline.apply_risk_pipeline(
            {"S0": 0.9, "S1": 0.05, "S2": 0.05}, nav, prices, market,
            cfg, book="t3")
        stage_w = res["audit"]["stages"]["risk_limits"]["weights"]
        assert all(v <= 0.2 + TOL for v in stage_w.values())
        assert any(a["reason"] == "max_single_asset_weight"
                   for a in res["audit"]["stages"]["risk_limits"]["adjustments"])
