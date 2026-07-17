# CHF Agentic Architecture

This document describes the agentic orchestration layer built from
`docs/agentic_ai_suggestions.md`. It implements the recommended hybrid:
a **deterministic quant execution core** (the existing pipeline agents)
wrapped by an **agentic control plane** that plans, gates, executes, and
reports — with zero LLM calls in the control path today.

## Planes

```
+---------------------------------------------------------------+
| Control plane: orchestration/                                 |
|                                                               |
|  supervisor.py ── run_goal(goal) loop                         |
|     │                                                         |
|     ├─ planner_agent.py   goal -> ordered PlanSteps           |
|     │    (rule-based; LLMPlanner stub behind same interface)  |
|     ├─ risk_agent.py      pre-flight checks + approval gates  |
|     ├─ execution_agent.py subprocess runner + verifiers       |
|     ├─ reporting_agent.py memory/last_report.md + summary     |
|     ├─ tool_registry.py   stages wrapped as ToolSpecs         |
|     └─ state_store.py     memory/*.json (atomic, versioned)   |
+---------------------------------------------------------------+
| Execution plane (unchanged, deterministic):                   |
|  main.py <stage> --config configs/run_config.yaml             |
|  UniverseAgent, MarketDataAgent, OnChainAgent, FeatureAgent,  |
|  LabelAgent, ModelAgent, PortfolioAgent, BacktestAgent,       |
|  AlphaResearchAgent, papertrade engine                        |
+---------------------------------------------------------------+
```

The control loop per goal is exactly the agentic shape the suggestions doc
asks for: *goal → inspect state → choose next tool → (gate) → run tool →
observe result → update memory → continue or stop*.

## Modules

| Module | Role |
|--------|------|
| `orchestration/contracts.py` | Dataclasses: `Task`, `PlanStep`, `ToolSpec`, `RunRecord`, `Approval` (pending/approved/rejected), `RiskVerdict`. |
| `orchestration/state_store.py` | JSON files under `memory/`: `system_state.json` (last stage runs + artifact freshness), `task_log.json` (append-only), `approvals.json`. Atomic writes (tmp + `os.replace`), `schema_version` on every document. |
| `orchestration/tool_registry.py` | Wraps the 10 CLI stages as tools. Each `ToolSpec` declares `reads_paths`, `writes_paths`, `result_affecting`, `requires_approval`, and its `scripts/verify_*_run.py` verifier when present. Computes artifact freshness from mtimes. |
| `orchestration/planner_agent.py` | `RuleBasedPlanner`: fixed goal vocabulary → ordered `PlanStep`s honoring the stage DAG; skips stages whose outputs are newer than their inputs (`--force` overrides). `LLMPlanner` is an unimplemented stub. |
| `orchestration/risk_agent.py` | Per-step pre-flight: input artifacts exist, >5 GB disk free, no foreign `memory/orchestrator.lock`, and an **approved** human `Approval` for gated steps. Never auto-approves. |
| `orchestration/execution_agent.py` | Sequential subprocess execution, stdout/stderr tails + exit codes into `RunRecord`s, runs the stage verifier after success, stops the plan on first failure. |
| `orchestration/reporting_agent.py` | Writes `memory/last_report.md`: step table, artifact freshness table, paper-trading book NAVs from `data/papertrade/papertrade_manifest.json`. |
| `orchestration/supervisor.py` | Ties it together; owns the run lock; public API `run_goal / approve / reject / list_pending`. |

## Goals → plans

The planner accepts a fixed vocabulary (deterministic by design):

| Goal | Stages (canonical DAG order) |
|------|------------------------------|
| `refresh_data` | universe → market → onchain |
| `refresh_research` | features → labels → model → portfolio → backtest → alpha_research |
| `daily_ops` | market → onchain → papertrade |
| `full_rebuild` | universe → market → onchain → features → labels → model → portfolio → backtest → alpha_research → papertrade |

Freshness rules:

- A stage is **fresh** when all its declared outputs exist and the oldest
  output is at least as new as the newest input artifact.
- A stage whose upstream re-runs *within the same plan* is never considered
  fresh (staleness cascades down the DAG).
- `papertrade` is a daily cycle: it is fresh only when the manifest's
  `as_of` equals today's UTC date.
- `--force` re-runs everything regardless of freshness (approvals still apply).

## CLI

```bash
python3 main.py orchestrate --goal daily_ops --dry-run   # print plan, execute nothing
python3 main.py orchestrate --goal refresh_research      # blocked steps register pending approvals
python3 main.py orchestrate --list-pending               # show approval queue
python3 main.py orchestrate --approve refresh_research:model
python3 main.py orchestrate --goal refresh_research      # now executes the approved steps
python3 main.py orchestrate --goal full_rebuild --force  # ignore freshness (still gated)
```

## Approval flow

1. Planning/risk assessment encounters a gated step (see
   `docs/decision_policies.md`) with no open approval → a **pending**
   `Approval` is written to `memory/approvals.json`. Step IDs are
   deterministic (`<goal>:<stage>`), so approvals survive replanning.
2. A human reviews (`--list-pending`, plus the dry-run report) and approves
   with `--approve STEP_ID`. Rejection is available via
   `Supervisor.reject()`; a rejected step gets a fresh pending record on the
   next plan so it can be re-reviewed.
3. On the next `run_goal`, the risk agent sees the approved record and lets
   the step through. Nothing in the codebase can flip a record to
   `approved` except the explicit approve API — there is no auto-approval
   path.

## Memory layout

```
memory/
  system_state.json    # schema_version, last stage runs, artifact freshness
  task_log.json        # append-only: tasks + per-step RunRecords
  approvals.json       # approval gate records
  last_report.md       # latest orchestration report
  orchestrator.lock    # present only while a real (non-dry) run executes
```

## How a future LLM planner slots in

`planner_agent.Planner` is the interface: `plan(goal, force) -> List[PlanStep]`.
An LLM-backed implementation (`LLMPlanner`, currently a stub that raises
`NotImplementedError`) would:

- read the same `StateStore` snapshot (freshness, last runs, failures) and
  emit the same `PlanStep` contract — JSON-schema-constrained output maps
  1:1 onto the dataclass;
- be free to *propose* non-routine plans (e.g. "rank IC degraded, schedule
  retraining"), but every step it emits still passes through `RiskAgent`,
  so approval gates and pre-flight checks cannot be bypassed by a model;
- log its reasoning into `task_log.json` entries for traceability.

The same pattern applies to a future `research_agent.py` (LLM summaries of
SHAP/walk-forward outputs): it may generate narrative, never numbers — all
figures must come from artifacts, per the "keep the financial core
deterministic" rule.

## Testing

`tests/test_orchestration.py` runs fully offline: the tool registry is
stubbed to `/bin/echo` (or a monkeypatched failing shell command) against a
synthetic project tree, covering planner ordering, freshness skipping and
cascade, force override, approval gating end-to-end, fail-fast execution,
state-store atomicity/round-trip, lock exclusion, and that `--dry-run`
spawns no subprocesses.
