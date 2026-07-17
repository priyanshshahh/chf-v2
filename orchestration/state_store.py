"""JSON-file-backed state store for the orchestration layer.

Files live under ``memory/`` (configurable for tests):

- ``system_state.json``  — last stage runs + artifact freshness snapshot
- ``task_log.json``      — append-only log of tasks and step run records
- ``approvals.json``     — human approval gate records

All writes are atomic (write to a temp file in the same directory, then
``os.replace``) and every document carries ``schema_version`` so future
migrations can detect old layouts.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestration.contracts import (
    SCHEMA_VERSION,
    Approval,
    ApprovalStatus,
    RunRecord,
    Task,
    new_id,
    utcnow_iso,
)

SYSTEM_STATE_FILE = "system_state.json"
TASK_LOG_FILE = "task_log.json"
APPROVALS_FILE = "approvals.json"


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically write ``payload`` as JSON to ``path`` (tmp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        # If os.replace succeeded the tmp file no longer exists.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with open(path, "r") as f:
        return json.load(f)


class StateStore:
    """Machine-readable memory for the orchestration layer."""

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.system_state_path = self.memory_dir / SYSTEM_STATE_FILE
        self.task_log_path = self.memory_dir / TASK_LOG_FILE
        self.approvals_path = self.memory_dir / APPROVALS_FILE

    # ------------------------------------------------------------------
    # system_state.json
    # ------------------------------------------------------------------
    def _default_system_state(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_utc": None,
            "stages": {},      # stage -> {last_run_utc, status, exit_code, step_id}
            "artifacts": {},   # stage -> freshness snapshot
        }

    def load_system_state(self) -> Dict[str, Any]:
        return _load_json(self.system_state_path, self._default_system_state())

    def save_system_state(self, state: Dict[str, Any]) -> None:
        state["schema_version"] = SCHEMA_VERSION
        state["updated_utc"] = utcnow_iso()
        atomic_write_json(self.system_state_path, state)

    def record_stage_run(self, record: RunRecord) -> None:
        """Persist the latest run outcome for a stage."""
        state = self.load_system_state()
        state.setdefault("stages", {})[record.stage] = {
            "last_run_utc": record.finished_utc or utcnow_iso(),
            "status": record.status,
            "exit_code": record.exit_code,
            "step_id": record.step_id,
            "verifier_exit_code": record.verifier_exit_code,
        }
        self.save_system_state(state)

    def record_artifact_freshness(self, freshness: Dict[str, Any]) -> None:
        state = self.load_system_state()
        state["artifacts"] = freshness
        self.save_system_state(state)

    # ------------------------------------------------------------------
    # task_log.json (append-only)
    # ------------------------------------------------------------------
    def _default_task_log(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "entries": []}

    def load_task_log(self) -> Dict[str, Any]:
        return _load_json(self.task_log_path, self._default_task_log())

    def append_task(self, task: Task) -> None:
        log = self.load_task_log()
        log.setdefault("entries", []).append({"type": "task", **task.to_dict()})
        atomic_write_json(self.task_log_path, log)

    def append_run_record(self, record: RunRecord) -> None:
        log = self.load_task_log()
        log.setdefault("entries", []).append({"type": "run", **record.to_dict()})
        atomic_write_json(self.task_log_path, log)

    def run_records(self) -> List[RunRecord]:
        log = self.load_task_log()
        return [
            RunRecord.from_dict(e)
            for e in log.get("entries", [])
            if e.get("type") == "run"
        ]

    # ------------------------------------------------------------------
    # approvals.json
    # ------------------------------------------------------------------
    def _default_approvals(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "approvals": []}

    def load_approvals(self) -> List[Approval]:
        doc = _load_json(self.approvals_path, self._default_approvals())
        return [Approval.from_dict(a) for a in doc.get("approvals", [])]

    def _save_approvals(self, approvals: List[Approval]) -> None:
        atomic_write_json(
            self.approvals_path,
            {
                "schema_version": SCHEMA_VERSION,
                "updated_utc": utcnow_iso(),
                "approvals": [a.to_dict() for a in approvals],
            },
        )

    def latest_approval_for_step(self, step_id: str) -> Optional[Approval]:
        matches = [a for a in self.load_approvals() if a.step_id == step_id]
        return matches[-1] if matches else None

    def ensure_pending_approval(self, step_id: str, stage: str, goal: str,
                                reason: str = "") -> Approval:
        """Register a pending approval for a gated step if none is open.

        Never auto-approves. If the latest record for the step is pending or
        approved it is returned unchanged; a rejected (or absent) record gets
        a fresh pending entry so the operator can re-review.
        """
        approvals = self.load_approvals()
        existing = [a for a in approvals if a.step_id == step_id]
        if existing and existing[-1].status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            return existing[-1]
        approval = Approval(
            approval_id=new_id("apr"),
            step_id=step_id,
            stage=stage,
            goal=goal,
            status=ApprovalStatus.PENDING,
            reason=reason,
        )
        approvals.append(approval)
        self._save_approvals(approvals)
        return approval

    def decide_approval(self, step_id: str, status: ApprovalStatus,
                        reason: str = "") -> Optional[Approval]:
        """Mark the latest approval record for ``step_id`` approved/rejected."""
        approvals = self.load_approvals()
        target = None
        for a in reversed(approvals):
            if a.step_id == step_id:
                target = a
                break
        if target is None:
            return None
        target.status = status
        target.decided_utc = utcnow_iso()
        if reason:
            target.reason = reason
        self._save_approvals(approvals)
        return target

    def pending_approvals(self) -> List[Approval]:
        return [a for a in self.load_approvals() if a.status == ApprovalStatus.PENDING]
