"""Offline synthetic tests for the monitoring/ package.

No network, no real pipeline artifacts: every test builds small synthetic
frames (or tmp_path sandboxes) and exercises the monitors' pure functions.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from monitoring import champion_challenger, data_quality, model_decay, risk_report, shadow_nav, signal_health, watchdog

UTC_TODAY = pd.Timestamp.now(tz="UTC").normalize()

DQ_CFG = {
    "market_max_staleness_days": 3,
    "onchain_max_staleness_days": 10,
    "onchain_staleness_severity": "warn",
    "row_drop_warn_pct": 0.10,
    "row_drop_fail_pct": 0.30,
    "baseline_days": 7,
    "nan_close_warn": 0.01,
    "nan_close_fail": 0.05,
    "nan_volume_warn": 0.10,
    "nan_volume_fail": 0.25,
    "spike_sigma": 5.0,
    "spike_sigma_window": 90,
    "spike_lookback_days": 7,
    "spike_warn_count": 1,
    "coverage_drop_fail_pct": 0.10,
}


def _market_panel(n_days: int = 60, symbols=("AAA", "BBB"), end=None, spike_symbol=None) -> pd.DataFrame:
    end = end or UTC_TODAY
    dates = pd.date_range(end=end, periods=n_days, freq="D", tz="UTC")
    rng = np.random.default_rng(7)
    rows = []
    for sym in symbols:
        price = 100.0
        for i, d in enumerate(dates):
            ret = rng.normal(0, 0.005)
            if spike_symbol == sym and i == n_days - 1:
                ret = 0.6  # ~120-sigma log-return spike
            price *= float(np.exp(ret))
            rows.append(
                {
                    "date_ts": d,
                    "symbol": sym,
                    "close": price,
                    "volume": 1e6,
                    "is_universe_member": True,
                }
            )
    return pd.DataFrame(rows)


class TestDataQuality:
    def test_staleness_fail(self):
        stale_end = UTC_TODAY - pd.Timedelta(days=10)
        market = _market_panel(end=stale_end)
        checks = data_quality.run_market_checks(market, DQ_CFG, UTC_TODAY)
        by_name = {c["name"]: c for c in checks}
        assert by_name["market_staleness"]["status"] == "fail"

    def test_fresh_panel_passes(self):
        checks = data_quality.run_market_checks(_market_panel(), DQ_CFG, UTC_TODAY)
        by_name = {c["name"]: c for c in checks}
        assert by_name["market_staleness"]["status"] == "pass"
        assert by_name["market_duplicate_keys"]["status"] == "pass"
        assert by_name["market_return_spikes"]["status"] == "pass"

    def test_spike_flagged(self):
        market = _market_panel(spike_symbol="AAA")
        checks = data_quality.run_market_checks(market, DQ_CFG, UTC_TODAY)
        spike = next(c for c in checks if c["name"] == "market_return_spikes")
        assert spike["status"] == "warn"
        assert spike["value"] >= 1
        assert "AAA" in spike["detail"]

    def test_duplicates_fail_and_verdict(self):
        market = _market_panel()
        market = pd.concat([market, market.tail(1)], ignore_index=True)
        checks = data_quality.run_market_checks(market, DQ_CFG, UTC_TODAY)
        dup = next(c for c in checks if c["name"] == "market_duplicate_keys")
        assert dup["status"] == "fail"
        assert data_quality.build_verdict(checks)["status"] == "fail"

    def test_missing_input_fails(self):
        checks = data_quality.run_market_checks(None, DQ_CFG, UTC_TODAY)
        assert data_quality.build_verdict(checks)["status"] == "fail"


class TestSignalHealth:
    @staticmethod
    def _panel(ic_sign: int, n_days: int = 40, n_names: int = 10) -> pd.DataFrame:
        dates = pd.date_range("2025-01-01", periods=n_days, freq="D", tz="UTC")
        rows = []
        for d in dates:
            actual = np.linspace(-0.05, 0.05, n_names)
            pred = actual * ic_sign  # perfect (anti-)ranking
            for i in range(n_names):
                rows.append(
                    {
                        "model_name": "m1",
                        "horizon_days": 7,
                        "feature_set": "fs",
                        "date_ts": d,
                        "symbol": f"S{i}",
                        "prediction": pred[i],
                        "actual_forward_return": actual[i],
                    }
                )
        return pd.DataFrame(rows)

    def test_perfect_ic_is_one(self):
        cfg = {"rolling_windows": [30, 90], "ic_floor_30d": 0.0, "min_names_per_day": 5, "min_days": 10}
        summary = signal_health.evaluate(self._panel(+1), cfg)
        assert summary["status"] == "ok"
        assert summary["models"]["m1"]["mean_ic_30d"] == pytest.approx(1.0)

    def test_negative_ic_alerts_below_floor(self):
        cfg = {"rolling_windows": [30, 90], "ic_floor_30d": 0.0, "min_names_per_day": 5, "min_days": 10}
        summary = signal_health.evaluate(self._panel(-1), cfg)
        assert summary["status"] == "alert"
        assert summary["models"]["m1"]["mean_ic_30d"] == pytest.approx(-1.0)
        assert summary["models"]["m1"]["below_floor"] is True

    def test_daily_ic_respects_min_names(self):
        panel = self._panel(+1, n_names=3)  # below min_names_per_day
        daily = signal_health.daily_rank_ic(panel, min_names=5)
        assert daily.empty


class TestModelDecayCusum:
    def test_no_alarm_on_noise(self):
        rng = np.random.default_rng(42)
        z = rng.normal(0.0, 1.0, size=200)
        _, _, alarms = model_decay.cusum_drift(z, k=0.5, h=5.0)
        assert alarms == []

    def test_alarm_on_injected_drift(self):
        rng = np.random.default_rng(42)
        z = np.concatenate([rng.normal(0.0, 1.0, 100), rng.normal(-2.0, 1.0, 30)])
        pos, neg, alarms = model_decay.cusum_drift(z, k=0.5, h=5.0)
        assert alarms and alarms[0] >= 100
        # the alarm was raised by the statistic crossing h
        assert (neg[alarms[0]] > 5.0) or (pos[alarms[0]] > 5.0)
        # the statistic resets to zero on the sample after the alarm
        if alarms[0] + 1 < len(z):
            assert pos[alarms[0] + 1] <= abs(z[alarms[0] + 1]) + 0.5
            assert neg[alarms[0] + 1] <= abs(z[alarms[0] + 1]) + 0.5

    def test_evaluate_alerts_on_decayed_live_returns(self):
        rng = np.random.default_rng(1)
        expected = pd.Series(rng.normal(0.001, 0.01, 400))
        live = pd.Series(rng.normal(-0.02, 0.01, 40))  # strong negative drift
        cfg = {"min_days": 10, "cusum_k": 0.5, "cusum_h": 5.0, "sharpe_delta_alert": -1.0}
        out = model_decay.evaluate(live, expected, cfg)
        assert out["status"] == "alert"
        assert out["cusum_alarm_count"] >= 1

    def test_evaluate_ok_on_matching_live_returns(self):
        rng = np.random.default_rng(2)
        expected = pd.Series(rng.normal(0.001, 0.01, 400))
        live = pd.Series(rng.normal(0.001, 0.01, 40))
        cfg = {"min_days": 10, "cusum_k": 0.5, "cusum_h": 5.0, "sharpe_delta_alert": -3.0}
        out = model_decay.evaluate(live, expected, cfg)
        assert out["status"] == "ok"

    def test_insufficient_data(self):
        expected = pd.Series(np.random.default_rng(3).normal(0, 0.01, 100))
        out = model_decay.evaluate(pd.Series([0.001, 0.002]), expected, {"min_days": 10})
        assert out["status"] == "insufficient_data"


class TestRiskReport:
    def test_risk_contributions_sum_to_portfolio_variance(self):
        rng = np.random.default_rng(0)
        returns = pd.DataFrame(rng.normal(0, 0.02, size=(120, 4)), columns=list("ABCD"))
        cov = risk_report.ewma_cov(returns, lam=0.94)
        weights = pd.Series({"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1})
        decomp = risk_report.risk_contributions(weights, cov)
        total = sum(c["variance_contribution"] for c in decomp["contributions"].values())
        assert total == pytest.approx(decomp["portfolio_variance_daily"], rel=1e-9)
        share_sum = sum(c["share"] for c in decomp["contributions"].values())
        assert share_sum == pytest.approx(1.0, rel=1e-9)

    def test_ols_beta_of_benchmark_is_one(self):
        rng = np.random.default_rng(5)
        btc = pd.Series(rng.normal(0, 0.03, 60))
        stats = risk_report.ols_beta(btc, btc)
        assert stats["beta"] == pytest.approx(1.0)
        assert stats["correlation"] == pytest.approx(1.0)


class TestShadowNav:
    def test_divergence_detection(self):
        state = {"cash": 100.0, "positions": {"AAA": 10.0}}
        prices = pd.Series({"AAA": 10.0})
        out = shadow_nav.shadow_nav(state, prices)
        assert out["shadow_nav"] == pytest.approx(200.0)
        assert out["unpriced_symbols"] == []
        # 25 bps off -> above the 10 bps alert threshold
        div = shadow_nav.divergence_bps(out["shadow_nav"], 200.5)
        assert abs(div) > 10.0
        # within tolerance
        div_ok = shadow_nav.divergence_bps(out["shadow_nav"], 200.01)
        assert abs(div_ok) < 10.0

    def test_unpriced_symbols_reported(self):
        state = {"cash": 0.0, "positions": {"ZZZ": 1.0}}
        out = shadow_nav.shadow_nav(state, pd.Series({"AAA": 10.0}))
        assert out["unpriced_symbols"] == ["ZZZ"]
        assert out["shadow_nav"] == pytest.approx(0.0)


class TestChampionChallenger:
    CFG = {"k_consecutive": 3, "sharpe_window": 60, "exclude_books": ["benchmark_btc"]}

    def _store(self, tmp_path):
        from orchestration.state_store import StateStore

        return StateStore(tmp_path / "memory")

    def test_pending_approval_after_k_consecutive_wins(self, tmp_path):
        store = self._store(tmp_path)
        scores = {
            "canonical_best_model": {"sharpe_60d": 0.5},
            "challenger_x": {"sharpe_60d": 1.5},
            "benchmark_btc": {"sharpe_60d": 9.9},  # excluded
        }
        state = {"streaks": {"challenger_x": {"consecutive_wins": 2}}}  # stubbed K-1 wins
        result = champion_challenger.evaluate(scores, "canonical_best_model", state, self.CFG, store)
        assert len(result["proposals"]) == 1
        proposal = result["proposals"][0]
        assert proposal["challenger"] == "challenger_x"
        assert proposal["approval_status"] == "pending"
        doc = json.loads((tmp_path / "memory" / "approvals.json").read_text())
        approvals = doc["approvals"]
        assert len(approvals) == 1
        assert approvals[0]["step_id"] == "champion_promotion:challenger_x"
        assert approvals[0]["status"] == "pending"
        # never auto-promoted: only a pending record exists
        assert approvals[0]["decided_utc"] is None

    def test_no_proposal_before_k_wins(self, tmp_path):
        store = self._store(tmp_path)
        scores = {
            "canonical_best_model": {"sharpe_60d": 0.5},
            "challenger_x": {"sharpe_60d": 1.5},
        }
        state = {}
        result = champion_challenger.evaluate(scores, "canonical_best_model", state, self.CFG, store)
        assert result["proposals"] == []
        assert state["streaks"]["challenger_x"]["consecutive_wins"] == 1
        assert not (tmp_path / "memory" / "approvals.json").exists()

    def test_loss_resets_streak(self, tmp_path):
        store = self._store(tmp_path)
        scores = {
            "canonical_best_model": {"sharpe_60d": 2.0},
            "challenger_x": {"sharpe_60d": 1.0},
        }
        state = {"streaks": {"challenger_x": {"consecutive_wins": 2}}}
        result = champion_challenger.evaluate(scores, "canonical_best_model", state, self.CFG, store)
        assert result["proposals"] == []
        assert state["streaks"]["challenger_x"]["consecutive_wins"] == 0


class TestWatchdog:
    CFG = {"daily_max_age_days": 1, "weekly_max_age_days": 7, "monthly_max_age_days": 31}

    def test_stale_daily_artifact_detected(self):
        now = datetime.now(timezone.utc)
        old_mtime = (now - timedelta(days=3)).timestamp()
        row = watchdog.check_stage("labels", "daily", old_mtime, True, self.CFG, now)
        assert row["status"] == "stale"

    def test_fresh_daily_artifact_ok(self):
        now = datetime.now(timezone.utc)
        row = watchdog.check_stage("market", "daily", now.timestamp(), True, self.CFG, now)
        assert row["status"] == "ok"

    def test_weekly_and_monthly_windows(self):
        now = datetime.now(timezone.utc)
        five_days = (now - timedelta(days=5)).timestamp()
        assert watchdog.check_stage("model", "weekly", five_days, True, self.CFG, now)["status"] == "ok"
        ten_days = (now - timedelta(days=10)).timestamp()
        assert watchdog.check_stage("model", "weekly", ten_days, True, self.CFG, now)["status"] == "stale"
        assert watchdog.check_stage("universe", "monthly", ten_days, True, self.CFG, now)["status"] == "ok"

    def test_missing_output_and_manual_stage(self):
        now = datetime.now(timezone.utc)
        assert watchdog.check_stage("features", "daily", None, False, self.CFG, now)["status"] == "missing"
        # BacktestAgent is never scheduled: manual stages never alert
        assert watchdog.check_stage("backtest", "manual", None, False, self.CFG, now)["status"] == "manual"
