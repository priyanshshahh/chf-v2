"""Rule-based planner for the orchestration layer.

Given a goal from a fixed vocabulary, emit an ordered list of PlanSteps that
honors stage dependencies and artifact freshness. Deterministic and
rule-based by design — an LLM planner can later slot in behind the same
``Planner`` interface (see ``LLMPlanner`` stub) without touching the
supervisor, risk agent, or execution agent.
"""
from __future__ import annotations

from typing import Dict, List

from orchestration.contracts import PlanStep
from orchestration.state_store import StateStore
from orchestration.tool_registry import ToolRegistry

# Fixed goal vocabulary -> ordered stage lists (canonical DAG order).
GOAL_STAGES: Dict[str, List[str]] = {
    # Refresh raw inputs only (universe definition + market/on-chain data).
    "refresh_data": ["universe", "market", "onchain"],
    # Rebuild the research artifacts from existing raw data.
    "refresh_research": ["features", "labels", "model", "portfolio",
                         "backtest", "alpha_research"],
    # Low-touch daily operations: fresh prices + paper-trading cycle.
    "daily_ops": ["market", "onchain", "papertrade"],
    # Everything, end to end.
    "full_rebuild": ["universe", "market", "onchain", "features", "labels",
                     "model", "portfolio", "backtest", "alpha_research",
                     "papertrade"],
}


class Planner:
    """Planner interface. Implementations emit ordered PlanSteps for a goal."""

    def plan(self, goal: str, force: bool = False) -> List[PlanStep]:
        raise NotImplementedError


class RuleBasedPlanner(Planner):
    """Deterministic planner over the fixed goal vocabulary."""

    def __init__(self, registry: ToolRegistry, state_store: StateStore) -> None:
        self.registry = registry
        self.state_store = state_store

    def plan(self, goal: str, force: bool = False) -> List[PlanStep]:
        if goal not in GOAL_STAGES:
            raise ValueError(
                f"Unknown goal '{goal}'. Valid goals: {sorted(GOAL_STAGES)}"
            )
        # Snapshot freshness once per plan and persist it so the state store
        # reflects what the planner saw.
        freshness = self.registry.freshness_snapshot()
        self.state_store.record_artifact_freshness(freshness)

        steps: List[PlanStep] = []
        stale_in_plan: set = set()  # stages this plan will (re)run
        for stage in GOAL_STAGES[goal]:
            spec = self.registry.get(stage)
            info = freshness.get(stage, {})
            fresh = bool(info.get("fresh", False))
            # If an upstream stage in this same plan will re-run, this stage
            # cannot be considered fresh — its inputs are about to change.
            upstream_stale = any(dep in stale_in_plan for dep in spec.depends_on)

            skipped = False
            skip_reason = ""
            reason = f"goal '{goal}' requires stage '{stage}'"
            if not force and fresh and not upstream_stale:
                skipped = True
                skip_reason = "outputs newer than inputs (fresh); use --force to re-run"
            else:
                stale_in_plan.add(stage)
                if force:
                    reason += " (forced)"
                elif upstream_stale:
                    reason += " (upstream re-runs in this plan)"
                else:
                    reason += " (outputs stale or missing)"

            steps.append(
                PlanStep(
                    step_id=f"{goal}:{stage}",
                    stage=stage,
                    goal=goal,
                    reason=reason,
                    requires_approval=spec.requires_approval,
                    result_affecting=spec.result_affecting,
                    skipped=skipped,
                    skip_reason=skip_reason,
                    depends_on=list(spec.depends_on),
                )
            )
        return steps


class LLMPlanner(Planner):
    """Placeholder for a future LLM-backed planner.

    Intentionally unimplemented: the orchestration layer is deterministic.
    An LLM planner must return the same ``List[PlanStep]`` contract and will
    still pass through the risk agent's approval gates — it can propose, but
    never bypass governance.
    """

    def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - stub
        raise NotImplementedError(
            "LLM planning is not enabled. Use RuleBasedPlanner; see "
            "docs/agentic_architecture.md for the integration contract."
        )
