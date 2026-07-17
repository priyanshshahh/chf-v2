"""Supervisor: the orchestration control loop.

plan(goal) -> risk-check -> filter to approved / non-approval steps ->
execute -> report.

Deterministic and rule-based; no LLM calls. ``dry_run=True`` prints the plan
and approval requirements without executing anything (no subprocesses, no
lock acquisition).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from orchestration.contracts import (
    ApprovalStatus,
    Approval,
    PlanStep,
    RiskVerdict,
    RunRecord,
    Task,
    new_id,
    utcnow_iso,
)
from orchestration.execution_agent import ExecutionAgent
from orchestration.planner_agent import RuleBasedPlanner
from orchestration.reporting_agent import ReportingAgent
from orchestration.risk_agent import LOCK_FILENAME, RiskAgent, read_lock
from orchestration.state_store import StateStore
from orchestration.tool_registry import ToolRegistry

DEFAULT_CONFIG_PATH = "configs/run_config.yaml"


class OrchestratorLockError(RuntimeError):
    pass


class Supervisor:
    """Owns the plan/risk/execute/report loop for one project root."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        config_path: str = DEFAULT_CONFIG_PATH,
        memory_dir: Optional[Path] = None,
        registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parent.parent)
        self.memory_dir = Path(memory_dir) if memory_dir else self.project_root / "memory"
        self.registry = registry or ToolRegistry(self.project_root, config_path)
        self.state_store = StateStore(self.memory_dir)
        self.planner = RuleBasedPlanner(self.registry, self.state_store)
        self.risk_agent = RiskAgent(self.registry, self.state_store, self.memory_dir)
        self.execution_agent = ExecutionAgent(self.registry, self.state_store)
        self.reporting_agent = ReportingAgent(self.registry, self.memory_dir)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def run_goal(
        self,
        goal: str,
        force: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, object]:
        """Plan, risk-check, execute (unless dry_run) and report a goal."""
        steps = self.planner.plan(goal, force=force)
        verdicts: Dict[str, RiskVerdict] = {
            step.step_id: self.risk_agent.assess(step) for step in steps
        }

        task = Task(
            task_id=new_id("task"),
            goal=goal,
            status="dry_run" if dry_run else "running",
            steps=[s.step_id for s in steps],
            metadata={"force": force, "dry_run": dry_run},
        )
        self.state_store.append_task(task)

        records: List[RunRecord] = []
        if not dry_run:
            executable: List[PlanStep] = []
            for step in steps:
                if step.skipped:
                    executable.append(step)  # recorded as skipped by executor
                elif verdicts[step.step_id].ok:
                    executable.append(step)
                else:
                    blocked = RunRecord(
                        step_id=step.step_id,
                        stage=step.stage,
                        status="blocked",
                        stdout_tail="; ".join(verdicts[step.step_id].blocked_reasons),
                        started_utc=utcnow_iso(),
                        finished_utc=utcnow_iso(),
                    )
                    records.append(blocked)
                    self.state_store.append_run_record(blocked)
            with self._lock():
                records.extend(self.execution_agent.execute_plan(executable))
            statuses = {r.status for r in records}
            task.status = "failed" if "failed" in statuses else "completed"
            self.state_store.append_task(task)

        report = self.reporting_agent.build_report(
            goal=goal, steps=steps, verdicts=verdicts, records=records, dry_run=dry_run
        )
        report_path = self.reporting_agent.write_report(report)
        return {
            "goal": goal,
            "dry_run": dry_run,
            "steps": steps,
            "verdicts": verdicts,
            "records": records,
            "report": report,
            "report_path": report_path,
        }

    def approve(self, step_id: str, reason: str = "approved via CLI") -> Optional[Approval]:
        """Mark the latest approval record for a step as APPROVED."""
        return self.state_store.decide_approval(step_id, ApprovalStatus.APPROVED, reason)

    def reject(self, step_id: str, reason: str = "rejected via CLI") -> Optional[Approval]:
        """Mark the latest approval record for a step as REJECTED."""
        return self.state_store.decide_approval(step_id, ApprovalStatus.REJECTED, reason)

    def list_pending(self) -> List[Approval]:
        return self.state_store.pending_approvals()

    # ------------------------------------------------------------------
    # lock handling
    # ------------------------------------------------------------------
    def _lock(self) -> "_LockContext":
        return _LockContext(self.memory_dir)


class _LockContext:
    """memory/orchestrator.lock guard: one executing orchestrator at a time."""

    def __init__(self, memory_dir: Path) -> None:
        self.lock_path = Path(memory_dir) / LOCK_FILENAME

    def __enter__(self) -> "_LockContext":
        existing = read_lock(self.lock_path.parent)
        if existing is not None and existing.get("pid") != os.getpid():
            raise OrchestratorLockError(
                f"another orchestrator holds {self.lock_path} (pid={existing.get('pid')})"
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
            json.dumps({"pid": os.getpid(), "acquired_utc": utcnow_iso()})
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            existing = read_lock(self.lock_path.parent)
            if existing is not None and existing.get("pid") == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


# ----------------------------------------------------------------------
# module-level convenience API (used by main.py)
# ----------------------------------------------------------------------
def run_goal(
    goal: str,
    config_path: str = DEFAULT_CONFIG_PATH,
    force: bool = False,
    dry_run: bool = False,
    project_root: Optional[Path] = None,
    memory_dir: Optional[Path] = None,
) -> Dict[str, object]:
    sup = Supervisor(project_root=project_root, config_path=config_path,
                     memory_dir=memory_dir)
    return sup.run_goal(goal, force=force, dry_run=dry_run)


def approve(step_id: str, config_path: str = DEFAULT_CONFIG_PATH,
            project_root: Optional[Path] = None,
            memory_dir: Optional[Path] = None) -> Optional[Approval]:
    sup = Supervisor(project_root=project_root, config_path=config_path,
                     memory_dir=memory_dir)
    return sup.approve(step_id)


def list_pending(config_path: str = DEFAULT_CONFIG_PATH,
                 project_root: Optional[Path] = None,
                 memory_dir: Optional[Path] = None) -> List[Approval]:
    sup = Supervisor(project_root=project_root, config_path=config_path,
                     memory_dir=memory_dir)
    return sup.list_pending()
