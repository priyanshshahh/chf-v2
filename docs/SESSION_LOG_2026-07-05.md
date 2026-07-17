# CHF Session Log — 2026-07-05 → 2026-07-06

Condensed engineering log of the session that took CHF from a frozen research release to an industry-grade, research-first, multi-strategy paper-traded fund system. Turn-by-turn dialogue is in [`CHAT_TRANSCRIPT_2026-07-05.md`](CHAT_TRANSCRIPT_2026-07-05.md) (abridged) and [`CHAT_TRANSCRIPT_2026-07-05_UNABRIDGED.md`](CHAT_TRANSCRIPT_2026-07-05_UNABRIDGED.md) (full).

## Goal (user's words)

Make CHF a working, research-first, production-grade automated crypto hedge fund — impressive to quant recruiters and funds — without LLM-based trading agents. Everything deterministic.

## Headline outcome

- **`alpha_verified = false`** — honest, and now credible: survivorship-free full-history data, real benchmarks, purged+embargoed walk-forward, single alpha authority. Advanced models (XGBoost/CatBoost/stacking) do **not** beat the simple baselines (baseline Rank IC 0.087 / ridge 0.064 lead; boosters mid-pack) — the expected, fundable finding.
- **~76 deterministic Python modules**, no LLM in any decision path. **625 tests pass, 2 skip.** Dual-book NAV reconciles at **0 bps**.
- **13 virtual paper books** across 7 strategy sleeves (trend, x-sectional momentum, technical ensemble, delta-neutral carry, stat-arb, DeFi-yield, + benchmarks).

## What was built / fixed, by area

**Research integrity fixes (early session):**
- Canonical portfolio stage had been silently broken since April (leakage guard rejected raw predictions) → sanitized prediction-only export; enabled real ML models canonically.
- **ccxt pagination bug** silently truncated all exchange history to one page (BTC/ETH ended 2021-11) → fixed; market file 137k→268,592 rows, survivorship-free.
- Benchmark sanity check hardened (degenerate benchmarks now fail loudly).
- CMC dataset-name collision, pyproject build backend, quotes collector interface, 12 spec-kit tasks, 2 pre-existing market-data test failures, full src/cmc code review (2 critical fixes).

**New capability layers (this session):**
- **Paper trading** (`papertrade/`) — simulator + Alpaca paper adapter; **short-side/margin engine** added (backward-compatible); funding accrual for perps.
- **Orchestration** (`orchestration/`) — planner → risk → execute → report, human approval gates, no LLM.
- **Accounting** (`accounting/`) — append-only ledger, independent NAV, 2/20 fees + HWM, hash-chained audit log, 0-bps reconciliation.
- **Monitoring** (`monitoring/`) — 10 monitors (data quality, signal health, model decay, exec quality, risk, shadow NAV, watchdog, champion/challenger) + deterministic notifier (SMTP/webhook, dedup).
- **Risk layer** (`portfolio/`) — vol targeting, concentration/liquidity limits, drawdown state machine, √-participation cost model.
- **Strategy sleeves** (`strategies/`) — trend, xsmom, technical ensemble, **true delta-neutral carry**, **stat-arb**, **DeFi-yield**, regime classifier, Sortino-keyed allocator (proposals only).
- **Data breadth** (`providers/`) — OKX/Bybit derivatives, DeFiLlama TVL/yields, FRED macro, Coinglass (all keyless-capable; Bybit/FRED geo/sandbox-blocked here but tested).
- **Advanced ML** — XGBoost, CatBoost, walk-forward stacking, regime-conditional, triple-barrier + meta-labeling + fractional differentiation (`features/labeling.py`, `features/frac_diff.py`), config-gated default-off.
- **Ops/deploy** — Docker, docker-compose, systemd unit, supervised-run script, `docs/DEV_SETUP.md`.
- **Reporting & narrative** — monthly investor letter, `docs/CASE_STUDY.md` (recruiter-facing), `docs/SCALING_ROADMAP.md`, `docs/FUND_PRACTICES_RESEARCH.md`, `docs/ADVANCED_MODEL_COMPARISON.md`, `docs/PAPER_TRADING.md`, `docs/LABELING.md`.
- **Websites** — React frontend rebaked + two new pages (`/results`, `/system`); Streamlit Fund Ops page; both verified.
- **One-command demo** — `scripts/demo.sh` (verdict → papertrade → nav reconcile → monitor → letter → dashboards).

## Research learned (the "why it's credible" part)

- Strong in-sample Rank IC (t-stats significant) does **not** survive the BacktestAgent cost/benchmark gate.
- The expanded 240-experiment search's best candidates (IC 0.127) lost 20–38% out-of-sample.
- Higher-capacity models overfit; parsimonious models generalize better on this low-signal problem.
- The system is built to return "no," and does — which is the portfolio signal.

## New CLI

`main.py papertrade | nav | monitor | letter | orchestrate` (plus the existing pipeline stages). Advanced ML: `CHF_MODELING_ADVANCED=1 main.py model`.

## Method notes

- Work executed via dependency-ordered subagent waves with strict non-overlapping file ownership; result-affecting steps behind approval gates.
- A repo pre-tool "Fact-Forcing Gate" hook required a facts statement before the first Bash/Edit/Write per file each session — satisfied inline throughout.

## Open items (not done — and why)

1. **~170 files uncommitted** — held for the user's explicit go; plan is to branch off `main` and commit in logical chunks.
2. **Valid `CMC_API_KEY`** (currently 401) — unblocks 3 paper books + spec T033. User-provided.
3. **Live paper track record** — needs months of the scheduler running; no way to fast-forward.
4. **Full stacked-ensemble run across both feature sets** — deferred (bounded comparison already answered the question; stacking + meta-labeling are built and unit-tested).
5. Optional keys: Alpaca paper, Etherscan/Dune/TheGraph; unblocked-region run for Bybit/FRED.

## Verification snapshot (end of session)

- Tests: **625 passed / 2 skipped** (`pytest tests/ --ignore=tests/test_pipeline_integration.py`).
- Frontend: `npm run build` passes; bake writes papertrade/fundops/meta JSON.
- `scripts/demo.sh`: runs end-to-end, no keys required.
- Accounting reconciliation: 0.0000 bps across live books.
- Canonical research artifacts restored/consistent; `alpha_verified=false`.
