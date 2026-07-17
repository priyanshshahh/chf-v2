"""Signal health monitor: rolling Spearman rank-IC per model.

Reads data/predictions/model_predictions.parquet (read-only; it already
carries both ``prediction`` and ``actual_forward_return``), computes a daily
cross-sectional Spearman rank-IC per (model_name, horizon_days, feature_set),
then 30/90-day rolling means, a 90-day trend slope and sign-flip detection
against the full backtest-period IC. Alerts (exit 1) when a model's mean 30d
IC drops below the configured floor.
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
    write_report,
)

COMBO_KEYS = ["model_name", "horizon_days", "feature_set"]


def daily_rank_ic(preds: pd.DataFrame, min_names: int = 5) -> pd.DataFrame:
    """Daily cross-sectional Spearman IC per model combo.

    Returns a frame with COMBO_KEYS + ['date_ts', 'ic', 'n_names'].
    """
    df = preds.dropna(subset=["prediction", "actual_forward_return"]).copy()
    if df.empty:
        return pd.DataFrame(columns=COMBO_KEYS + ["date_ts", "ic", "n_names"])
    rows: List[Dict[str, Any]] = []
    for keys, grp in df.groupby(COMBO_KEYS + ["date_ts"], sort=True):
        if len(grp) < min_names:
            continue
        ic = grp["prediction"].corr(grp["actual_forward_return"], method="spearman")
        if pd.isna(ic):
            continue
        model, horizon, feats, date_ts = keys
        rows.append(
            {
                "model_name": model,
                "horizon_days": horizon,
                "feature_set": feats,
                "date_ts": date_ts,
                "ic": float(ic),
                "n_names": int(len(grp)),
            }
        )
    return pd.DataFrame(rows)


def summarize_combo(ic_series: pd.Series, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Rolling stats for one combo's daily IC series (indexed by date)."""
    ic_series = ic_series.sort_index()
    windows = [int(w) for w in cfg.get("rolling_windows", [30, 90])]
    out: Dict[str, Any] = {"n_days": int(len(ic_series))}
    full_ic = float(ic_series.mean()) if len(ic_series) else None
    out["ic_full_period"] = full_ic
    for w in windows:
        tail = ic_series.tail(w)
        out[f"ic_{w}d"] = float(tail.mean()) if len(tail) else None
        out[f"ic_{w}d_n"] = int(len(tail))
    # trend: OLS slope of daily IC over the last 90 observations (per day)
    tail90 = ic_series.tail(90)
    if len(tail90) >= 10:
        x = np.arange(len(tail90), dtype=float)
        out["ic_trend_slope_90d"] = float(np.polyfit(x, tail90.to_numpy(dtype=float), 1)[0])
    else:
        out["ic_trend_slope_90d"] = None
    # sign flip vs the backtest-period (full sample) IC
    ic30 = out.get("ic_30d")
    min_ref = float(cfg.get("sign_flip_min_reference_ic", 0.005))
    out["sign_flip"] = bool(
        ic30 is not None
        and full_ic is not None
        and abs(full_ic) >= min_ref
        and np.sign(ic30) != 0
        and np.sign(ic30) != np.sign(full_ic)
    )
    out["last_ic_date"] = ic_series.index.max() if len(ic_series) else None
    return out


def evaluate(preds: pd.DataFrame, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Full signal-health evaluation on a predictions panel."""
    min_names = int(cfg.get("min_names_per_day", 5))
    min_days = int(cfg.get("min_days", 20))
    floor = float(cfg.get("ic_floor_30d", 0.0))

    daily = daily_rank_ic(preds, min_names=min_names)
    combos: List[Dict[str, Any]] = []
    for keys, grp in daily.groupby(COMBO_KEYS, sort=True):
        series = grp.set_index("date_ts")["ic"]
        if len(series) < min_days:
            continue
        row = dict(zip(COMBO_KEYS, keys))
        row.update(summarize_combo(series, cfg))
        combos.append(row)

    alerts: List[str] = []
    models: Dict[str, Any] = {}
    combo_df = pd.DataFrame(combos)
    for model, grp in combo_df.groupby("model_name") if len(combo_df) else []:
        ic30 = grp["ic_30d"].dropna()
        mean_ic30 = float(ic30.mean()) if len(ic30) else None
        flips = grp[grp["sign_flip"]]
        models[model] = {
            "mean_ic_30d": mean_ic30,
            "mean_ic_90d": float(grp["ic_90d"].dropna().mean()) if "ic_90d" in grp else None,
            "mean_ic_full_period": float(grp["ic_full_period"].dropna().mean()),
            "n_combos": int(len(grp)),
            "sign_flips": int(len(flips)),
            "below_floor": bool(mean_ic30 is not None and mean_ic30 < floor),
        }
        if models[model]["below_floor"]:
            alerts.append(f"{model}: mean 30d rank-IC {mean_ic30:.4f} < floor {floor:.4f}")
        if len(flips):
            alerts.append(f"{model}: IC sign flip vs backtest-period IC in {len(flips)} combo(s)")

    status = "alert" if alerts else "ok"
    if not combos:
        status = "insufficient_data"
    return {
        "status": status,
        "ic_floor_30d": floor,
        "alerts": alerts,
        "models": models,
        "combos": combos,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF signal health (rolling rank-IC) monitor").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("signal_health", {})

    preds = read_parquet_safe(
        root / paths["predictions"],
        columns=COMBO_KEYS + ["date_ts", "symbol", "prediction", "actual_forward_return"],
    )
    if preds is None or preds.empty:
        summary = {"status": "missing_input", "alerts": [], "detail": paths["predictions"]}
        write_report(root, paths["reports_dir"], "signal_health", summary)
        print("signal_health: missing predictions input; nothing to score")
        return 0

    summary = evaluate(preds, cfg)
    table = pd.DataFrame(summary["combos"])
    written = write_report(root, paths["reports_dir"], "signal_health", summary, table)
    print(f"signal_health: status={summary['status']} models={len(summary['models'])} -> {written['json']}")
    for model, stats in summary["models"].items():
        ic30 = stats["mean_ic_30d"]
        print(f"  {model}: 30d IC={ic30 if ic30 is None else round(ic30, 4)} flips={stats['sign_flips']}")
    for alert in summary["alerts"]:
        print(f"  ALERT: {alert}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
