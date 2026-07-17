"""Offline tests for the agentic orchestration layer.

No real pipeline stages run: the tool registry is stubbed so every stage
command is ``/bin/echo`` (or a monkeypatched shell exit), and all artifacts
live in a synthetic tmp_path project tree.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orchestration.contracts import ApprovalStatus, PlanStep, RunRecord, Task
from orchestration.planner_agent import GOAL_STAGES, RuleBasedPlanner
from orchestration.risk_agent import RiskAgent
from orchestration.state_store import StateStore
from orchestration.supervisor import Supervisor
from orchestration.tool_registry import ToolRegistry

# DAG-ordered stage -> artifact files for the synthetic project.
STAGE_FILES = {
    "universe": ["data/raw/universe/universe_monthly.parquet"],
    "market": ["data/raw/market/BTC_ohlcv.parquet"],
    "onchain": ["data/raw/onchain/BTC_metrics.parquet"],
    "features": ["data/features/full_features.parquet"],
    "labels": ["data/labels/modeling_dataset.parquet",
               "data/labels/label_matrix.parquet"],
    "model": ["data/predictions/fold_metrics.parquet"],
    "portfolio": ["data/allocations/latest_allocation.parquet"],
    "backtest": ["data/backtests/backtest_summary.parquet"],
    "alpha_research": ["data/predictions/alpha_research_manifest.json"],
    "papertrade": ["data/papertrade/papertrade_manifest.json"],
}

YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
TODAY = datetime.now(timezone.utc).date().isoformat()


def make_project(tmp_path: Path, papertrade_as_of: str = YESTERDAY) -> Path:
    """Create a synthetic project tree with DAG-ascending artifact mtimes."""
    base = time.time() - 10_000
    for i, (stage, files) in enumerate(STAGE_FILES.items()):
        for rel in files:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if stage == "papertrade":
                p.write_text(json.dumps({
                    "as_of": papertrade_as_of,
                    "books": {"benchmark_btc": {"status": "ok", "nav": 100000.0,
                                                "n_fills": 0}},
                }))
            else:
                p.write_text(f"stub artifact for {stage}")
            mtime = base + i * 60
            os.utime(p, (mtime, mtime))
    return tmp_path


def make_supervisor(tmp_path: Path, **project_kwargs) -> Supervisor:
    root = make_project(tmp_path, **project_kwargs)
    registry = ToolRegistry(root, config_path="configs/run_config.yaml",
                            python_bin="/bin/echo")
    return Supervisor(project_root=root, memory_dir=root / "memory",
                      registry=registry)


def touch_now(root: Path, stage: str, offset: float = 0.0) -> None:
    """Bump a stage's artifacts to 'now' so downstream stages become stale."""
    now = time.time() + offset
    for rel in STAGE_FILES[stage]:
        os.utime(root / rel, (now, now))


# ----------------------------------------------------------------------
# planner
# ----------------------------------------------------------------------
def test_planner_full_rebuild_ordering(tmp_path):
    sup = make_supervisor(tmp_path)
    steps = sup.planner.plan("full_rebuild")
    assert [s.stage for s in steps] == GOAL_STAGES["full_rebuild"]
    # dependency honored: every dep appears earlier in the plan
    seen = set()
    for s in steps:
        assert set(s.depends_on) <= seen | set(), f"{s.stage} deps not before it"
        seen.add(s.stage)


def test_planner_unknown_goal_raises(tmp_path):
    sup = make_supervisor(tmp_path)
    with pytest.raises(ValueError, match="Unknown goal"):
        sup.planner.plan("conquer_the_market")


def test_planner_freshness_skipping_and_force(tmp_path):
    sup = make_supervisor(tmp_path)
    # All artifacts fresh (ascending mtimes) and papertrade as_of=yesterday:
    steps = sup.planner.plan("daily_ops")
    by_stage = {s.stage: s for s in steps}
    assert by_stage["market"].skipped
    assert by_stage["onchain"].skipped
    assert not by_stage["papertrade"].skipped  # daily cycle not run today

    # force overrides freshness
    forced = sup.planner.plan("daily_ops", force=True)
    assert all(not s.skipped for s in forced)


def test_planner_upstream_staleness_cascades(tmp_path):
    sup = make_supervisor(tmp_path)
    root = sup.registry.project_root
    touch_now(root, "universe")  # newest input -> market/onchain stale
    steps = sup.planner.plan("full_rebuild")
    by_stage = {s.stage: s for s in steps}
    # universe has no inputs: existing outputs count as fresh
    assert by_stage["universe"].skipped
    assert not by_stage["market"].skipped
    assert not by_stage["onchain"].skipped
    # cascade: features depends on market/onchain which re-run in this plan
    assert not by_stage["features"].skipped
    assert not by_stage["model"].skipped


