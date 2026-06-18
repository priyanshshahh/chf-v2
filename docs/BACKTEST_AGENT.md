# Backtest Agent — Complete Reference

The Backtest Agent (`agents/backtest_agent.py`) is the **terminal node** of the CHF pipeline
and the **single component in the entire system with the authority to verify or reject
alpha**. It consumes the prediction-derived, leakage-safe portfolio allocations produced by
`PortfolioAgent`, replays them against realized market prices with a transparent vectorized
return engine, charges transaction costs, runs a slate of benchmarks with explicit sanity
checks, and emits a binary verdict: `alpha_status="passed"` or `"failed"` per strategy and
`alpha_verified` in the manifest. No other agent — not `AlphaResearchAgent`, not the model
stage — may claim verified alpha; their outputs are signal-only. The headline CHF result
(`alpha_verified=false` for all tested candidates) is produced precisely **by** this agent's
honest gating, not in spite of it. The agent does not create alpha; it evaluates realized
performance from precomputed allocations and gates it.

This is the **single exhaustive reference** for the agent: its full output contract, the
backtest engine math, the research-integrity guards, the complete lifecycle, the full config
surface, the verifier, the test inventory, the limitation register, and how it stacks up
against fund-grade discipline.

---

## 1. Output contract

All artifacts are written to `data/backtests/` (configurable via `backtesting.output_dir`).
Parquet files are written with `index=False`.

| Artifact | Contents |
|---|---|
| `equity_curves.parquet` | Per-day, per-strategy **and** per-benchmark equity track and decomposed returns |
| `backtest_summary.parquet` | One row per strategy: headline performance + gate-state columns |
| `benchmark_summary.parquet` | One row per benchmark (BTC, ETH, BTC_ETH_50_50, equal_weight_universe, cash) |
| `strategy_comparison.parquet` | Strategy-vs-benchmark deltas and the `alpha_status` verdict |
| `cost_sweep.parquet` | Each strategy re-run at every cost level in `cost_sweep_bps` |
| `benchmark_sanity_report.parquet` | Per-benchmark sanity diagnostics + `passed_sanity` flag |
| `drawdown_series.parquet` | Per-day, per-strategy running peak and drawdown |
| `turnover_report.parquet` | Per-day, per-strategy turnover, cost, and return decomposition |
| `subperiod_performance.parquet` | **(NEW)** Per-strategy/benchmark performance recomputed over each contiguous time window (regime robustness) |
| `alpha_report.json` / `alpha_report.md` | Best-by-Sharpe / best-by-return, `any_strategy_passed_alpha_status`, diagnostic note, survivorship-limitation string, benchmark rows |
| `backtest_summary.json` | JSON mirror of `backtest_summary.parquet` (records orient) |
| `backtest_manifest.json` | Full provenance: run_id, snapshot_id, `data_content_hash`, input paths, strategies, benchmarks, cost config, allocation-mode/gate state, `alpha_verified`, `benchmark_sanity_passed`, subperiod state, warnings, limitations, output-file map |
| `data_quality_backtest.md` | Human-readable QA tear-sheet (strategy summary, benchmarks, alpha status, limitations) |

### Key columns

**`equity_curves.parquet`** (and the benchmark rows share this schema):
`date_ts, strategy_name, portfolio_value, gross_return, net_return, transaction_cost,
turnover, n_positions, benchmark_type, snapshot_id, run_id`. `benchmark_type` is `"strategy"`
for strategies and `"benchmark"` for benchmark tracks. `net_return = gross_return −
transaction_cost`.

**`backtest_summary.parquet`** — from `_perf_from_returns` plus per-strategy gate state:
`strategy_name, n_days, start_date, end_date, final_value, total_return, cagr,
annualized_vol, sharpe, sortino, calmar, max_drawdown, average_daily_return, hit_rate,
best_day, worst_day, average_turnover, annualized_turnover, total_cost_drag,
average_positions, min_positions, max_positions, failure_reason, transaction_cost_bps,
alpha_gate_passed, signal_gate_passed, candidate_for_backtest, allocation_mode,
missing_held_return_fraction`.

