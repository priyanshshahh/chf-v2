"""Risk agent: skeptical pre-execution reviewer for plan steps.

Deterministic checks only — no LLM. For every step it verifies:

1. required input artifacts exist,
2. free disk space exceeds a floor (default 5 GB),
3. no other orchestrator holds ``memory/orchestrator.lock``,
4. result-affecting / canonical-data steps carry an APPROVED human approval
   record. Approvals are never granted automatically: a missing approval is
   registered as PENDING and the step is blocked until a human approves it
   via ``main.py orchestrate --approve <step_id>``.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from orchestration.contracts import ApprovalStatus, PlanStep, RiskVerdict
from orchestration.state_store import StateStore
from orchestration.tool_registry import ToolRegistry

LOCK_FILENAME = "orchestrator.lock"
DEFAULT_MIN_FREE_GB = 5.0


def read_lock(memory_dir: Path) -> Optional[dict]:
    lock_path = Path(memory_dir) / LOCK_FILENAME
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"pid": None, "raw": True}


class RiskAgent:
    """Pre-execution gatekeeper. Returns a RiskVerdict per step."""

    def __init__(
        self,
        registry: ToolRegistry,
        state_store: StateStore,
        memory_dir: Path,
        min_free_gb: float = DEFAULT_MIN_FREE_GB,
    ) -> None:
        self.registry = registry
        self.state_store = state_store
        self.memory_dir = Path(memory_dir)
        self.min_free_gb = min_free_gb

    def assess(self, step: PlanStep) -> RiskVerdict:
        spec = self.registry.get(step.stage)
        blocked: list[str] = []
        checks: dict = {}

        # Skipped steps sail through — they will not execute anyway.
        if step.skipped:
            return RiskVerdict(
                step_id=step.step_id,
                ok=True,
                checks={"skipped": True},
                requires_approval=step.requires_approval,
            )

        # 1) Required input artifacts exist.
        missing = [
            rel
            for rel in spec.reads_paths
            if not (self.registry.project_root / rel).exists()
        ]
        checks["missing_inputs"] = missing
        if missing:
            blocked.append(f"missing input artifacts: {', '.join(missing)}")

        # 2) Disk space floor.
        free_gb = shutil.disk_usage(str(self.registry.project_root)).free / (1024 ** 3)
        checks["free_gb"] = round(free_gb, 2)
        if free_gb < self.min_free_gb:
            blocked.append(
                f"insufficient disk space: {free_gb:.1f} GB free "
                f"(< {self.min_free_gb:.0f} GB floor)"
            )

        # 3) No foreign orchestrator lock.
        lock = read_lock(self.memory_dir)
        checks["lock"] = lock
        if lock is not None and lock.get("pid") != os.getpid():
            blocked.append(
                f"another orchestrator holds {LOCK_FILENAME} (pid={lock.get('pid')})"
            )

        # 4) Human approval gate. NEVER auto-approve.
        approval_status: Optional[str] = None
        if step.requires_approval:
            approval = self.state_store.latest_approval_for_step(step.step_id)
            if approval is None:
                approval = self.state_store.ensure_pending_approval(
                    step_id=step.step_id,
                    stage=step.stage,
                    goal=step.goal,
                    reason=(
                        "result-affecting stage"
                        if step.result_affecting
                        else "rewrites canonical data"
                    ),
                )
            approval_status = approval.status.value
            if approval.status != ApprovalStatus.APPROVED:
                blocked.append(
                    f"human approval required (status={approval_status}); "
                    f"approve with: python3 main.py orchestrate --approve {step.step_id}"
                )

        return RiskVerdict(
            step_id=step.step_id,
            ok=not blocked,
            blocked_reasons=blocked,
            checks=checks,
            requires_approval=step.requires_approval,
            approval_status=approval_status,
        )
