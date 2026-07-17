"""Pre-flight data quality gate for market/on-chain inputs.

Checks: staleness, latest-day row-count delta vs a 7-day baseline, NaN rates,
duplicate (symbol, date) keys, >k-sigma daily log-return spikes and universe
coverage drops. Writes the readiness verdict to
``data/readiness/daily_quality_verdict.json`` plus a JSON/parquet report under
``data/reports/monitoring/``. Exit code 1 when the verdict is ``fail``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from monitoring.common import (
    build_arg_parser,
    exit_code,
    load_config,
    read_parquet_safe,
    resolve_root,
    utc_today,
    utcnow_iso,
    write_json,
    write_report,
)

_SEVERITY_ORDER = {"pass": 0, "warn": 1, "fail": 2}


def _check(name: str, status: str, value: Any, threshold: Any, detail: str) -> Dict[str, Any]:
    return {"name": name, "status": status, "value": value, "threshold": threshold, "detail": detail}


def _staleness_check(
    name: str, max_date: Optional[pd.Timestamp], max_days: float, severity: str, today: pd.Timestamp
) -> Dict[str, Any]:
    if max_date is None:
        return _check(name, "fail", None, max_days, "input file missing or empty")
    age_days = float((today - max_date.normalize()).days)
    status = "pass" if age_days <= max_days else severity
    return _check(name, status, age_days, max_days, f"max date {max_date.date()} is {age_days:.0f}d old")


def run_market_checks(market: Optional[pd.DataFrame], cfg: Dict[str, Any], today: pd.Timestamp) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    if market is None or market.empty:
        checks.append(_check("market_present", "fail", 0, ">0 rows", "market file missing or empty"))
        return checks

    market = market.copy()
    market["date_ts"] = pd.to_datetime(market["date_ts"], utc=True)
    max_date = market["date_ts"].max()
    checks.append(
        _staleness_check("market_staleness", max_date, float(cfg["market_max_staleness_days"]), "fail", today)
    )

    # row-count delta vs baseline of the preceding N distinct dates
    counts = market.groupby(market["date_ts"].dt.normalize()).size().sort_index()
    baseline_days = int(cfg.get("baseline_days", 7))
    if len(counts) >= 2:
        latest = float(counts.iloc[-1])
        baseline = counts.iloc[:-1].tail(baseline_days)
        base_mean = float(baseline.mean()) if len(baseline) else latest
        drop_pct = 0.0 if base_mean <= 0 else max(0.0, 1.0 - latest / base_mean)
        if drop_pct >= float(cfg["row_drop_fail_pct"]):
            status = "fail"
        elif drop_pct >= float(cfg["row_drop_warn_pct"]):
            status = "warn"
        else:
            status = "pass"
        checks.append(
            _check(
                "market_row_count_delta",
                status,
                round(drop_pct, 4),
                cfg["row_drop_warn_pct"],
                f"latest day {latest:.0f} rows vs {baseline_days}d baseline {base_mean:.1f}",
            )
        )

    # NaN rates on the latest day
    latest_rows = market[market["date_ts"].dt.normalize() == max_date.normalize()]
    for col, warn_key, fail_key in (
        ("close", "nan_close_warn", "nan_close_fail"),
        ("volume", "nan_volume_warn", "nan_volume_fail"),
    ):
        if col not in latest_rows.columns or latest_rows.empty:
            continue
        rate = float(latest_rows[col].isna().mean())
        if rate >= float(cfg[fail_key]):
            status = "fail"
        elif rate >= float(cfg[warn_key]):
            status = "warn"
        else:
            status = "pass"
        checks.append(
            _check(f"market_nan_rate_{col}", status, round(rate, 4), cfg[warn_key], f"NaN share of {col} on latest day")
        )

    # duplicate (symbol, date) keys anywhere in the panel
    dup_count = int(market.duplicated(subset=["symbol", "date_ts"]).sum())
    checks.append(
        _check(
            "market_duplicate_keys",
            "pass" if dup_count == 0 else "fail",
            dup_count,
            0,
            "duplicated (symbol, date_ts) rows",
        )
    )

    # >k-sigma daily log-return spikes over the recent lookback
    sigma_k = float(cfg["spike_sigma"])
    sigma_window = int(cfg["spike_sigma_window"])
    lookback = int(cfg["spike_lookback_days"])
    panel = market.sort_values(["symbol", "date_ts"])
    close = panel["close"].where(panel["close"] > 0)
    log_ret = np.log(close).groupby(panel["symbol"]).diff()
    sigma = log_ret.groupby(panel["symbol"]).transform(
        lambda s: s.rolling(sigma_window, min_periods=20).std().shift(1)
    )
    recent_cutoff = max_date.normalize() - pd.Timedelta(days=lookback - 1)
    is_recent = panel["date_ts"].dt.normalize() >= recent_cutoff
    spikes = panel[is_recent & sigma.notna() & (log_ret.abs() > sigma_k * sigma)]
    spike_count = int(len(spikes))
    spike_syms = sorted(spikes["symbol"].unique().tolist())[:20]
    checks.append(
        _check(
            "market_return_spikes",
            "warn" if spike_count >= int(cfg["spike_warn_count"]) else "pass",
            spike_count,
            f">{sigma_k}sigma",
            f"spikes in last {lookback}d: {spike_syms}",
        )
    )

    # universe coverage drop vs baseline
    if "is_universe_member" in market.columns:
        cov = (
            market[market["is_universe_member"].fillna(False)]
            .groupby(market["date_ts"].dt.normalize())["symbol"]
            .nunique()
            .sort_index()
        )
        if len(cov) >= 2:
            latest_cov = float(cov.iloc[-1])
            base_cov = float(cov.iloc[:-1].tail(baseline_days).mean())
            drop = 0.0 if base_cov <= 0 else max(0.0, 1.0 - latest_cov / base_cov)
            status = "fail" if drop > float(cfg["coverage_drop_fail_pct"]) else "pass"
            checks.append(
                _check(
                    "universe_coverage_drop",
                    status,
                    round(drop, 4),
                    cfg["coverage_drop_fail_pct"],
                    f"universe members {latest_cov:.0f} vs baseline {base_cov:.1f}",
                )
            )
    return checks


def run_onchain_checks(onchain: Optional[pd.DataFrame], cfg: Dict[str, Any], today: pd.Timestamp) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    severity = str(cfg.get("onchain_staleness_severity", "warn"))
    if onchain is None or onchain.empty:
        checks.append(_check("onchain_present", severity, 0, ">0 rows", "onchain file missing or empty"))
        return checks
    max_date = pd.to_datetime(onchain["date_ts"], utc=True).max()
    checks.append(
        _staleness_check("onchain_staleness", max_date, float(cfg["onchain_max_staleness_days"]), severity, today)
    )
    dup = int(onchain.duplicated(subset=["symbol", "date_ts"]).sum()) if "symbol" in onchain.columns else 0
    checks.append(
        _check("onchain_duplicate_keys", "pass" if dup == 0 else "fail", dup, 0, "duplicated (symbol, date_ts) rows")
    )
    return checks


def build_verdict(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    worst = max((_SEVERITY_ORDER[c["status"]] for c in checks), default=0)
    status = {0: "pass", 1: "warn", 2: "fail"}[worst]
    return {"status": status, "as_of_utc": utcnow_iso(), "n_checks": len(checks), "checks": checks}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF data quality pre-flight gate").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("data_quality", {})
    today = utc_today()

    market = read_parquet_safe(
        root / paths["market"],
        columns=["date_ts", "symbol", "close", "volume", "is_universe_member"],
    )
    onchain = read_parquet_safe(root / paths["onchain"], columns=["date_ts", "symbol"])

    checks = run_market_checks(market, cfg, today) + run_onchain_checks(onchain, cfg, today)
    verdict = build_verdict(checks)

    write_json(root / paths["readiness_verdict"], verdict)
    table = pd.DataFrame(checks)
    table["as_of_utc"] = verdict["as_of_utc"]
    written = write_report(root, paths["reports_dir"], "data_quality", verdict, table)
    print(f"data_quality: status={verdict['status']} checks={len(checks)} -> {written['json']}")
    for check in checks:
        print(f"  [{check['status']:>4}] {check['name']}: {check['detail']}")
    return exit_code(verdict["status"] == "fail")


if __name__ == "__main__":
    raise SystemExit(main())