**`strategy_comparison.parquet`** — the verdict table:
`strategy_name, Sharpe, CAGR, max_drawdown, total_return, average_turnover, total_cost_drag,
beats_btc, beats_eth, beats_btc_eth_50_50, beats_equal_weight, excess_return_vs_btc,
excess_return_vs_eth, excess_return_vs_equal_weight, excess_sharpe_vs_btc,
excess_sharpe_vs_equal_weight, alpha_status, failure_reason, research_note`.

**`benchmark_sanity_report.parquet`**:
`benchmark_name, start_date, end_date, n_days, start_value, final_value, total_return,
min_daily_return, max_daily_return, max_abs_daily_return, valid_price_days,
average_assets_with_valid_prices, min_assets_with_valid_prices,
days_with_missing_held_prices, sanitized_extreme_return_count, passed_sanity,
failure_reason`.

**`cost_sweep.parquet`**: `strategy_name, cost_bps, Sharpe, CAGR, max_drawdown,
total_return, final_value`.

**`drawdown_series.parquet`**: `date_ts, strategy_name, portfolio_value, running_peak,
drawdown`.

**`turnover_report.parquet`**: `date_ts, strategy_name, turnover, transaction_cost,
gross_return, net_return, n_positions`.

**`subperiod_performance.parquet`** (NEW): `subperiod, start_date, end_date, strategy_name,
benchmark_type, n_days, total_return, cagr, sharpe, sortino, max_drawdown, hit_rate,
total_cost_drag`. `subperiod` is `full_period` plus `segment_i_of_N` labels (see §4).

---

## 2. The backtest engine

The core engine is **transparent vectorized math** in `_perf_from_returns` and
`_run_strategy` (and the benchmark twin `_run_benchmark_weights`). `vectorbt` is imported
at module load and `_VBT_AVAILABLE` is set if present, but the `from_orders` path is **not**
wired — the custom vectorized engine is used by design. This is a deliberate
research-integrity choice: every number in the equity curve traces back to a few explicit
pandas operations a reviewer can read and re-derive by hand, with no opaque third-party order
simulation between the allocations and the verdict.

### Leakage-safe return computation

For each strategy, allocation weights are pivoted onto their **`execution_date`** into a
`schedule`, forward-filled across the daily price grid into `daily_target`, masked to days
with valid positive prices (`effective_weights`), and then **shifted by one day**:

```
prev_weights   = effective_weights.shift(1).fillna(0.0)
turnover       = (effective_weights - prev_weights).abs().sum(axis=1)
gross_returns  = (prev_weights * asset_returns).sum(axis=1)
transaction_cost = turnover * (cost_bps / 10000.0)
net_returns    = gross_returns - transaction_cost
```

The `shift(1)` is the leakage guard at the engine level: weights set on `execution_date`
earn the asset return on the **next** day, never the same day. (The first equity-curve row
therefore has `gross_return == 0`, which the test suite asserts.) `asset_returns` is
`self._returns = prices.pct_change(fill_method=None)`. Equity compounds as
`initial_capital * (1 + net_returns).cumprod()`; drawdown is `equity / equity.cummax() − 1`.
`_perf_from_returns` annualizes with `annualization_days` (default 365 — crypto trades every
calendar day) and computes Sharpe, Sortino, Calmar, max drawdown, hit rate, turnover, and
cost-drag aggregates.

---

## 3. Research-integrity guards (do not violate)

**Input validation (`_validate_allocation_inputs`).** Required columns are
`{date_ts, signal_date, execution_date, symbol, strategy_name, weight}`. The agent rejects:

- **Look-ahead**: when `fail_on_lookahead` is true, any row with
  `execution_date <= signal_date` (same-day or earlier execution) raises.
