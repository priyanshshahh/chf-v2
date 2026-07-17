"""Shared dataclass contracts for the CHF agentic orchestration layer.

All contracts are plain dataclasses with dict round-trip helpers so that the
JSON-file-backed state store (memory/) can persist them without any external
serialization dependency. No LLM types live here — the orchestration layer is
deterministic and rule-based; LLM integration stays behind the planner
interface stub in ``orchestration.planner_agent``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


def utcnow_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Return a short unique identifier with a readable prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class ApprovalStatus(str, Enum):
    """Lifecycle of a human approval record. Never auto-approved."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ToolSpec:
    """Declaration of one pipeline stage wrapped as a callable tool.

    The command itself is built by ``tool_registry.ToolRegistry`` so tests can
    stub it; the spec declares the metadata the planner/risk agents need.
    """

    name: str                       # stage name, e.g. "market"
    description: str = ""
    depends_on: List[str] = field(default_factory=list)   # upstream stage names
    reads_paths: List[str] = field(default_factory=list)  # input artifacts (rel. to project root)
    writes_paths: List[str] = field(default_factory=list) # output artifacts (rel. to project root)
    result_affecting: bool = False  # changes research results (models/allocs/backtests/alpha)
    requires_approval: bool = False # human approval gate (result-affecting or canonical data)
    verifier: Optional[str] = None  # e.g. "scripts/verify_market_run.py" when it exists

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanStep:
    """One ordered step emitted by the planner for a goal."""

    step_id: str                    # deterministic: "<goal>:<stage>" (stable across replans)
    stage: str
    goal: str
    reason: str = ""
    requires_approval: bool = False
    result_affecting: bool = False
    skipped: bool = False           # freshness skip decided at plan time
    skip_reason: str = ""
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanStep":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Task:
    """A goal-level unit of work tracked in the append-only task log."""

    task_id: str
    goal: str
    created_utc: str = field(default_factory=utcnow_iso)
    status: str = "planned"         # planned | running | completed | failed | dry_run
    steps: List[str] = field(default_factory=list)  # ordered step_ids
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class RunRecord:
    """Observed outcome of executing (or skipping) one plan step."""

    step_id: str
    stage: str
    command: List[str] = field(default_factory=list)
    started_utc: str = ""
    finished_utc: str = ""
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    verifier_command: List[str] = field(default_factory=list)
    verifier_exit_code: Optional[int] = None
    verifier_tail: str = ""
    status: str = "pending"         # pending | success | failed | skipped | blocked | not_run

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RunRecord":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Approval:
    """A human approval gate record persisted in memory/approvals.json."""

    approval_id: str
    step_id: str
    stage: str
    goal: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_utc: str = field(default_factory=utcnow_iso)
    decided_utc: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, ApprovalStatus) else str(self.status)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Approval":
        kwargs = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        kwargs["status"] = ApprovalStatus(kwargs.get("status", "pending"))
        return cls(**kwargs)


@dataclass
class RiskVerdict:
    """Pre-execution risk assessment for a single plan step."""

    step_id: str
    ok: bool
    blocked_reasons: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    approval_status: Optional[str] = None  # pending/approved/rejected/None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
