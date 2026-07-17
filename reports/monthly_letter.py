"""CHF institutional monthly letter generator (deterministic).

Reads only artifacts that other layers already produced — paper-trading books
(data/papertrade/), the independent accounting NAV series (data/accounting/),
the regime/sleeve strategy layer (data/strategies/), the monitoring suite
(data/reports/monitoring/) and the orchestration approval queue
(memory/approvals.json) — and renders a Markdown monthly letter plus a baked
JSON payload under artifacts/letters/.

Everything is virtual paper trading. The letter never claims verified alpha:
the canonical research verdict remains ``alpha_verified=false`` and the footer
disclaimers are always emitted.

The output is deterministic: the same input artifacts always produce the same
letter (no wall-clock timestamps in the rendered body).

Usage:
    python3 -m reports.monthly_letter [--month YYYY-MM] [--root PATH]
    python3 main.py letter
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ANNUALIZATION_DAYS = 365.0
BTC_BETA_WINDOW = 60

MONITORING_MODULES = [
    "data_quality",
    "signal_health",
    "execution_quality",
    "model_decay",
    "risk_report",
    "shadow_nav",
    "watchdog",
    "champion_challenger",
]

DISCLAIMERS = [
    "All books shown are **virtual paper trading** with simulated cash. Nothing here is live trading or real holdings.",
    "The canonical research verdict for this system remains `alpha_verified = false`. No result in this letter is a claim of verified alpha.",
    "Net-of-fees figures apply a simulated institutional fee schedule (management + performance fees) to virtual NAVs for realism only.",
    "This letter is for research and education. It is **not financial advice** and not an offer or solicitation of any investment.",
]


# ------------------------------------------------------------------ io ------
def _read_parquet(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a corrupt artifact must not kill the letter
        return None


def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        obj = float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    return obj


# ------------------------------------------------------------- formatting ---
def _pct(x: Optional[float], digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x * 100:+.{digits}f}%"


def _upct(x: Optional[float], digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x * 100:.{digits}f}%"


def _num(x: Optional[float], digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:.{digits}f}"


def _usd(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"${x:,.2f}"


# ------------------------------------------------------------- statistics ---
def _period_return(nav: pd.Series, dates: pd.Series, start: pd.Timestamp,
                   end: pd.Timestamp) -> Optional[float]:
    """Return over (start, end]: last NAV at/before end vs base NAV.

    Base is the last NAV strictly before ``start`` when it exists (a true
    period return) or the first NAV inside the window (partial period since
    inception).
    """
    mask_end = dates <= end
    if not mask_end.any():
        return None
    last = float(nav[mask_end].iloc[-1])
    before = nav[dates < start]
    if len(before):
        base = float(before.iloc[-1])
    else:
        inside = nav[(dates >= start) & mask_end]
        if inside.empty:
            return None
        base = float(inside.iloc[0])
    if base == 0 or last == base:
        return 0.0 if base != 0 else None
    return last / base - 1.0


def _risk_metrics(nav: pd.Series, dates: pd.Series,
                  btc_returns: Optional[pd.Series]) -> Dict[str, Any]:
    """Sharpe/Sortino/Calmar (sqrt(365)), max DD, % positive days, BTC beta/corr."""
    out: Dict[str, Any] = {
        "n_days": int(len(nav)),
        "sharpe": None, "sortino": None, "calmar": None,
        "max_drawdown": None, "pct_positive_days": None,
        "btc_beta_60d": None, "btc_corr_60d": None,
    }
    nav = pd.Series(np.asarray(nav, dtype=float), index=pd.DatetimeIndex(dates))
    nav = nav[~nav.index.duplicated(keep="last")].sort_index()
    if len(nav) >= 2:
        peaks = nav.cummax()
        out["max_drawdown"] = float((nav / peaks - 1.0).min())
    rets = nav.pct_change().dropna()
    if rets.empty:
        return out
    out["pct_positive_days"] = float((rets > 0).mean())
    if len(rets) >= 2:
        std = float(rets.std(ddof=1))
        if std > 0:
            out["sharpe"] = float(rets.mean() / std * math.sqrt(ANNUALIZATION_DAYS))
        downside = rets[rets < 0]
        dstd = float(downside.std(ddof=1)) if len(downside) >= 2 else None
        if dstd and dstd > 0:
            out["sortino"] = float(rets.mean() / dstd * math.sqrt(ANNUALIZATION_DAYS))
        n = len(nav)
        ann_ret = (float(nav.iloc[-1]) / float(nav.iloc[0])) ** (ANNUALIZATION_DAYS / max(n - 1, 1)) - 1.0
        mdd = out["max_drawdown"]
        if mdd is not None and mdd < 0:
            out["calmar"] = float(ann_ret / abs(mdd))
    if btc_returns is not None and len(rets) >= 3:
        joined = pd.concat([rets.rename("book"), btc_returns.rename("btc")], axis=1).dropna()
        joined = joined.tail(BTC_BETA_WINDOW)
        if len(joined) >= 3:
            var_btc = float(joined["btc"].var(ddof=1))
            if var_btc > 0:
                out["btc_beta_60d"] = float(joined["book"].cov(joined["btc"]) / var_btc)
            corr = float(joined["book"].corr(joined["btc"]))
            if not math.isnan(corr):
                out["btc_corr_60d"] = corr
    return out


# ----------------------------------------------------------------- inputs ---
def _load_books(root: Path) -> List[Dict[str, Any]]:
    """One record per paper book, preferring the accounting NAV (gross+net)."""
    pt_dir = root / "data" / "papertrade"
    manifest = _read_json(pt_dir / "papertrade_manifest.json") or {}
    manifest_books = manifest.get("books") or {}
    names = sorted(
        {p.name for p in pt_dir.iterdir() if p.is_dir() and (p / "state.json").exists()}
        | set(manifest_books)
    ) if pt_dir.exists() else sorted(manifest_books)

    books = []
    for name in names:
        acc = _read_parquet(root / "data" / "accounting" / f"nav_{name}.parquet")
        eq = _read_parquet(pt_dir / name / "equity.parquet")
        rec: Dict[str, Any] = {
            "name": name,
            "nav_source": None,
            "dates": None,          # pd.Series of Timestamps (internal)
            "gross": None,          # pd.Series (internal)
            "net": None,            # pd.Series or None (internal)
            "status": (manifest_books.get(name) or {}).get("status"),
        }
        if acc is not None and not acc.empty and {"date", "nav"}.issubset(acc.columns):
            acc = acc.copy()
            acc["date"] = pd.to_datetime(acc["date"])
            acc = acc.sort_values("date")
            rec["nav_source"] = "accounting"
            rec["dates"] = acc["date"].reset_index(drop=True)
            rec["gross"] = acc["nav"].astype(float).reset_index(drop=True)
            if "net_nav" in acc.columns:
                rec["net"] = acc["net_nav"].astype(float).reset_index(drop=True)
        elif eq is not None and not eq.empty and {"date", "nav"}.issubset(eq.columns):
            eq = eq.copy()
            eq["date"] = pd.to_datetime(eq["date"])
            eq = eq.sort_values("date")
            rec["nav_source"] = "papertrade"
            rec["dates"] = eq["date"].reset_index(drop=True)
            rec["gross"] = eq["nav"].astype(float).reset_index(drop=True)
        else:
            rec["nav_source"] = "none"
            rec["manifest_nav"] = (manifest_books.get(name) or {}).get("nav")
        books.append(rec)
    return books


def _load_market(root: Path):
    """Return (btc close series, equal-weight index series) indexed by date."""
    mk = _read_parquet(root / "data" / "raw" / "market" / "market_ohlcv.parquet")
    if mk is None or mk.empty or not {"symbol", "date_ts", "close"}.issubset(mk.columns):
        return None, None
    mk = mk.copy()
    mk["date_ts"] = pd.to_datetime(mk["date_ts"], utc=True).dt.tz_localize(None)
    btc = (mk[mk["symbol"] == "BTC"].sort_values("date_ts")
             .drop_duplicates("date_ts", keep="last").set_index("date_ts")["close"].astype(float))
    if "is_universe_member" in mk.columns and bool(mk["is_universe_member"].any()):
        panel = mk[mk["is_universe_member"].fillna(False).astype(bool)]
    else:
        panel = mk
    closes = (panel.sort_values("date_ts")
                    .drop_duplicates(["symbol", "date_ts"], keep="last")
                    .pivot(index="date_ts", columns="symbol", values="close").astype(float))
    ew_ret = closes.pct_change(fill_method=None).mean(axis=1, skipna=True)
    ew_index = (1.0 + ew_ret.fillna(0.0)).cumprod() * 100.0
    return (btc if not btc.empty else None), (ew_index if not ew_index.empty else None)


def _bench_row(name: str, series: Optional[pd.Series], month_start: pd.Timestamp,
               month_end: pd.Timestamp, year_start: pd.Timestamp,
               inception: Optional[pd.Timestamp]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"name": name, "month_return": None, "ytd_return": None,
                           "since_fund_inception": None, "data_through": None}
    if series is None or series.empty:
        return row
    s = series.dropna()
    dates = pd.Series(s.index)
    row["data_through"] = s.index.max().strftime("%Y-%m-%d")
    row["month_return"] = _period_return(pd.Series(s.values), dates, month_start, month_end)
    row["ytd_return"] = _period_return(pd.Series(s.values), dates, year_start, month_end)
    if inception is not None:
        row["since_fund_inception"] = _period_return(pd.Series(s.values), dates, inception, month_end)
    return row


def _regime_section(root: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"current_regime": None, "days_in_regime": None,
                           "as_of": None, "detail": None}
    latest = _read_json(root / "data" / "strategies" / "regime_latest.json") or {}
    daily = _read_parquet(root / "data" / "strategies" / "regime_daily.parquet")
    if daily is not None and not daily.empty and "regime" in daily.columns:
        daily = daily.sort_values("date_ts")
        regimes = daily["regime"].astype(str).tolist()
        current = regimes[-1]
        days = 0
        for r in reversed(regimes):
            if r != current:
                break
            days += 1
        out["current_regime"] = current
        out["days_in_regime"] = int(days)
        out["as_of"] = pd.to_datetime(daily["date_ts"].iloc[-1]).strftime("%Y-%m-%d")
    elif latest:
        out["current_regime"] = latest.get("regime")
        out["as_of"] = latest.get("date")
    if latest:
        out["detail"] = {
            "btc_trend_up": latest.get("btc_trend_up"),
            "breadth_pct_above_50d_ma": latest.get("breadth_pct_above_50d_ma"),
            "avg_pairwise_corr_60d_top20": latest.get("avg_pairwise_corr_60d_top20"),
            "btc_dominance_trend": latest.get("btc_dominance_trend"),
            "fear_greed_value": latest.get("fear_greed_value"),
            "fear_greed_bucket": latest.get("fear_greed_bucket"),
        }
    return out


def _sleeve_section(root: Path) -> Dict[str, Any]:
    prop = _read_json(root / "data" / "strategies" / "sleeve_allocation_proposal.json") or {}
    rows = []
    for sleeve, info in sorted((prop.get("sleeves") or {}).items()):
        rows.append({
            "sleeve": sleeve,
            "base_weight": info.get("base_weight"),
            "proposed_weight": info.get("proposed_weight"),
            "sortino_90d": info.get("sortino_90d"),
            "return_source": info.get("return_source"),
        })
    return {
        "as_of": prop.get("as_of"),
        "regime": prop.get("regime"),
        "cash_weight": prop.get("cash_weight"),
        "artifact_type": prop.get("artifact_type"),
        "approved": bool(prop.get("approved", False)),
        "note": prop.get("note"),
        "rows": rows,
    }


def _monitoring_section(root: Path) -> Dict[str, Any]:
    mon_dir = root / "data" / "reports" / "monitoring"
    modules, open_alerts = [], []
    for name in MONITORING_MODULES:
        rep = _read_json(mon_dir / f"{name}.json")
        if rep is None:
            continue
        status = str(rep.get("status", "unknown"))
        alerts = [str(a) for a in (rep.get("alerts") or [])]
        modules.append({"module": name, "status": status, "n_alerts": len(alerts)})
        if status == "alert" or alerts:
            for msg in alerts or [f"status={status}"]:
                open_alerts.append({"module": name, "message": msg})
    verdict = _read_json(root / "data" / "readiness" / "daily_quality_verdict.json") or {}
    return {"modules": modules, "open_alerts": open_alerts,
            "daily_quality_verdict": verdict.get("status")}


def _approvals_section(root: Path) -> List[Dict[str, Any]]:
    payload = _read_json(root / "memory" / "approvals.json") or {}
    return [
        {"step_id": a.get("step_id"), "stage": a.get("stage"),
         "goal": a.get("goal"), "reason": a.get("reason"),
         "created_utc": a.get("created_utc")}
        for a in (payload.get("approvals") or [])
        if a.get("status") == "pending"
    ]


# ------------------------------------------------------------------- build --
def build_letter(root: Path, month: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the full letter payload (JSON-safe) for the given month."""
    books = _load_books(root)
    all_dates = [b["dates"] for b in books if b["dates"] is not None]
    latest_date = max((d.iloc[-1] for d in all_dates), default=None)
    inception = min((d.iloc[0] for d in all_dates), default=None)

    if month is None:
        if latest_date is None:
            raise SystemExit("monthly_letter: no paper book NAV history found "
                             "(run `python3 main.py papertrade` first).")
        month = latest_date.strftime("%Y-%m")
    month_start = pd.Timestamp(f"{month}-01")
    month_end = month_start + pd.offsets.MonthEnd(0)
    year_start = pd.Timestamp(f"{month_start.year}-01-01")

    history_days = 0
    if inception is not None and latest_date is not None:
        history_days = int((latest_date - inception).days) + 1
    is_inception_letter = history_days < 28

    btc_close, ew_index = _load_market(root)
    btc_returns = btc_close.pct_change().dropna() if btc_close is not None else None

    # (a) fund summary + (c) risk metrics per book
    fund_rows, risk_rows = [], []
    for b in books:
        row: Dict[str, Any] = {
            "book": b["name"], "nav_source": b["nav_source"],
            "nav_gross": None, "nav_net": None, "as_of": None,
            "month_return": None, "ytd_return": None, "since_inception": None,
        }
        if b["dates"] is not None:
            dates, gross = b["dates"], b["gross"]
            row["as_of"] = dates.iloc[-1].strftime("%Y-%m-%d")
            row["nav_gross"] = float(gross.iloc[-1])
            if b["net"] is not None:
                row["nav_net"] = float(b["net"].iloc[-1])
            row["month_return"] = _period_return(gross, dates, month_start, month_end)
            row["ytd_return"] = _period_return(gross, dates, year_start, month_end)
            first = float(gross.iloc[0])
            row["since_inception"] = (float(gross.iloc[-1]) / first - 1.0) if first else None
            metrics = _risk_metrics(gross, dates, btc_returns)
            metrics["book"] = b["name"]
            risk_rows.append(metrics)
        else:
            row["nav_gross"] = b.get("manifest_nav")
        fund_rows.append(row)

    bench_rows = [
        _bench_row("BTC (buy & hold)", btc_close, month_start, month_end, year_start, inception),
        _bench_row("Equal-weight universe", ew_index, month_start, month_end, year_start, inception),
    ]

    payload = {
        "letter_month": month,
        "is_inception_letter": is_inception_letter,
        "history_days": history_days,
        "fund_inception": inception.strftime("%Y-%m-%d") if inception is not None else None,
        "as_of": latest_date.strftime("%Y-%m-%d") if latest_date is not None else None,
        "alpha_verified": False,
        "fund_summary": fund_rows,
        "benchmarks": bench_rows,
        "risk_metrics": risk_rows,
        "regime": _regime_section(root),
        "sleeve_allocation": _sleeve_section(root),
        "monitoring": _monitoring_section(root),
        "pending_approvals": _approvals_section(root),
        "disclaimers": DISCLAIMERS,
    }
    return _clean(payload)