- **Duplicate rows**: any duplicate `(date_ts, symbol, strategy_name)` raises.
- **Bad weights**: null/non-finite weights raise.
- **Shorts in a long-only book**: unless `allow_short` is set, any weight `< -1e-12` raises.
- **Position-cap breach**: any weight above `max_weight` (alias `max_position_weight`) raises.
- **Gross-exposure cap**: per-`(date_ts, strategy_name)` sum of `|weight|` above
  `target_gross_exposure` raises.

`PortfolioAgent`'s prediction-only contract is upstream; this agent additionally refuses to
silently accept any allocation that violates the long-only / exposure / look-ahead invariants.

**Benchmarks (`_run_benchmarks`).** Five reference tracks, all clipped to the strategy's
allocation window: `BTC`, `ETH`, `BTC_ETH_50_50` (rebalanced at `benchmark_rebalance_frequency`
anchors, default monthly), `equal_weight_universe` (rebalanced at the same anchors over assets
with ≥30 days of history and no extreme recent returns), and `cash` (flat at
`initial_capital`). Sanity checks run on the resulting summaries:

- `impossible_btc_eth_50_50_return` — if BTC and ETH both lose money over the window but the
  50/50 mix returns `> 0.25`, the mix row is flagged `passed_sanity=False`.
- `absurd_equal_weight_daily_return` — if the equal-weight benchmark's `max_abs_daily_return`
  exceeds 10.0 (i.e. >1000%/day), it is flagged.

The verifier (`scripts/verify_backtest_run.py`) re-derives both checks independently from the
persisted parquet, so a tampered file is caught.

**Alpha gating (`_build_strategy_comparison`).** A strategy earns `alpha_status="passed"`
**only when every one of these holds**:

- `allocation_mode == "signal_candidate_for_backtest"` (and not in the diagnostic set
  `{diagnostic_not_live_trading, override_diagnostic, leaderboard_missing_diagnostic}`),
- `signal_gate_passed` is true,
- `candidate_for_backtest` is true,
- `benchmark_sanity_passed` (all benchmark rows passed sanity),
- the strategy did not fail (`failure_reason` empty, finite `final_value > 0`),
- **beats equal-weight on both Sharpe and total return**,
- `max_drawdown >= max_allowed_drawdown` (i.e. drawdown within the configured bound),
- beats `BTC_ETH_50_50` on total return **or** Sharpe,
- `final_value > initial_capital`.

Any failure yields `alpha_status="failed"` with a human-readable `research_note` /
`failure_reason` (e.g. `diagnostic_allocation_not_alpha_eligible`, `signal_gate_not_passed`,
`not_candidate_for_backtest`, `benchmark_sanity_failed`). **Diagnostic allocations can never
pass** — this is enforced twice: once in the eligibility predicate, and again as a hard
backstop in `_validate_result`, which raises `BacktestAgentError` if a diagnostic-mode
allocation produced any `alpha_status=="passed"` row before persistence.

### Newer hardening

- **`data_content_hash` (`_content_hash`).** A deterministic 16-hex SHA-256 fingerprint over
  the price matrix + allocations, normalized (UTC dates, sorted columns/rows) so identical
  inputs hash identically regardless of row/column order — mirroring the other agents'
  reproducibility guarantee. Stored in the manifest and the MLflow tags.
- **Gated MLflow logging (`_log_to_mlflow`).** Logs params/metrics/tags (including
  `data_content_hash`, `allocation_mode`, `alpha_verified`) and artifacts. Fully non-fatal and
  gated by `mlflow.log_backtest_run` (the test config sets it to `False` for hygiene).
- **Subperiod robustness (`_run_subperiod_analysis` / `_build_subperiods`).** Splits the
  realized window into contiguous segments labeled by their **real date ranges**
  (`full_period`, `segment_1_of_3`, …) — never fabricated bull/bear tags — and recomputes the
  *same* proper net-return performance math per window via `_perf_from_returns`. Lets a
  reviewer see whether any apparent edge is concentrated in a single regime. Honors an explicit
  `subperiods` config list if provided; otherwise builds `subperiod_count` equal segments when
  there are enough days. Optional and config-gated (`subperiod_analysis`); when disabled the
  file is simply not written and the verifier still passes.