def test_papertrade_fresh_when_ran_today(tmp_path):
    sup = make_supervisor(tmp_path, papertrade_as_of=TODAY)
    steps = sup.planner.plan("daily_ops")
    by_stage = {s.stage: s for s in steps}
    assert by_stage["papertrade"].skipped


# ----------------------------------------------------------------------
# tool registry
# ----------------------------------------------------------------------
def test_registry_commands_and_gates(tmp_path):
    root = make_project(tmp_path)
    reg = ToolRegistry(root, python_bin="/bin/echo")
    assert reg.build_command("market") == [
        "/bin/echo", "main.py", "market", "--config", "configs/run_config.yaml"
    ]
    for stage in ("model", "portfolio", "backtest", "alpha_research"):
        spec = reg.get(stage)
        assert spec.result_affecting and spec.requires_approval
    for stage in ("universe", "features", "labels"):
        spec = reg.get(stage)
        assert spec.requires_approval and not spec.result_affecting
    for stage in ("market", "onchain", "papertrade"):
        assert not reg.get(stage).requires_approval
    # no scripts/ dir in the synthetic project -> no verifiers
    assert all(reg.get(s).verifier is None for s in reg.stages())


# ----------------------------------------------------------------------
# risk agent
# ----------------------------------------------------------------------
def test_risk_agent_blocks_unapproved_result_affecting(tmp_path):
    sup = make_supervisor(tmp_path)
    step = PlanStep(step_id="refresh_research:model", stage="model",
                    goal="refresh_research", requires_approval=True,
                    result_affecting=True)
    verdict = sup.risk_agent.assess(step)
    assert not verdict.ok
    assert verdict.approval_status == "pending"
    assert any("approval" in r for r in verdict.blocked_reasons)
    # a pending approval record was registered, never auto-approved
    pending = sup.state_store.pending_approvals()
    assert [a.step_id for a in pending] == ["refresh_research:model"]

    # approval flips the verdict
    sup.approve("refresh_research:model")
    verdict2 = sup.risk_agent.assess(step)
    assert verdict2.ok and verdict2.approval_status == "approved"


def test_risk_agent_blocks_missing_inputs(tmp_path):
    sup = make_supervisor(tmp_path)
    root = sup.registry.project_root
    (root / "data/raw/universe/universe_monthly.parquet").unlink()
    step = PlanStep(step_id="daily_ops:market", stage="market", goal="daily_ops")
    verdict = sup.risk_agent.assess(step)
    assert not verdict.ok
    assert any("missing input artifacts" in r for r in verdict.blocked_reasons)


def test_risk_agent_blocks_foreign_lock(tmp_path):
    sup = make_supervisor(tmp_path)
    lock = sup.memory_dir / "orchestrator.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 999_999_999}))
    step = PlanStep(step_id="daily_ops:papertrade", stage="papertrade",
                    goal="daily_ops")
    verdict = sup.risk_agent.assess(step)
    assert not verdict.ok
    assert any("orchestrator.lock" in r for r in verdict.blocked_reasons)


# ----------------------------------------------------------------------
# approval flow end-to-end (echo-stubbed execution)
# ----------------------------------------------------------------------
def test_approval_flow_end_to_end(tmp_path):
    sup = make_supervisor(tmp_path)
    root = sup.registry.project_root
    touch_now(root, "market")
    touch_now(root, "onchain")  # features & downstream become stale

    # 1) first run: gated steps are blocked, pending approvals registered
    result = sup.run_goal("refresh_research")
    statuses = {r.step_id: r.status for r in result["records"]}
    assert statuses["refresh_research:features"] == "blocked"
    assert statuses["refresh_research:model"] == "blocked"
    pending_ids = {a.step_id for a in sup.list_pending()}
    assert pending_ids == {f"refresh_research:{s}" for s in
                           GOAL_STAGES["refresh_research"]}

    # 2) human approves every gated step
    for step_id in sorted(pending_ids):
        approved = sup.approve(step_id)
        assert approved is not None
        assert approved.status == ApprovalStatus.APPROVED
    assert sup.list_pending() == []

    # 3) second run executes all steps via /bin/echo
    result2 = sup.run_goal("refresh_research")
    statuses2 = {r.step_id: r.status for r in result2["records"]}
    assert all(v == "success" for v in statuses2.values()), statuses2
    state = sup.state_store.load_system_state()
    assert state["stages"]["model"]["status"] == "success"
    assert (sup.memory_dir / "last_report.md").exists()


