"""Offline tests for the CHF operational-continuity + execution-realism layer.

Covers:
  * monitoring/notifier.py  -- degrades to logged-only with no channels,
    builds correct messages from a monitoring report dict.
  * jobs/scheduler.py       -- alert dedup suppresses a repeat within the TTL.
  * portfolio/cost_model.py -- square-root impact math and ADV-table build.

All tests are fully offline (no SMTP, no webhook, no network).
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from monitoring import notifier
from portfolio import cost_model


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_report():
    return {
        "monitor": "data_quality",
        "status": "fail",
        "as_of_utc": "2026-07-06T05:45:00+00:00",
        "n_checks": 3,
        "checks": [
            {"name": "market_staleness", "status": "fail", "value": 5, "threshold": 3, "detail": "max date 5d old"},
            {"name": "nan_close", "status": "warn", "value": 0.02, "threshold": 0.01, "detail": "NaN share of close"},
            {"name": "coverage", "status": "pass", "value": 0.99, "threshold": 0.9, "detail": "ok"},
        ],
    }


@pytest.fixture
def no_channels(monkeypatch):
    """Ensure no SMTP/webhook channel is configured via env."""
    for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM", "SMTP_TO", "WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# notifier: degrade to log when no channel set
# ---------------------------------------------------------------------------
def test_notifier_degrades_to_log_when_unconfigured(no_channels, caplog):
    cfg = notifier.load_config()
    with caplog.at_level("INFO"):
        result = notifier.send("subject", "body line 1\nbody line 2", severity="critical", config=cfg)
    assert result["delivered"] == []
    assert result["logged_only"] is True
    assert result["errors"] == []
    # the alert itself must have been logged
    assert any("ALERT[critical]" in rec.message for rec in caplog.records)


def test_notifier_email_and_webhook_configs_from_env(no_channels, monkeypatch):
    cfg = notifier.load_config()
    # unconfigured
    assert notifier.email_config_from_env(cfg).configured is False
    assert notifier.webhook_config_from_env(cfg).configured is False
    # configured
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.com/xyz")
    ec = notifier.email_config_from_env(cfg)
    assert ec.configured is True
    assert ec.recipients == ["a@example.com", "b@example.com"]
    assert notifier.webhook_config_from_env(cfg).configured is True


# ---------------------------------------------------------------------------
# notifier: builds correct message from a report dict
# ---------------------------------------------------------------------------
def test_notifier_builds_message_from_report(sample_report):
    subject = notifier.build_subject(sample_report, prefix="[CHF]")
    assert subject == "[CHF] data_quality: FAIL"

    text = notifier.build_text_message(sample_report)
    assert "Monitor: data_quality" in text
    assert "Status: FAIL" in text
    # only triggered (warn/fail) checks appear, not the passing one
    assert "market_staleness" in text
    assert "nan_close" in text
    assert "coverage" not in text

    md = notifier.build_markdown_message(sample_report)
    assert "*data_quality*" in md
    assert "`FAIL`" in md


def test_severity_mapping():
    assert notifier.severity_from_status("fail") == "critical"
    assert notifier.severity_from_status("warn") == "warn"
    assert notifier.severity_from_status("pass") == "info"
    assert notifier.severity_from_status(None) == "info"


def test_notifier_min_severity_skips_low(no_channels):
    cfg = notifier.load_config()
    cfg["notifications"]["min_severity"] = "critical"
    # a warn alert is below the critical threshold -> skipped (logged only)
    result = notifier.send("s", "b", severity="warn", config=cfg)
    assert result["skipped"] is True
    assert result["logged_only"] is True
    assert result["delivered"] == []


# ---------------------------------------------------------------------------
# scheduler: dedup suppresses a repeat
# ---------------------------------------------------------------------------
def test_scheduler_dedup_suppresses_repeat(tmp_path, monkeypatch):
    import jobs.scheduler as sched

    monkeypatch.setattr(sched, "ALERT_STATE_PATH", tmp_path / "alert_state.json")

    key = sched._dedup_key("monitor", "data_quality", "fail", "market_staleness")
    ttl = 12.0

    # first time: should send
    assert sched._should_send(key, ttl) is True
    sched._mark_sent(key)
    # immediately after: within TTL -> suppressed
    assert sched._should_send(key, ttl) is False
    # a different alert key is not suppressed
    other = sched._dedup_key("monitor", "signal_health", "warn", "ic_floor")
    assert sched._should_send(other, ttl) is True
    # TTL of 0 hours means never dedup
    assert sched._should_send(key, 0.0) is True


def test_scheduler_dedup_key_stable_and_distinct():
    import jobs.scheduler as sched

    a = sched._dedup_key("monitor", "dq", "fail", "x")
    b = sched._dedup_key("monitor", "dq", "fail", "x")
    c = sched._dedup_key("monitor", "dq", "fail", "y")
    assert a == b
    assert a != c


def test_scheduler_failing_check_names():
    import jobs.scheduler as sched

    report = {
        "checks": [
            {"name": "a", "status": "pass"},
            {"name": "c", "status": "fail"},
            {"name": "b", "status": "warn"},
        ]
    }
    assert sched._failing_check_names(report) == ["b", "c"]  # sorted, passes excluded


# ---------------------------------------------------------------------------
# cost_model: square-root impact math
# ---------------------------------------------------------------------------
def _cfg():
    return cost_model.CostConfig(
        fee_bps=10.0, spread_bps=5.0, impact_k=100.0,
        adv_window_days=30, adv_floor_usd=100_000.0, max_cost_bps=500.0,
    )


def test_cost_zero_participation_is_fee_plus_spread():
    cfg = _cfg()
    b = cost_model.estimate_cost(0.0, adv=5_000_000.0, cfg=cfg)
    assert b["impact_bps"] == 0.0
    assert b["cost_bps"] == cfg.fee_bps + cfg.spread_bps
    assert b["cost_usd"] == 0.0


def test_cost_impact_is_square_root_and_monotonic():
    cfg = _cfg()
    adv = 1_000_000.0
    small = cost_model.estimate_cost(10_000.0, adv, cfg)     # participation 0.01
    large = cost_model.estimate_cost(250_000.0, adv, cfg)    # participation 0.25
    # square-root model: impact_k * sqrt(participation)
    assert small["impact_bps"] == pytest.approx(100.0 * math.sqrt(0.01))   # 10 bps
    assert large["impact_bps"] == pytest.approx(100.0 * math.sqrt(0.25))   # 50 bps
    # larger participation -> strictly higher cost
    assert large["cost_bps"] > small["cost_bps"] > (cfg.fee_bps + cfg.spread_bps)


def test_cost_quadruple_notional_doubles_impact():
    cfg = _cfg()
    adv = 1_000_000.0
    base = cost_model.estimate_cost(50_000.0, adv, cfg)["impact_bps"]
    quad = cost_model.estimate_cost(200_000.0, adv, cfg)["impact_bps"]
    # sqrt(4x) == 2x
    assert quad == pytest.approx(2.0 * base)


def test_cost_adv_floor_prevents_blowup():
    cfg = _cfg()
    # zero ADV should floor to adv_floor_usd, not divide-by-zero
    b = cost_model.estimate_cost(1_000.0, adv=0.0, cfg=cfg)
    assert math.isfinite(b["impact_bps"])
    assert b["participation"] == pytest.approx(1_000.0 / cfg.adv_floor_usd)


def test_cost_is_capped():
    cfg = _cfg()
    # enormous participation -> raw cost exceeds cap
    b = cost_model.estimate_cost(1_000_000_000.0, adv=100_000.0, cfg=cfg)
    assert b["cost_bps"] == cfg.max_cost_bps
    assert b["capped"] is True


def test_cost_config_from_yaml_defaults():
    cfg = cost_model.load_cost_config()
    assert cfg.fee_bps > 0
    assert cfg.spread_bps >= 0
    assert cfg.impact_k > 0
    assert cfg.adv_floor_usd > 0


# ---------------------------------------------------------------------------
# cost_model: ADV-table build on a synthetic market frame
# ---------------------------------------------------------------------------
def _synthetic_market():
    dates = pd.date_range("2024-01-01", periods=40, freq="D", tz="UTC")
    rows = []
    for d in dates:
        rows.append({"date_ts": d, "symbol": "BTC", "close": 100.0, "volume": 1000.0})   # dv = 100k
        rows.append({"date_ts": d, "symbol": "ETH", "close": 10.0, "volume": 5000.0})    # dv = 50k
    return pd.DataFrame(rows)


def test_build_adv_table():
    market = _synthetic_market()
    table = cost_model.build_adv_table(market, window_days=30)
    assert list(table.columns) == ["symbol", "adv_usd", "n_obs", "last_date"]
    # BTC dollar-volume 100k > ETH 50k -> BTC first (sorted desc)
    assert table.iloc[0]["symbol"] == "BTC"
    assert table.iloc[0]["adv_usd"] == pytest.approx(100_000.0)
    eth = table[table["symbol"] == "ETH"].iloc[0]
    assert eth["adv_usd"] == pytest.approx(50_000.0)
    # window caps observations at 30 even though 40 days exist
    assert table.iloc[0]["n_obs"] == 30


def test_build_adv_table_missing_columns_raises():
    bad = pd.DataFrame({"date_ts": [1], "symbol": ["BTC"]})
    with pytest.raises(KeyError):
        cost_model.build_adv_table(bad)


def test_adv_lookup():
    market = _synthetic_market()
    table = cost_model.build_adv_table(market, window_days=30)
    assert cost_model.adv_lookup(table, "ETH") == pytest.approx(50_000.0)
    assert cost_model.adv_lookup(table, "DOGE", default=-1.0) == -1.0
    assert cost_model.adv_lookup(pd.DataFrame(), "BTC", default=None) is None
