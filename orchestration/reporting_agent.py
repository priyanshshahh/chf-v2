"""Reporting agent: writes memory/last_report.md and returns a printable
summary of an orchestration run (or dry run).

Deterministic templating only — a future LLM "research/reporting" agent could
enrich this narrative, but the numbers must keep coming from artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from orchestration.contracts import PlanStep, RiskVerdict, RunRecord
from orchestration.tool_registry import ToolRegistry

REPORT_FILENAME = "last_report.md"


def _fmt_ts(mtime: Optional[float]) -> str:
    if mtime is None:
        return "-"
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


class ReportingAgent:
    def __init__(self, registry: ToolRegistry, memory_dir: Path) -> None:
        self.registry = registry
        self.memory_dir = Path(memory_dir)

    # ------------------------------------------------------------------
    def build_report(
        self,
        goal: str,
        steps: List[PlanStep],
        verdicts: Dict[str, RiskVerdict],
        records: List[RunRecord],
        dry_run: bool = False,
    ) -> str:
        by_step: Dict[str, RunRecord] = {r.step_id: r for r in records}
        lines: List[str] = []
        mode = "DRY RUN (nothing executed)" if dry_run else "EXECUTION"
        lines.append(f"# CHF Orchestration Report — goal `{goal}`")
        lines.append("")
        lines.append(f"- Mode: {mode}")
        lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append("")

        # ---- step table -------------------------------------------------
        lines.append("## Plan steps")
        lines.append("")
        lines.append("| # | step_id | stage | approval | status | detail |")
        lines.append("|---|---------|-------|----------|--------|--------|")
        n_run = n_skip = n_fail = n_block = 0
        for i, step in enumerate(steps, start=1):
            verdict = verdicts.get(step.step_id)
            record = by_step.get(step.step_id)
            if step.requires_approval:
                approval = (verdict.approval_status if verdict and verdict.approval_status
                            else "required")
            else:
                approval = "not required"
            if step.skipped:
                status, detail = "skipped", step.skip_reason
                n_skip += 1
            elif verdict is not None and not verdict.ok:
                status, detail = "blocked", "; ".join(verdict.blocked_reasons)
                n_block += 1
            elif dry_run:
                status, detail = "would run", step.reason
            elif record is None:
                status, detail = "not_run", "plan stopped before this step"
            elif record.status == "success":
                status, detail = "success", f"exit={record.exit_code}"
                n_run += 1
            elif record.status == "failed":
                status = "failed"
                detail = (f"exit={record.exit_code} "
                          f"verifier_exit={record.verifier_exit_code}")
                n_fail += 1
            else:
                status, detail = record.status, record.stdout_tail[:80]
            detail = (detail or "").replace("\n", " ").replace("|", "\\|")
            lines.append(
                f"| {i} | `{step.step_id}` | {step.stage} | {approval} | {status} | {detail} |"
            )
        lines.append("")
        if not dry_run:
            lines.append(
                f"**Totals:** {n_run} run, {n_skip} skipped, {n_fail} failed, "
                f"{n_block} blocked."
            )
            lines.append("")

        # ---- freshness table --------------------------------------------
        lines.append("## Artifact freshness")
        lines.append("")
        lines.append("| stage | outputs exist | oldest output (UTC) | newest input (UTC) | fresh |")
        lines.append("|-------|---------------|---------------------|--------------------|-------|")
        for stage, info in self.registry.freshness_snapshot().items():
            lines.append(
                f"| {stage} | {'yes' if info['outputs_exist'] else 'no'} "
                f"| {_fmt_ts(info['oldest_output_mtime'])} "
                f"| {_fmt_ts(info['newest_input_mtime'])} "
                f"| {'yes' if info['fresh'] else 'no'} |"
            )
        lines.append("")

        # ---- papertrade NAVs ---------------------------------------------
        navs = self._papertrade_navs()
        if navs is not None:
            lines.append("## Paper-trading books")
            lines.append("")
            lines.append(f"- as_of: {navs.get('as_of', '-')}")
            lines.append("")
            lines.append("| book | status | NAV | fills |")
            lines.append("|------|--------|-----|-------|")
            for name, info in navs.get("books", {}).items():
                nav = info.get("nav")
                nav_s = f"{nav:,.2f}" if isinstance(nav, (int, float)) else "-"
                lines.append(
                    f"| {name} | {info.get('status', '-')} | {nav_s} "
                    f"| {info.get('n_fills', 0)} |"
                )
            lines.append("")

        return "\n".join(lines)

    def write_report(self, report: str) -> Path:
        from orchestration.state_store import atomic_write_json  # noqa: F401 (same dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        path = self.memory_dir / REPORT_FILENAME
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(report)
        tmp.replace(path)
        return path

    # ------------------------------------------------------------------
    def _papertrade_navs(self) -> Optional[dict]:
        manifest = (
            self.registry.project_root / "data" / "papertrade" / "papertrade_manifest.json"
        )
        if not manifest.exists():
            return None
        try:
            with open(manifest, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