# ------------------------------------------------------------------ render --
def render_markdown(p: Dict[str, Any]) -> str:
    month = p["letter_month"]
    title = (f"CHF Virtual Fund — Inception Letter ({month})"
             if p["is_inception_letter"] else f"CHF Virtual Fund — Monthly Letter ({month})")
    lines: List[str] = [f"# {title}", ""]
    lines.append(f"**Period:** {month} · **Data as of:** {p.get('as_of') or '—'} · "
                 f"**Fund inception:** {p.get('fund_inception') or '—'} · "
                 f"**alpha_verified:** `false`")
    lines.append("")
    lines.append("> All figures below describe **virtual paper-trading books** "
                 "(simulated cash, simulated fees). This is research validation, "
                 "not a live fund and not financial advice.")
    lines.append("")
    if p["is_inception_letter"]:
        lines.append(f"*This is an inception letter: the books have only "
                     f"{p['history_days']} day(s) of history (< 1 month). Period "
                     f"statistics are partial and will stabilize as history accrues.*")
        lines.append("")

    # (a) fund summary
    lines.append("## 1. Fund summary (all paper books)")
    lines.append("")
    lines.append("| Book | NAV (gross) | NAV (net of fees) | Month | YTD | Since inception | NAV source |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for r in p["fund_summary"]:
        lines.append(
            f"| {r['book']} | {_usd(r['nav_gross'])} | {_usd(r['nav_net'])} | "
            f"{_pct(r['month_return'])} | {_pct(r['ytd_return'])} | "
            f"{_pct(r['since_inception'])} | {r['nav_source'] or '—'} |"
        )
    lines.append("")
    lines.append("Net-of-fees NAV comes from the independent accounting ledger "
                 "(`data/accounting/nav_<book>.parquet`, simulated 2%/20% fee schedule) "
                 "where available; other books show the paper-trade engine NAV (gross).")
    lines.append("")

    # (b) benchmarks
    lines.append("## 2. Benchmark comparison")
    lines.append("")
    lines.append("| Benchmark | Month | YTD | Since fund inception | Data through |")
    lines.append("|---|---:|---:|---:|---|")
    for r in p["benchmarks"]:
        lines.append(f"| {r['name']} | {_pct(r['month_return'])} | {_pct(r['ytd_return'])} | "
                     f"{_pct(r['since_fund_inception'])} | {r['data_through'] or '—'} |")
    lines.append("")
    lines.append("Benchmarks are computed directly from `data/raw/market/market_ohlcv.parquet` "
                 "(BTC close; equal-weight = mean daily return across universe members).")
    lines.append("")

    # (c) risk metrics
    lines.append("## 3. Risk metrics per book (annualized, √365)")
    lines.append("")
    lines.append("| Book | Sharpe | Sortino | Calmar | Max DD | % positive days | BTC β (60d) | BTC ρ (60d) | Days |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in p["risk_metrics"]:
        lines.append(
            f"| {r['book']} | {_num(r['sharpe'])} | {_num(r['sortino'])} | {_num(r['calmar'])} | "
            f"{_upct(r['max_drawdown'])} | {_upct(r['pct_positive_days'], 0)} | "
            f"{_num(r['btc_beta_60d'])} | {_num(r['btc_corr_60d'])} | {r['n_days']} |"
        )
    lines.append("")
    lines.append("Metrics requiring more history than the books currently have are shown as —.")
    lines.append("")

    # (d) regime
    reg = p["regime"]
    lines.append("## 4. Market regime")
    lines.append("")
    if reg.get("current_regime"):
        lines.append(f"- **Current regime:** `{reg['current_regime']}` "
                     f"(as of {reg.get('as_of') or '—'})")
        if reg.get("days_in_regime") is not None:
            lines.append(f"- **Days in regime:** {reg['days_in_regime']}")
        det = reg.get("detail") or {}
        if det:
            breadth = det.get("breadth_pct_above_50d_ma")
            corr = det.get("avg_pairwise_corr_60d_top20")
            lines.append(
                f"- BTC trend up: `{det.get('btc_trend_up')}` · breadth above 50d MA: "
                f"{_upct(breadth, 1)} · avg pairwise corr (60d, top 20): "
                f"{_num(corr)} · BTC dominance: {det.get('btc_dominance_trend') or '—'} · "
                f"fear & greed: {det.get('fear_greed_value') if det.get('fear_greed_value') is not None else '—'} "
                f"({det.get('fear_greed_bucket') or '—'})"
            )
    else:
        lines.append("- Regime data not available (run the strategies regime job).")
    lines.append("")

    # (e) sleeves
    sl = p["sleeve_allocation"]
    lines.append("## 5. Sleeve allocation (proposal)")
    lines.append("")
    if sl.get("rows"):
        state = "APPROVED" if sl.get("approved") else "PROPOSAL — not approved / not auto-executed"
        lines.append(f"- **Status:** {state} · as of {sl.get('as_of') or '—'} · "
                     f"regime `{sl.get('regime') or '—'}` · proposed cash weight "
                     f"{_upct(sl.get('cash_weight'), 1)}")
        lines.append("")
        lines.append("| Sleeve | Base weight | Proposed weight | Sortino (90d) | Return source |")
        lines.append("|---|---:|---:|---:|---|")
        for r in sl["rows"]:
            lines.append(f"| {r['sleeve']} | {_upct(r['base_weight'], 1)} | "
                         f"{_upct(r['proposed_weight'], 1)} | {_num(r['sortino_90d'])} | "
                         f"{r['return_source'] or '—'} |")
        lines.append("")
        if sl.get("note"):
            lines.append(f"_{sl['note']}_")
    else:
        lines.append("- No sleeve allocation proposal artifact found.")
    lines.append("")

    # (f) alerts + approvals
    mon = p["monitoring"]
    lines.append("## 6. Open alerts & pending approvals")
    lines.append("")
    lines.append(f"- Daily data-quality verdict: `{mon.get('daily_quality_verdict') or 'unknown'}`")
    if mon.get("open_alerts"):
        lines.append(f"- **Open monitoring alerts ({len(mon['open_alerts'])}):**")
        for a in mon["open_alerts"]:
            lines.append(f"  - `{a['module']}` — {a['message']}")
    else:
        lines.append("- No open monitoring alerts.")
    approvals = p["pending_approvals"]
    if approvals:
        lines.append(f"- **Pending approvals ({len(approvals)})** (humans own promotion; "
                     f"nothing auto-executes):")
        for a in approvals:
            lines.append(f"  - `{a['step_id']}` (goal `{a['goal']}`) — {a['reason']}")
    else:
        lines.append("- No pending approvals.")
    lines.append("")

    # (g) disclaimers
    lines.append("---")
    lines.append("")
    lines.append("### Disclaimers")
    lines.append("")
    for d in p["disclaimers"]:
        lines.append(f"- {d}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------- cli --
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CHF institutional monthly letter (virtual books)")
    parser.add_argument("--month", default=None, help="Letter month YYYY-MM (default: latest book month)")
    parser.add_argument("--root", default=None, help="Project root (default: repo root)")
    parser.add_argument("--out-dir", default=None, help="Output dir (default: artifacts/letters)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else ROOT
    payload = build_letter(root, month=args.month)
    md = render_markdown(payload)

    out_dir = Path(args.out_dir) if args.out_dir else root / "artifacts" / "letters"
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{payload['letter_month']}_letter"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[letter] wrote {md_path}")
    print(f"[letter] wrote {json_path}")
    print(f"[letter] month={payload['letter_month']} inception_letter={payload['is_inception_letter']} "
          f"books={len(payload['fund_summary'])} open_alerts={len(payload['monitoring']['open_alerts'])} "
          f"pending_approvals={len(payload['pending_approvals'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
