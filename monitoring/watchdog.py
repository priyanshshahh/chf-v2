"""End-of-cycle watchdog: artifact freshness per pipeline stage.

Reuses orchestration.tool_registry.ToolRegistry ``writes_paths`` and verifies
each stage's newest output mtime against the scheduler cadence (daily stages
refreshed today, weekly within 7 days, monthly within 31; manual stages —
backtest/alpha_research — are informational only). Writes the ops summary to
``data/reports/monitoring/watchdog.json``. Exit 1 when a scheduled stage is
stale or missing.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from monitoring.common import (
    build_arg_parser,
    exit_code,
    load_config,
    resolve_root,
    write_report,
)

_CADENCE_LIMIT_KEYS = {
    "daily": "daily_max_age_days",
    "weekly": "weekly_max_age_days",
    "monthly": "monthly_max_age_days",
}


def check_stage(
    stage: str,
    cadence: str,
    newest_output_mtime: Optional[float],
    outputs_exist: bool,
    cfg: Dict[str, Any],
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Freshness verdict for one stage given its newest writes_paths mtime."""
    now = now_utc or datetime.now(timezone.utc)
    row: Dict[str, Any] = {
        "stage": stage,
        "cadence": cadence,
        "outputs_exist": outputs_exist,
        "newest_output_mtime": newest_output_mtime,
        "age_days": None,
        "status": "ok",
    }
    if cadence == "manual":
        row["status"] = "manual"
        return row
    if not outputs_exist or newest_output_mtime is None:
        row["status"] = "missing"
        return row
    mtime_dt = datetime.fromtimestamp(newest_output_mtime, tz=timezone.utc)
    age_days = (now - mtime_dt).total_seconds() / 86400.0
    row["age_days"] = round(age_days, 3)
    row["newest_output_utc"] = mtime_dt.isoformat()
    if cadence == "daily":
        # "refreshed today": the newest output carries today's UTC date
        fresh = mtime_dt.date() == now.date() or age_days <= float(cfg.get("daily_max_age_days", 1))
    else:
        limit = float(cfg.get(_CADENCE_LIMIT_KEYS.get(cadence, "daily_max_age_days"), 1))
        fresh = age_days <= limit
    row["status"] = "ok" if fresh else "stale"
    return row


def run_watchdog(root, cfg: Dict[str, Any]) -> Dict[str, Any]:
    sys.path.insert(0, str(root))
    from orchestration.tool_registry import ToolRegistry, _newest_mtime

    registry = ToolRegistry(project_root=root)
    cadence_map: Dict[str, str] = cfg.get("cadence", {}) or {}
    rows: List[Dict[str, Any]] = []
    for stage in registry.stages():
        spec = registry.get(stage)
        mtimes = [m for m in (_newest_mtime(root / rel) for rel in spec.writes_paths) if m is not None]
        outputs_exist = len(mtimes) == len(spec.writes_paths) and bool(spec.writes_paths)
        newest = max(mtimes) if mtimes else None
        cadence = cadence_map.get(stage, "manual")
        row = check_stage(stage, cadence, newest, outputs_exist, cfg)
        row["writes_paths"] = list(spec.writes_paths)
        rows.append(row)

    stale = [r["stage"] for r in rows if r["status"] in ("stale", "missing")]
    return {
        "status": "alert" if stale else "ok",
        "stale_stages": stale,
        "alerts": [f"stage '{s}' output is stale or missing for its cadence" for s in stale],
        "stages": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF pipeline watchdog").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("watchdog", {})

    summary = run_watchdog(root, cfg)
    table = pd.DataFrame(
        [{k: v for k, v in row.items() if k != "writes_paths"} for row in summary["stages"]]
    )
    written = write_report(root, paths["reports_dir"], "watchdog", summary, table)
    print(f"watchdog: status={summary['status']} stale={summary['stale_stages']} -> {written['json']}")
    for row in summary["stages"]:
        print(f"  {row['stage']:>15} [{row['cadence']:>7}] status={row['status']} age_days={row['age_days']}")
    return exit_code(summary["status"] == "alert")


if __name__ == "__main__":
    raise SystemExit(main())
