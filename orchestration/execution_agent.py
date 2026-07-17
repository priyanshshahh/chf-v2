"""Execution agent: runs approved plan steps sequentially via subprocess.

Captures stdout/stderr tails and exit codes into RunRecords, runs the stage
verifier (scripts/verify_*_run.py) after each successful stage, and stops the
plan on the first failure (stage or verifier). All records are appended to
the state store's task log and reflected in system_state.json.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional

from orchestration.contracts import PlanStep, RunRecord, utcnow_iso
from orchestration.state_store import StateStore
from orchestration.tool_registry import ToolRegistry

TAIL_CHARS = 2000
DEFAULT_STEP_TIMEOUT_S = 6 * 60 * 60  # generous ceiling per stage


def _tail(text: Optional[str], limit: int = TAIL_CHARS) -> str:
    if not text:
        return ""
    return text[-limit:]


class ExecutionAgent:
    """Sequential, fail-fast executor over plan steps."""

    def __init__(
        self,
        registry: ToolRegistry,
        state_store: StateStore,
        timeout_s: int = DEFAULT_STEP_TIMEOUT_S,
    ) -> None:
        self.registry = registry
        self.state_store = state_store
        self.timeout_s = timeout_s

    def execute_plan(self, steps: List[PlanStep]) -> List[RunRecord]:
        """Execute non-skipped steps in order; stop on first failure."""
        records: List[RunRecord] = []
        failed = False
        for step in steps:
            if step.skipped:
                record = RunRecord(
                    step_id=step.step_id,
                    stage=step.stage,
                    status="skipped",
                    stdout_tail=step.skip_reason,
                    started_utc=utcnow_iso(),
                    finished_utc=utcnow_iso(),
                )
            elif failed:
                record = RunRecord(
                    step_id=step.step_id,
                    stage=step.stage,
                    status="not_run",
                    stdout_tail="plan stopped: an earlier step failed",
                )
            else:
                record = self._run_step(step)
                if record.status == "failed":
                    failed = True
            records.append(record)
            self.state_store.append_run_record(record)
            if record.status in ("success", "failed"):
                self.state_store.record_stage_run(record)
        return records

    # ------------------------------------------------------------------
    def _run_step(self, step: PlanStep) -> RunRecord:
        command = self.registry.build_command(step.stage)
        record = RunRecord(
            step_id=step.step_id,
            stage=step.stage,
            command=command,
            started_utc=utcnow_iso(),
        )
        proc = self._run(command)
        record.exit_code = proc["exit_code"]
        record.stdout_tail = _tail(proc["stdout"])
        record.stderr_tail = _tail(proc["stderr"])
        if record.exit_code != 0:
            record.status = "failed"
            record.finished_utc = utcnow_iso()
            return record

        # Stage succeeded: run the verifier if one exists.
        verifier_cmd = self.registry.build_verifier_command(step.stage)
        if verifier_cmd:
            record.verifier_command = verifier_cmd
            vproc = self._run(verifier_cmd)
            record.verifier_exit_code = vproc["exit_code"]
            record.verifier_tail = _tail(
                (vproc["stdout"] or "") + (vproc["stderr"] or "")
            )
            if record.verifier_exit_code != 0:
                record.status = "failed"
                record.finished_utc = utcnow_iso()
                return record

        record.status = "success"
        record.finished_utc = utcnow_iso()
        return record

    def _run(self, command: List[str]) -> dict:
        """Run one subprocess and normalize the outcome."""
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.registry.project_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            return {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "exit_code": 124,
                "stdout": (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                "stderr": f"timeout after {self.timeout_s}s",
            }
        except FileNotFoundError as exc:
            return {"exit_code": 127, "stdout": "", "stderr": str(exc)}
