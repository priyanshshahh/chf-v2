"""CHF agentic orchestration layer (deterministic, rule-based control plane).

The quant pipeline stays the execution core; this package plans, gates,
executes, and reports on it. See docs/agentic_architecture.md.
"""
from orchestration.contracts import (  # noqa: F401
    Approval,
    ApprovalStatus,
    PlanStep,
    RiskVerdict,
    RunRecord,
    Task,
    ToolSpec,
)
from orchestration.supervisor import (  # noqa: F401
    Supervisor,
    approve,
    list_pending,
    run_goal,
)

__all__ = [
    "Approval",
    "ApprovalStatus",
    "PlanStep",
    "RiskVerdict",
    "RunRecord",
    "Task",
    "ToolSpec",
    "Supervisor",
    "approve",
    "list_pending",
    "run_goal",
]