# ----------------------------------------------------------------------
# execution agent
# ----------------------------------------------------------------------
def test_execution_stops_on_failure(tmp_path, monkeypatch):
    sup = make_supervisor(tmp_path)
    for stage in GOAL_STAGES["refresh_research"]:
        sup.state_store.ensure_pending_approval(f"refresh_research:{stage}",
                                                stage, "refresh_research")
        sup.approve(f"refresh_research:{stage}")

    real_build = sup.registry.build_command

    def fake_build(stage):
        if stage == "labels":
            return ["/bin/sh", "-c", "echo boom >&2; exit 3"]
        return real_build(stage)

    monkeypatch.setattr(sup.registry, "build_command", fake_build)
    result = sup.run_goal("refresh_research", force=True)
    statuses = {r.step_id: r.status for r in result["records"]}
    assert statuses["refresh_research:features"] == "success"
    assert statuses["refresh_research:labels"] == "failed"
    for stage in ("model", "portfolio", "backtest", "alpha_research"):
        assert statuses[f"refresh_research:{stage}"] == "not_run"
    failed = next(r for r in result["records"] if r.status == "failed")
    assert failed.exit_code == 3
    assert "boom" in failed.stderr_tail


# ----------------------------------------------------------------------
# state store
# ----------------------------------------------------------------------
def test_state_store_atomicity_and_roundtrip(tmp_path):
    store = StateStore(tmp_path / "memory")
    store.append_task(Task(task_id="t1", goal="daily_ops"))
    store.append_run_record(RunRecord(step_id="daily_ops:market", stage="market",
                                      status="success", exit_code=0))
    approval = store.ensure_pending_approval("daily_ops:model", "model", "daily_ops")
    store.decide_approval("daily_ops:model", ApprovalStatus.APPROVED, "ok")

    # roundtrip
    log = store.load_task_log()
    assert log["schema_version"] == 1
    kinds = [e["type"] for e in log["entries"]]
    assert kinds == ["task", "run"]
    records = store.run_records()
    assert records[0].step_id == "daily_ops:market" and records[0].exit_code == 0
    latest = store.latest_approval_for_step("daily_ops:model")
    assert latest.approval_id == approval.approval_id
    assert latest.status == ApprovalStatus.APPROVED
    assert latest.decided_utc is not None

    # atomicity: no temp files left behind, and every file is valid JSON
    leftovers = list((tmp_path / "memory").glob("*.tmp"))
    assert leftovers == []
    for name in ("system_state.json", "task_log.json", "approvals.json"):
        path = tmp_path / "memory" / name
        if path.exists():
            json.loads(path.read_text())


def test_state_store_never_auto_approves(tmp_path):
    store = StateStore(tmp_path / "memory")
    a1 = store.ensure_pending_approval("g:model", "model", "g")
    a2 = store.ensure_pending_approval("g:model", "model", "g")
    assert a1.approval_id == a2.approval_id  # idempotent, still pending
    assert a2.status == ApprovalStatus.PENDING
    # rejection opens a fresh pending record on next request
    store.decide_approval("g:model", ApprovalStatus.REJECTED)
    a3 = store.ensure_pending_approval("g:model", "model", "g")
    assert a3.approval_id != a1.approval_id
    assert a3.status == ApprovalStatus.PENDING


# ----------------------------------------------------------------------
# dry run
# ----------------------------------------------------------------------
def test_dry_run_executes_nothing(tmp_path, monkeypatch):
    sup = make_supervisor(tmp_path)

    def explode(*args, **kwargs):  # pragma: no cover - must never fire
        raise AssertionError("dry_run must not spawn subprocesses")

    import orchestration.execution_agent as ea
    monkeypatch.setattr(ea.subprocess, "run", explode)

    result = sup.run_goal("daily_ops", dry_run=True)
    assert result["dry_run"] is True
    assert result["records"] == []                      # nothing executed
    assert not (sup.memory_dir / "orchestrator.lock").exists()
    assert (sup.memory_dir / "last_report.md").exists()
    assert "DRY RUN" in result["report"]
    # plan is sane: daily_ops stages in order
    assert [s.stage for s in result["steps"]] == GOAL_STAGES["daily_ops"]
    # no run entries in the task log
    assert sup.state_store.run_records() == []
