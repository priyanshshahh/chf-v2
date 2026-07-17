"""Offline tests for the CHF fund-accounting layer (accounting/ package).

Everything runs against synthetic books under tmp_path — no network, no
touching the real data/ tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from accounting import audit_log, fees, ledger, nav, reconcile

MGMT_CONFIG = {
    "management_fee_annual": 0.02,
    "performance_fee_rate": 0.20,
    "day_count": 365,
}
NO_MGMT_CONFIG = {
    "management_fee_annual": 0.0,
    "performance_fee_rate": 0.20,
    "day_count": 365,
}


def _event(event_id: str, **kwargs) -> ledger.LedgerEvent:
    base = dict(
        event_id=event_id,
        ts_utc="2026-01-01T00:00:00+00:00",
        book="alpha",
        event_type="cash_flow",
        notional=100.0,
    )
    base.update(kwargs)
    return ledger.LedgerEvent(**base)


# -- ledger --------------------------------------------------------------------


def test_ledger_append_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "ledger.parquet"
    events = [_event("e1"), _event("e2", event_type="fill", symbol="BTC", qty=1.0, price=10.0)]
    assert ledger.append(events, path) == 2
    assert ledger.append(events, path) == 0  # exact re-append: no-op
    # Same id with different payload must NOT rewrite the immutable row.
    assert ledger.append([_event("e1", notional=999.0)], path) == 0
    df = ledger.read_ledger(path)
    assert len(df) == 2
    assert float(df[df["event_id"] == "e1"].iloc[0]["notional"]) == 100.0


def test_ledger_rejects_invalid_event_type() -> None:
    with pytest.raises(ValueError, match="invalid event_type"):
        _event("bad", event_type="withdrawal")


def test_ledger_read_filters(tmp_path: Path) -> None:
    path = tmp_path / "ledger.parquet"
    ledger.append(
        [
            _event("a", book="alpha"),
            _event("b", book="beta"),
            _event("c", book="alpha", event_type="mark", symbol="BTC", price=10.0),
        ],
        path,
    )
    assert len(ledger.read_ledger(path, book="alpha")) == 2
    assert len(ledger.read_ledger(path, event_types=["mark"])) == 1
    assert ledger.read_ledger(tmp_path / "missing.parquet").empty


# -- independent NAV -------------------------------------------------------------


def test_nav_rebuild_matches_hand_computed_example(tmp_path: Path) -> None:
    path = tmp_path / "ledger.parquet"
    ledger.append(
        [
            _event("fund", notional=100_000.0),
            _event(
                "f1",
                event_type="fill",
                symbol="BTC",
                qty=2.0,
                price=100.0,
                notional=200.0,
                details_json=ledger.details(side="buy", cost_usd=2.0),
            ),
            _event("m1", event_type="mark", symbol="BTC", price=110.0),
            _event(
                "m2",
                event_type="mark",
                symbol="BTC",
                price=120.0,
                ts_utc="2026-01-02T00:00:00+00:00",
            ),
        ],
        path,
    )
    series = nav.compute_nav_series(ledger.read_ledger(path), "alpha")
    # Day 1: cash = 100000 - (200 + 2) = 99798; positions_value = 2 x 110 = 220.
    d1 = series.iloc[0]
    assert d1["date"] == "2026-01-01"
    assert d1["cash"] == pytest.approx(99_798.0)
    assert d1["positions_value"] == pytest.approx(220.0)
    assert d1["nav"] == pytest.approx(100_018.0)
    # Day 2: same cash, marked at 120 -> 99798 + 240.
    d2 = series.iloc[1]
    assert d2["nav"] == pytest.approx(100_038.0)
    assert int(d2["n_positions"]) == 1


def test_nav_missing_mark_raises(tmp_path: Path) -> None:
    path = tmp_path / "ledger.parquet"
    ledger.append(
        [
            _event("fund", notional=1000.0),
            _event(
                "f1",
                event_type="fill",
                symbol="BTC",
                qty=1.0,
                price=100.0,
                notional=100.0,
                details_json=ledger.details(cost_usd=1.0),
            ),
        ],
        path,
    )
    with pytest.raises(ValueError, match="missing_price:alpha:2026-01-01:BTC"):
        nav.compute_nav_series(ledger.read_ledger(path), "alpha")


# -- management fee ---------------------------------------------------------------


def test_mgmt_fee_daily_365_accrual() -> None:
    gross = pd.DataFrame(
        {"date": ["2026-01-01", "2026-01-02", "2026-01-03"], "nav": [100_000.0] * 3}
    )
    series, _ = fees.apply_fees(gross, "alpha", MGMT_CONFIG)
    daily = 100_000.0 * 0.02 / 365
    assert series["mgmt_fee_accrual"].tolist() == pytest.approx([daily] * 3)
    assert series.iloc[-1]["mgmt_fee_cum"] == pytest.approx(3 * daily)
    assert series.iloc[-1]["net_nav"] == pytest.approx(100_000.0 - 3 * daily)
    assert series["perf_fee"].sum() == 0.0  # trailing partial month never crystallizes


def test_mgmt_fee_scales_with_calendar_gap() -> None:
    gross = pd.DataFrame({"date": ["2026-01-01", "2026-01-04"], "nav": [100_000.0, 100_000.0]})
    series, _ = fees.apply_fees(gross, "alpha", MGMT_CONFIG)
    daily = 100_000.0 * 0.02 / 365
    assert series.iloc[0]["mgmt_fee_accrual"] == pytest.approx(daily)
    assert series.iloc[1]["mgmt_fee_accrual"] == pytest.approx(3 * daily)


# -- performance fee / high-water mark --------------------------------------------


def test_perf_fee_only_above_hwm_with_ratchet_and_no_clawback() -> None:
    gross = pd.DataFrame(
        {
            "date": ["2026-01-15", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-10"],
            "nav": [100.0, 110.0, 105.0, 120.0, 130.0],
        }
    )
    series, hwm_entry = fees.apply_fees(gross, "alpha", NO_MGMT_CONFIG)

    # Inception HWM = first net NAV = 100.
    assert series.iloc[0]["perf_fee"] == 0.0
    assert series.iloc[0]["hwm"] == pytest.approx(100.0)

    # Jan month-end: 20% of (110 - 100) = 2; net 108; HWM ratchets to 108.
    jan = series.iloc[1]
    assert jan["perf_fee"] == pytest.approx(2.0)
    assert jan["net_nav"] == pytest.approx(108.0)
    assert jan["hwm"] == pytest.approx(108.0)

    # Feb month-end: net-before-perf 103 < HWM 108 -> no fee, no clawback.
    feb = series.iloc[2]
    assert feb["perf_fee"] == 0.0
    assert feb["hwm"] == pytest.approx(108.0)
    assert feb["perf_fee_cum"] == pytest.approx(2.0)  # earlier fee is never returned

    # Mar month-end: net-before-perf 118 > 108 -> fee on the 10 above HWM only.
    mar = series.iloc[3]
    assert mar["perf_fee"] == pytest.approx(2.0)
    assert mar["hwm"] == pytest.approx(116.0)
    assert mar["net_nav"] == pytest.approx(116.0)

    # Trailing partial month (April): accrues nothing, HWM untouched.
    apr = series.iloc[4]
    assert apr["perf_fee"] == 0.0
    assert apr["hwm"] == pytest.approx(116.0)

    assert hwm_entry["hwm"] == pytest.approx(116.0)
    assert hwm_entry["last_crystallization"] == "2026-03-31"


def test_hwm_state_round_trip(tmp_path: Path) -> None:
    hwm_path = tmp_path / "hwm.json"
    fees.update_hwm_state("alpha", {"hwm": 108.0, "as_of": "2026-01-31"}, hwm_path)
    fees.update_hwm_state("beta", {"hwm": 99.0, "as_of": "2026-01-31"}, hwm_path)
    state = fees.load_hwm_state(hwm_path)
    assert state["alpha"]["hwm"] == 108.0
    assert state["beta"]["hwm"] == 99.0


# -- reconciliation ----------------------------------------------------------------


def _write_book(
    root: Path,
    name: str,
    equity_rows: list,
    fill_rows: list,
) -> None:
    book_dir = root / name
    book_dir.mkdir(parents=True)
    pd.DataFrame(
        equity_rows,
        columns=["date", "nav", "cash", "positions_value", "daily_return", "cum_return"],
    ).to_parquet(book_dir / "equity.parquet", index=False)
    pd.DataFrame(
        fill_rows,
        columns=["date", "symbol", "side", "qty", "price", "notional", "cost_usd", "reason"],
    ).to_parquet(book_dir / "fills.parquet", index=False)


def _write_cache(cache_dir: Path, date: str, prices: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"symbol": s.lower(), "current_price": p} for s, p in prices.items()]
    (cache_dir / f"markets_{date.replace('-', '')}_test.json").write_text(json.dumps(rows))


@pytest.fixture()
def recon_env(tmp_path: Path) -> dict:
    """Three synthetic books: one clean, one missing a fill, one mispriced."""
    papertrade_dir = tmp_path / "papertrade"
    cache_dir = tmp_path / "coingecko"

    # ok_book: buy 10 BTC @ 100 (cost 10) on d1; marked at 110 on d2.
    _write_book(
        papertrade_dir,
        "ok_book",
        equity_rows=[
            ["2026-01-01", 99_990.0, 98_990.0, 1_000.0, 0.0, -0.0001],
            ["2026-01-02", 100_090.0, 98_990.0, 1_100.0, 0.001, 0.0009],
        ],
        fill_rows=[["2026-01-01", "BTC", "buy", 10.0, 100.0, 1_000.0, 10.0, "rebalance"]],
    )
    _write_cache(cache_dir, "2026-01-02", {"BTC": 110.0})

    # missing_fill_book: engine cash/positions reflect a trade absent from fills.
    _write_book(
        papertrade_dir,
        "missing_fill_book",
        equity_rows=[["2026-01-01", 99_900.0, 89_900.0, 10_000.0, 0.0, -0.001]],
        fill_rows=[],
    )

    # price_book: engine marked BTC at 110 on d2, independent source says 120.
    _write_book(
        papertrade_dir,
        "price_book",
        equity_rows=[
            ["2026-01-01", 99_990.0, 98_990.0, 1_000.0, 0.0, -0.0001],
            ["2026-01-03", 100_090.0, 98_990.0, 1_100.0, 0.001, 0.0009],
        ],
        fill_rows=[["2026-01-01", "BTC", "buy", 10.0, 100.0, 1_000.0, 10.0, "rebalance"]],
    )
    _write_cache(cache_dir, "2026-01-03", {"BTC": 120.0})

    return {
        "papertrade_dir": papertrade_dir,
        "ledger_path": tmp_path / "ledger.parquet",
        "out_dir": tmp_path / "accounting",
        "config_path": tmp_path / "no_config.yaml",  # defaults: tolerance 1 bp
        "run_config_path": tmp_path / "no_run_config.yaml",  # default 100k funding
        "coingecko_cache_dir": cache_dir,
        "external_prices_path": tmp_path / "no_external.parquet",
        "audit_log_path": tmp_path / "audit.log",
    }


def test_reconciliation_clean_book_is_ok(recon_env: dict) -> None:
    report = reconcile.run_reconciliation(books=["ok_book"], **recon_env)
    result = report["ok_book"]
    assert result["status"] == "OK"
    assert result["break_classification"] is None
    assert result["days_ok"] == 2
    assert result["max_divergence_bps"] == pytest.approx(0.0, abs=1e-6)
    assert result["engine_nav"] == pytest.approx(100_090.0)
    assert result["shadow_nav"] == pytest.approx(100_090.0)
    report_path = recon_env["out_dir"] / "reconciliation_report.json"
    assert json.loads(report_path.read_text())["ok_book"]["status"] == "OK"


def test_reconciliation_import_is_idempotent(recon_env: dict) -> None:
    first = reconcile.run_reconciliation(books=["ok_book"], **recon_env)
    assert first["ok_book"]["ledger_rows_appended"] > 0
    second = reconcile.run_reconciliation(books=["ok_book"], **recon_env)
    assert second["ok_book"]["ledger_rows_appended"] == 0
    assert second["ok_book"]["status"] == "OK"


def test_reconciliation_catches_missing_fill(recon_env: dict) -> None:
    report = reconcile.run_reconciliation(books=["missing_fill_book"], **recon_env)
    result = report["missing_fill_book"]
    assert result["status"] == "BREAK"
    assert result["break_classification"] == "missing_fill"
    assert result["divergence_bps"] > 1.0
    assert result["shadow_nav"] == pytest.approx(100_000.0)  # funding only, no trade seen


def test_reconciliation_catches_price_mismatch(recon_env: dict) -> None:
    report = reconcile.run_reconciliation(books=["price_book"], **recon_env)
    result = report["price_book"]
    assert result["status"] == "BREAK"
    assert result["break_classification"] == "price_mismatch"
    assert result["date"] == "2026-01-03"
    assert result["shadow_nav"] == pytest.approx(98_990.0 + 10 * 120.0)


def test_reconciliation_discovers_all_books(recon_env: dict) -> None:
    assert reconcile.discover_books(recon_env["papertrade_dir"]) == [
        "missing_fill_book",
        "ok_book",
        "price_book",
    ]
    report = reconcile.run_reconciliation(**recon_env)
    assert report["ok_book"]["status"] == "OK"
    assert report["missing_fill_book"]["break_classification"] == "missing_fill"
    assert report["price_book"]["break_classification"] == "price_mismatch"
    ok, err = audit_log.verify_chain(recon_env["audit_log_path"])
    assert ok, err


# -- end-to-end NAV CLI path --------------------------------------------------------


def test_build_book_nav_writes_gross_and_net(recon_env: dict, tmp_path: Path) -> None:
    reconcile.run_reconciliation(books=["ok_book"], **recon_env)
    config_path = tmp_path / "accounting.yaml"
    config_path.write_text(
        "accounting:\n"
        "  management_fee_annual: 0.02\n"
        "  performance_fee_rate: 0.20\n"
        "  day_count: 365\n"
        f"  hwm_path: {tmp_path / 'hwm.json'}\n"
    )
    series = nav.build_book_nav(
        "ok_book",
        ledger_path=recon_env["ledger_path"],
        out_dir=recon_env["out_dir"],
        config_path=config_path,
        audit_log_path=recon_env["audit_log_path"],
    )
    assert {"date", "nav", "net_nav", "mgmt_fee_cum", "hwm"} <= set(series.columns)
    assert (series["net_nav"] < series["nav"]).all()  # fees always drag
    out = pd.read_parquet(recon_env["out_dir"] / "nav_ok_book.parquet")
    assert len(out) == 2
    assert fees.load_hwm_state(tmp_path / "hwm.json")["ok_book"]["hwm"] is not None
    # Fee events landed in the ledger as typed rows.
    fee_rows = ledger.read_ledger(recon_env["ledger_path"], event_types=["fee_accrual"])
    assert len(fee_rows) == 2


# -- audit log -----------------------------------------------------------------------


def test_audit_chain_verifies_and_detects_tampering(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    audit_log.log_event("tester", "action_one", {"x": 1}, log_path)
    audit_log.log_event("tester", "action_two", {"x": 2}, log_path)
    audit_log.log_event("tester", "action_three", {"x": 3}, log_path)
    ok, err = audit_log.verify_chain(log_path)
    assert ok and err is None

    lines = log_path.read_text().splitlines()

    # Tamper with a field in the middle entry.
    entry = json.loads(lines[1])
    entry["action"] = "action_two_FORGED"
    forged = lines[:1] + [json.dumps(entry, sort_keys=True, separators=(",", ":"))] + lines[2:]
    log_path.write_text("\n".join(forged) + "\n")
    ok, err = audit_log.verify_chain(log_path)
    assert not ok and "line 2" in err

    # Delete the middle entry: the chain must break at the successor.
    log_path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    ok, err = audit_log.verify_chain(log_path)
    assert not ok and "prev_hash mismatch" in err


def test_audit_chain_empty_and_genesis(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    ok, err = audit_log.verify_chain(log_path)  # absent file: trivially valid
    assert ok and err is None
    entry = audit_log.log_event("tester", "first", {}, log_path)
    assert entry["prev_hash"] == audit_log.GENESIS_HASH
    assert audit_log.verify_chain(log_path) == (True, None)