- **Graceful markdown fallback (`_md_table`).** Renders DataFrames via `to_markdown` but
  degrades to fixed-width `to_string` when the optional `tabulate` dependency is absent — a
  reporting nicety that never raises.
- **Cost-resilience sweep (`_run_cost_sweep`).** Re-runs every strategy at each level in
  `cost_sweep_bps` (default `[0, 10, 20, 50, 100]`) so a reviewer can see how quickly any edge
  is eroded by costs. The verifier asserts every configured cost level is present.

---

## 4. Lifecycle (prepare → run → persist)

The agent subclasses `AgentBase`; `execute()` wraps `prepare → run → persist` with retries,
status tracking, logging, and registry updates — the standard CHF agent lifecycle.

**`prepare()`** — resolves paths under the project root; requires the allocation file and the
market file to exist (raises `FileNotFoundError` otherwise); reads allocations, normalizes the
three date columns to UTC midnight, runs `_validate_allocation_inputs`; computes the strategy
window `[date_ts.min, date_ts.max]`; loads the optional allocation manifest; loads market
OHLCV, pivots `close` into a `date_ts × symbol` price matrix clipped to the window, and
computes `pct_change` returns. Empty allocations (when `fail_on_empty_backtest`) or an empty
price matrix raise.

**`run()`** — generates the `backtest_research` snapshot id; iterates strategies (optionally a
single `strategy_override`) through `_run_strategy`; runs `_run_benchmarks`; builds
`strategy_comparison`, the `cost_sweep`, the `alpha_report`, and concatenated
equity/drawdown/turnover frames; runs `_run_subperiod_analysis`; records best-by-Sharpe metrics;
and assembles the manifest (including `data_content_hash`, `alpha_verified`,
`benchmark_sanity_passed`, the survivorship limitation string, and the output-file map).

**`persist()`** — calls `_validate_result` first (see §3 backstops + non-empty-frame checks +
`fail_on_benchmark_sanity_failure`), then writes all parquet outputs, the optional
`subperiod_performance.parquet`, the JSON mirrors, `alpha_report.json`/`.md`,
`backtest_manifest.json`, `data_quality_backtest.md`, and finally calls `_log_to_mlflow`.

---

## 5. Config surface (`backtesting:` section)

Actual defaults from `configs/run_config.yaml` (the `backtesting` base section) plus
code-level fallbacks read by the agent:

| Key | Default | Meaning |
|---|---|---|
| `research_mode` | `true` | Research-integrity mode flag |
| `allocation_path` | `data/allocations/allocations_from_predictions.parquet` | Strategy allocation input |
| `allocation_manifest_path` | `data/allocations/allocation_manifest.json` | Gate-state / allocation-mode source |
| `market_path` | `data/raw/market/market_ohlcv.parquet` | OHLCV price source |
| `output_dir` | `data/backtests` | Output directory |
| `initial_capital` | `100000` | Starting equity |
| `transaction_cost_bps` | `20` | Per-unit-turnover cost in bps |
| `cost_sweep_bps` | `[0, 10, 20, 50, 100]` | Cost levels for the resilience sweep |
| `benchmark_rebalance_frequency` | `"M"` | Anchor frequency for 50/50 and equal-weight (`M`/`W`/daily) |
| `fail_on_empty_backtest` | `true` | Raise on empty allocations |
| `fail_on_missing_allocations` | `true` | Require the allocation file |
| `max_allowed_drawdown` | `-0.80` | Drawdown bound for alpha eligibility |
| `subperiod_analysis` | `true` | Recompute per-regime robustness on real net returns |
| `subperiod_count` | `3` | Equal contiguous segments + `full_period` |

