# CHF Agentic AI Suggestions

## Goal

You said you want to build the system as an agentic AI system entirely, but the PDFs also matter. Those two goals are not perfectly aligned.

Important constraint from the PDFs:

- the build-plan PDF explicitly argues against putting conversational LLM agents inside the financial prediction core
- it prefers deterministic software agents for ingestion, modeling, and backtesting

Because of that, the best path is:

- keep the existing quant pipeline as the deterministic execution backbone
- add a real agentic AI control layer above it

This gives you an agentic system overall without violating the reproducibility and academic-integrity requirements of the project.

## Recommended Target Architecture

### Layer 1: Deterministic Quant Engine

Keep these modules as tools or execution primitives:

- `UniverseAgent`
- `MarketDataAgent`
- `OnChainAgent`
- `FeatureAgentV1` and `FeatureAgentV2`
- `LabelAgent`
- `ModelAgent`
- `PortfolioAgent`
- `BacktestAgent`
- `DuckDBEngine`

These should remain deterministic and testable.

### Layer 2: Agentic Orchestration Layer

Add an AI-driven control plane that can:

- inspect system state
- decide what to run next
- diagnose failures
- recommend experiments
- summarize results
- request human approval for high-impact actions

Suggested new modules:

- `orchestration/state_store.py`
- `orchestration/tool_registry.py`
- `orchestration/planner_agent.py`
- `orchestration/execution_agent.py`
- `orchestration/research_agent.py`
- `orchestration/risk_agent.py`
- `orchestration/reporting_agent.py`
- `orchestration/supervisor.py`
- `orchestration/contracts.py`

### Layer 3: Human Review and Governance

For a finance-oriented project, AI should not silently push decisions into production.

Add approval gates for:

- changing the universe definition
- retraining with new feature sets
- switching the production model
- changing portfolio construction rules
- publishing dashboard or report summaries

## What A Real Agentic Loop Would Look Like

Right now the repo mostly does:

`fixed DAG -> execute stage -> write artifacts`

The target design should do:

`goal -> inspect state -> choose next tool -> run tool -> evaluate result -> update memory -> continue or stop`

That means you need:

1. state
2. planning
3. tool calling
4. observation
5. memory update
6. critique or approval

Without that loop, the system is modular, but not truly agentic.

## Concrete Recommendations

### 1. Reframe Existing Pipeline Agents As Tools

Do not delete the existing agent classes.

Instead:

- keep their logic
- wrap them in a standard tool interface
- make them callable by an AI supervisor

Suggested contract:

- tool name
- required inputs
- expected outputs
- side effects
- validation checks
- rollback or retry policy

Example tools:

- `run_universe_snapshot`
- `run_market_ingestion`
- `run_onchain_ingestion`
- `build_feature_store`
- `generate_labels`
- `train_models`
- `build_allocations`
- `run_backtests`
- `generate_dashboard_summary`

### 2. Add A Structured State Store

The agentic layer needs a machine-readable memory system. The current artifact files are useful, but not enough.

Add a state store that tracks:

- latest successful stage outputs
- artifact locations
- snapshot IDs
- config hash
- model metrics by horizon and model
- feature set used
- known failures
- pending tasks
- last human approvals

This can start very simply as JSON or SQLite.

Suggested file:

- `orchestration/state_store.py`

Suggested sources of truth:

- `metadata/agent_registry.db`
- prediction metrics JSON files
- feature keep list JSON
- backtest summary files

### 3. Build A Planner Agent First

The planner is the heart of the agentic layer.

Its job:

- inspect current system state
- decide what task is most important
- emit a structured action plan

Examples:

- "market data is stale, run market ingestion"
- "new on-chain data arrived but labels are outdated, rebuild features and labels"
- "model Rank IC degraded below threshold, schedule retraining"
- "backtest is missing for the latest allocation run, run backtest"
- "paper metrics changed, regenerate summary tables"

Recommended implementation:

- start with rules-first planning
- then optionally add LLM reasoning for non-routine diagnosis and prioritization

This helps preserve determinism while still moving toward real agent behavior.

