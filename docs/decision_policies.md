# CHF Orchestration Decision Policies

The orchestration layer is deterministic and rule-based. These are the rules
it applies, and why. The overriding principle: **AI (or automation) may plan
and observe, but a human must sign off before anything that can change
research results or rewrite canonical data.**

## Approval gates

| Stage | result_affecting | requires_approval | Rationale |
|-------|------------------|-------------------|-----------|
| `model` | yes | yes | Retraining rewrites predictions/metrics — the frozen research results the paper and paper-trading books stand on. |
| `portfolio` | yes | yes | Changes allocation construction, i.e. what the strategy actually holds. |
| `backtest` | yes | yes | Rewrites the performance record used for claims and comparisons. |
| `alpha_research` | yes | yes | Re-runs candidate experiments; the alpha verdict is the single alpha authority and must not drift silently. |
| `universe` | no | yes | Rewrites the universe definition (canonical PIT snapshot); every downstream artifact depends on it. |
| `features` | no | yes | Rewrites the canonical feature store (`data/features/full_features.parquet`) that frozen models were trained on. |
| `labels` | no | yes | Rewrites the canonical modeling dataset / label matrix. |
| `market` | no | no | Incremental raw ingestion; verified post-run by `scripts/verify_market_run.py`. Does not by itself change research outputs. |
| `onchain` | no | no | Same as market: raw ingestion with a post-run verifier. |
| `papertrade` | no | no | Appends to isolated paper-trading book state; never touches research artifacts. |

Rules of the gate:

- Approvals are per **step id** (`<goal>:<stage>`), stored in
  `memory/approvals.json`, and are **never granted automatically** — the only
  path to `approved` is the explicit `--approve` CLI / `Supervisor.approve()`.
- A rejected step stays blocked; the next plan opens a fresh pending record
  so it can be re-reviewed rather than silently retried.
- `--force` overrides *freshness skipping only*. It never overrides an
  approval gate or a risk check.

## Risk agent pre-flight checks (every non-skipped step)

1. **Inputs exist** — every declared upstream artifact path must exist.
   Blocks runs on incomplete upstream data.
2. **Disk floor** — more than 5 GB free on the project volume.
3. **Single orchestrator** — no foreign `memory/orchestrator.lock`
   (another pid). Prevents concurrent orchestrators from interleaving stage
   writes.
4. **Approval** — gated steps need an `approved` record (see above).

Any failed check blocks that step; blocked steps are recorded in the task
log and shown in the report with the exact reason and the approve command.

## Freshness / skip rules

- Skip a stage when all outputs exist and the oldest output mtime ≥ the
  newest input mtime.
- Never skip a stage whose upstream re-runs in the same plan (cascade).
- `papertrade` is fresh only if its manifest `as_of` == today (UTC): it is a
  daily cycle, not a build artifact.

## Execution policy

- Steps run **sequentially in DAG order**; the plan **stops at the first
  failure** (stage exit ≠ 0 *or* verifier exit ≠ 0). Downstream steps are
  recorded `not_run` — never executed against suspect inputs.
- After each successful stage, its `scripts/verify_*_run.py` verifier runs;
  a verifier failure counts as a stage failure.
- Every run appends a `RunRecord` (command, exit codes, stdout/stderr tails)
  to `memory/task_log.json` — an audit trail of what the orchestrator did.

## What automation must never do

- Approve its own gated steps.
- Generate trading signals, features, labels, or backtest numbers directly
  (all numbers come from the deterministic pipeline).
- Execute real-money trades — out of scope entirely; see
  `docs/promotion_checklist.md`.