Additional keys honored by the agent (code-level defaults shown where not in the base YAML):
`fail_on_lookahead` (true), `allow_short` (false), `max_weight` / `max_position_weight` (1.0),
`target_gross_exposure` (1.0), `annualization_days` (365), `max_missing_held_return_fraction`
(0.0), `fail_on_missing_held_returns` (true), `max_abs_daily_return` (10.0),
`allow_extreme_return_sanitization` (false), `fail_on_benchmark_sanity_failure` (true), and an
explicit `subperiods` list (overrides `subperiod_count`). `mlflow.log_backtest_run` (default
true) gates run logging.

Parallel sections (`backtesting_smoke`, `backtesting_alpha_candidates`,
`backtesting_candidate_*`) merge over the base `backtesting` section — used by the per-candidate
backtests; the verifier's `_merge` resolves any `backtesting*` section name back onto the base
key.

---

## 6. Verifier and tests

**`scripts/verify_backtest_run.py`** independently re-reads the persisted parquet and fails on:
missing files; empty frames; missing required columns on equity/summary/comparison/sanity;
duplicate `(date_ts, strategy_name)` rows; null/non-finite or negative cost/turnover/position
values; `max_drawdown` outside `[-1, 0]`; missing any of the four required benchmarks; date-range
inconsistency across BTC/ETH/BTC_ETH_50_50; the `impossible_btc_eth_50_50` and
`absurd_equal_weight_daily_return` checks; any failed `passed_sanity` row; missing configured
cost-sweep levels; manifest/summary strategy mismatch; and a recorded `same_day_lookahead` flag.

**`tests/test_backtest_agent_research_mode.py`** is the research-integrity suite (~30 tests):
execution-date-only application (first-row gross return ≈ 0), turnover/cost computation,
cost-monotonicity, the BTC/ETH/50-50/equal-weight benchmark slate, alpha-status requiring
benchmark outperformance, window clipping, missing-price handling without backfill bias, the
two benchmark-sanity rejections, deterministic order-independent `data_content_hash`, and the
new subperiod robustness output (written, consistent, disable-able). Treat any failure here as a
correctness failure, not flakiness.

---

## 7. Limitations / what is left

These are stated honestly in the agent's own `limitations` string, `alpha_report`, and
`data_quality_backtest.md`:

- **Survivorship.** Results are **conditional on the latest eligible survivor universe** and
  may overstate historical tradability because full point-in-time membership and delisting data
  are not yet modeled (the CMC historical-listing limitation upstream — see
  `docs/CMC_HISTORICAL_ACCESS_LIMITATION.md`).
- **No tear sheet.** No QuantStats-style tear sheet is generated; the agent emits raw parquet +
  a markdown QA report, not a polished PDF/HTML report.
- **vectorbt `from_orders` path not wired.** `vectorbt` is optional and imported but the order
  simulator is intentionally not used — the transparent custom engine is the source of truth.
- **No real-time / live execution.** This is a research backtester over precomputed daily
  allocations; there is no execution engine, intraday fills, or live paper-trading loop.

---

## 8. How it compares to a hedge fund

**Genuinely fund-grade discipline already present.** The agent is cost-aware (per-turnover bps
plus a full cost-resilience sweep), benchmark-sanity-checked against impossible reference
returns, has **single-authority alpha gating** with a hard diagnostic-mode backstop, runs
**subperiod robustness** to expose regime-concentrated edges, enforces leakage safety at the
engine level (the `shift(1)`), produces a deterministic reproducibility hash, and refuses to
overclaim — exactly the kind of "prove it survives costs, benchmarks, and out-of-sample
windows before you believe it" rigor a serious research desk demands. Producing an honest
`alpha_verified=false` rather than a flattering curve is itself the discipline.

**What a real desk adds on top.** Slippage and market-impact models (this agent charges a flat
bps cost on turnover, not size-dependent impact); capacity / liquidity-constrained position
sizing; borrow/financing costs and short availability (the book is long-only); a richer
benchmark and factor set (here BTC/ETH/50-50/equal-weight/cash, vs. multi-factor risk models,
beta/sector neutralization, and risk-adjusted attribution); live paper-trading and a real
execution/OMS layer with realistic fills; and point-in-time universe construction with full
delisting handling to remove the survivorship caveat above.