### 4. Add A Research Agent

This is the best place for LLM usage that does not violate the PDFs.

The research agent can:

- analyze feature importance and SHAP outputs
- summarize walk-forward results
- propose feature experiments
- compare ablation outcomes
- write draft notes for the paper

What it should not do:

- directly generate trading signals that bypass the deterministic models
- replace validation logic

### 5. Add A Risk Or Review Agent

This agent should act like a skeptical reviewer before any important state transition.

It can check:

- whether data coverage is acceptable
- whether leakage checks passed
- whether model performance is stable enough
- whether turnover and costs are within acceptable range
- whether a new model materially beats the baseline

It can block or flag actions such as:

- promoting a model to production-like status
- changing portfolio rules
- using incomplete upstream data

### 6. Keep The Financial Core Deterministic

This is the most important design recommendation.

Use AI for:

- planning
- diagnosis
- summarization
- experiment suggestion
- operator support
- paper/report writing

Do not use AI as the direct source of:

- feature calculations
- label generation
- backtest accounting
- allocation math
- production portfolio weights without deterministic validation

That hybrid design is the strongest fit for your PDFs.

## Suggested Build Order

### Priority 1: Documentation and Architecture Lock

Do next:

- decide that the current quant pipeline stays as the execution core
- define the boundaries of AI vs deterministic logic
- create an architecture note showing the control plane and execution plane

### Priority 2: Tool Registry and State Store

Build:

- `tool_registry.py`
- `state_store.py`
- wrappers around the current pipeline agents

This gives the future planner something clean to call.

### Priority 3: Supervisor and Planner

Build:

- `supervisor.py`
- `planner_agent.py`

The supervisor should:

- load state
- ask planner for next action
- invoke a tool
- store results
- continue until stop conditions are met

### Priority 4: Review and Approval Gates

Build:

- `risk_agent.py`
- human approval checkpoints

This is especially important if the system is ever presented as more than an academic prototype.

### Priority 5: Research and Reporting Agent

Build:

- `reporting_agent.py`
- `research_agent.py`

This will help with:

- weekly summaries
- paper section drafting
- feature and model comparison memos
- dashboard narratives

### Priority 6: Optional LLM Integration

If you add LLM support, use it behind interfaces and structured outputs.

Do not hardwire the whole project to one provider. Add:

- model client abstraction
- prompt templates
- JSON-schema-constrained responses
- trace logging for agent decisions

## Non-Agentic Gaps You Still Need To Finish

Even if you build the agentic layer, you still have some roadmap items to complete.

High-value remaining work:

1. Wire Optuna into `ModelAgent` instead of only listing it in config.
2. Add a true feature-decay monitor with rolling rank-IC and feature retirement/down-weight suggestions.
3. Export a richer feature redundancy report including VIF.
4. Add stronger unit tests around each agent, not only smoke tests.
5. Add schema documentation markdown files if you want closer roadmap compliance.
6. Add paper/report artifact generation for reproducible tables and figures.
7. Decide whether social sentiment is in scope or explicitly out of scope.

## Suggested Repo Additions

A practical structure could be:

```text
orchestration/
  __init__.py
  contracts.py
  state_store.py
  tool_registry.py
  supervisor.py
  planner_agent.py
  execution_agent.py
  risk_agent.py
  research_agent.py
  reporting_agent.py

memory/
  system_state.json
  task_log.json
  approvals.json

docs/
  agentic_architecture.md
  decision_policies.md
  promotion_checklist.md
```

## Recommended Interpretation Of "Agentic AI System Entirely"

The strongest version of your project is not:

- replacing the quant system with an LLM workflow

The strongest version is:

- a deterministic quant engine
- wrapped by an agentic AI operating system

That gives you:

- academic defensibility
- reproducible finance logic
- a genuinely agentic workflow layer
- a much stronger story for both the paper and product direction

## Final Recommendation

Build CHF as a hybrid system:

- deterministic quant execution core
- agentic planning, monitoring, review, and reporting layer on top

That approach respects the PDFs, preserves technical credibility, and still gives you a real agentic AI system worth showing.
