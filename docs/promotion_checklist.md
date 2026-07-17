# Strategy Promotion Checklist

Path: **research → paper trading → (hypothetical) production**.
Each promotion is a human decision recorded through the orchestration
approval flow (`memory/approvals.json`). **Real-money execution is out of
scope for this project** — the final stage is documented only so the bar is
explicit if the system is ever taken beyond an academic prototype.

## Stage 1 → 2: Research → Paper trading

Data & reproducibility

- [ ] Universe snapshot is point-in-time and survivorship-aware; membership
      mask (`universe_membership_daily.parquet`) covers the full backtest span.
- [ ] All verifiers green for the producing run: `verify_universe_run`,
      `verify_market_run`, `verify_onchain_run`, `verify_feature_run`,
      `verify_label_run`, `verify_model_run`, `verify_portfolio_run`,
      `verify_backtest_run`, `verify_alpha_research_run`.
- [ ] Pipeline re-run from the same config reproduces the same artifacts
      (seeded; config hash recorded).
- [ ] No leakage: features strictly lagged vs. labels; walk-forward folds
      have no overlap.

Alpha evidence

- [ ] **`alpha_verified` is true** in the alpha report — the single alpha
      authority (`data/reports/alpha_report.json`). No promotion on raw
      backtest Sharpe alone.
- [ ] Candidate beats *all* benchmarks (BTC, ETH, EW top-100) net of costs
      over the evaluation window.
- [ ] Rank IC positive and stable across folds (not driven by one fold or
      one asset).
- [ ] Turnover and cost assumptions (bps) documented; results survive a
      cost-sensitivity bump.

Governance

- [ ] Approval recorded for the producing `model` / `portfolio` /
      `backtest` / `alpha_research` steps.
- [ ] Strategy config frozen (model, horizon, top-k, weighting) and given a
      named paper book in the papertrade config.

## Stage 2 → 3: Paper trading → (hypothetical) production

Sustained paper performance — the core requirement

- [ ] **≥ 90 calendar days of daily paper cycles** with the book config
      unchanged.
- [ ] Paper NAV outperforms the paper benchmark books (`benchmark_btc`,
      `benchmark_btc_eth_5050`) over the window, net of modeled costs.
- [ ] Realized paper metrics are consistent with the backtest (no large
      live/backtest gap in return, vol, or turnover).
- [ ] Max drawdown within the tolerance stated before the paper period began
      (pre-registered, not chosen after the fact).
- [ ] Operational reliability: papertrade cycles ran (or were explainably
      skipped, e.g. missing prices) on ≥ 95% of days; skip reasons reviewed.

Risk & review

- [ ] Feature/rank-IC decay reviewed — no evidence the signal has decayed
      during the paper period.
- [ ] Capacity/liquidity sanity check: intended notional is small relative
      to constituent dollar volume.
- [ ] Independent (second-person) review of the full evidence pack:
      alpha report, backtest summary, paper history, this checklist.
- [ ] Explicit human sign-off recorded; a dated decision memo added to
      `docs/`.

De-promotion (always in force)

- [ ] Pre-registered kill criteria: drawdown breach, N-week underperformance
      vs. benchmark books, or `alpha_verified` flipping false on re-run →
      book is demoted back to research.

## Out of scope

Exchange connectivity, order routing, custody, and any real-money execution
are **not** part of CHF. Nothing in the orchestration layer can place a
trade; the strongest claim this system supports is sustained, audited paper
performance.
