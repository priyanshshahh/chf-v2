# CHF — Case Study for Quant Reviewers

*A crypto systematic-research platform built to the standard of a real fund's
process — and honest enough to report that it has not (yet) found verified
alpha.*

> **Read this first.** The single most important fact about CHF is the result:
> `alpha_verified = false`. Every candidate strategy CHF has produced fails its
> own out-of-sample backtest gate after costs and benchmark checks. That is not
> a bug to be hidden — it is the deliverable. This document argues that the way
> CHF *reaches and reports* that negative result is exactly what distinguishes a
> disciplined quant researcher from someone curve-fitting a backtest.

---

## 1. The research question

> Do liquid crypto assets exhibit **cross-sectional** structure — in market
> microstructure, momentum, liquidity, and on-chain activity — that survives
> leakage-safe modeling, transaction costs, and honest out-of-sample validation,
> and beats the obvious passive benchmarks (BTC, ETH, a 50/50 BTC-ETH mix, and an
> equal-weight universe)?

This is deliberately a *falsifiable* question with a real null hypothesis. The
system is built so that the answer can come back **"no"** — and it does.

---

## 2. Why the process is the signal (not a Sharpe ratio)

Anyone can produce a backtest with a 3.0 Sharpe. The hard, fundable skill is
building a research process that **won't let you fool yourself**. CHF's negative
result is credible precisely because of the guards that produced it:

- **Survivorship-free universe.** The universe is reconstructed point-in-time
  from a keyless `cmc_web_pit` historical source, not from *today's* list of
  winners. Delisted/failed coins remain in-sample for the dates they were live,
  so the backtest is not silently only trading survivors.
- **Purged + embargoed walk-forward.** Validation uses an expanding walk-forward
  (504-day initial train, 30-day test, 30-day step) with a **30-day embargo**
  and purge around each test window, so a label that looks 30 days forward cannot
  leak into the training fold that predicts it.
- **Single alpha authority.** Exactly one component — `BacktestAgent` — is
  permitted to stamp `alpha_verified`. The signal-research agents
  (`ModelAgent`, `AlphaResearchAgent`) can find promising Rank IC all day long;
  they are explicitly forbidden from claiming alpha. This prevents the classic
  failure where an encouraging in-sample IC gets narrated into a "strategy."
- **Prediction-only portfolio inputs.** `PortfolioAgent` rejects any realized
  return or label column from its allocation inputs. Allocations can only be
  built from ex-ante predictions, closing the most common leakage path.
- **Triple-barrier & meta-labeling** (López de Prado style) available in
  `features/labeling.py`: path-dependent take-profit/stop-loss/vertical-barrier
  labels, plus a second-stage meta-model that sizes (not just directs) the
  primary signal — trained strictly inside each fold on train-only outcomes.
- **Dual-book accounting that reconciles at 0 bps.** The paper-trading engine
  computes each book's NAV; an *independent* accounting module recomputes NAV
  from the append-only ledger. The two agree to ~1e-12 bps against a 1.0 bps
  tolerance. If they ever diverged, that would be an alert, not a footnote.
- **Cost realism.** Backtests are run across a cost sweep (0, 10, 20, 50, 100
  bps); the headline verdict uses 20 bps, not a frictionless 0.

The result of all of this: **no verified alpha**. A researcher who understands
overfitting reads that and trusts the rest of the system more, not less.

---

## 3. The honest result

- **`alpha_verified = false`** for every candidate, at the 20 bps cost gate.
- The signal search *does* surface statistically promising candidates
  (positive, t-significant Rank IC on some model/feature/horizon combos). They
  simply do not clear the post-cost, post-benchmark backtest gate — several beat
  BTC and the equal-weight universe but lose to ETH and the 50/50 mix, and none
  clear the gate cleanly and robustly.
- The advanced ML layer (XGBoost, CatBoost, a walk-forward stacking ensemble,
  optional regime-conditional and meta-labeling stages) was added and run
  head-to-head against the linear/RF/LightGBM baselines on the identical panel.
  **See `docs/RESEARCH_RESULTS_SUMMARY.md` / the model leaderboard for the
  head-to-head Rank IC.** The point of that exercise was not to manufacture a
  win; it was to check whether more model capacity changes the conclusion. It
  does not materially — which is itself the expected, honest finding: on this
  feature set and horizon, gradient boosting and stacking do not turn a
  non-verified signal into a verified one.

**What would be dishonest** is reporting the best in-sample Rank IC as if it were
tradable edge. CHF structurally cannot do that: the model stage does not have
the authority to claim alpha.

**The sharper reading of the null (2026-07).** The signal screen is not marginal:
the strongest configuration (`elastic_net / market_only / 30d`) reaches a mean
Rank IC of `0.1271` at `t = 9.60` across folds — p ≈ 10⁻²¹ — yet `alpha_verified`
is still `false`. Statistical significance and economic significance are
independent, and two *structural constraints* (not signal absence) best explain
the gap: (1) the book is **long-only**, so a purely cross-sectional
(market-neutral) ranking edge cannot be expressed — β is forced to ≈1 into a
−46% equal-weight market; and (2) the out-of-sample window (2025-03 → 2026-05) is
a **single bear regime**, exactly where cross-sectional momentum is known to
underperform, while bull/recovery regimes sit untested in the training split.
Both are falsifiable next experiments — a long/short variant and a wider
walk-forward — not excuses. See `docs/RESEARCH_RESULTS_SUMMARY.md` →
"Interpreting the Null Result" and `docs/SCALING_ROADMAP.md`.

---

## 4. System architecture

CHF is ~76 first-class Python modules organized as deterministic, testable
agents and services. It is not a notebook.

```
                          ┌──────────────────────────────────────────────┐
                          │              RESEARCH PIPELINE               │
                          │  (9 deterministic stages, single CLI)        │
                          └──────────────────────────────────────────────┘
  UniverseAgent ─► MarketDataAgent ─► OnChainAgent ─► FeatureAgent ─► LabelAgent
   (PIT, survivor-   (OHLCV ingest,    (CoinMetrics,   (leakage-safe   (exact fwd
    ship-free)        multi-provider)   DeFiLlama...)    features)       + triple-barrier)
        │                                                                     │
        ▼                                                                     ▼
   ModelAgent  ─────────────────►  AlphaResearchAgent  ────────►  PortfolioAgent
   (purged walk-forward;           (signal-only expansion;         (deterministic
    ridge/RF/LGBM +                 CANNOT claim alpha)             allocations from
    XGB/CatBoost/stacking)                                          predictions only)
        │                                                                     │
        └──────────────────────────────►  BacktestAgent  ◄────────────────────┘
                                          (SINGLE ALPHA AUTHORITY:
                                           costs + benchmarks + sanity
                                           => alpha_verified true/false)

  ┌───────────────────────────────────────────────────────────────────────────┐
  │  EXECUTION / OPS LAYER  (runs on the research output, no real money)        │
  ├───────────────────────────────────────────────────────────────────────────┤
  │  Paper-trading engine (13 virtual books)                                    │
  │     • canonical_best_model  • 3 candidate books  • 2 benchmark books        │
  │     • 7 strategy sleeves: trend, xsmom, carry, technical, carry-neutral,    │
  │       stat-arb, defi-yield                                                  │
  │  Accounting (5): append-only ledger, independent NAV, fees/HWM,             │
  │     reconciliation (0 bps), hash-chained audit log                          │
  │  Monitoring (10): data-quality, signal-health, model-decay, exec-quality,   │
  │     risk-report, shadow-NAV, watchdog, champion/challenger, notifier        │
  │  Orchestration (8): planner ─► risk-check ─► execute ─► report, w/ approvals │
  │  Institutional reporting: monthly investor letter, dashboards, FastAPI      │
  └───────────────────────────────────────────────────────────────────────────┘

  DATA BREADTH: 21 provider adapters (16 keyless-capable), incl. derivatives
  (OKX/Bybit/Coinglass funding & OI), DeFiLlama TVL/yields, FRED macro.
```

**Module tally (distinct `.py` modules):**

| Layer | Count | Notes |
|---|---:|---|
| Pipeline agents | 11 | 9 stages + universe CMC/free variants |
| Strategy sleeves | 11 | 7 concrete sleeve classes incl. delta-neutral carry & stat-arb |
| Data providers | 21 | 5 new keyless collectors this session |
| Accounting | 5 | ledger / NAV / fees / reconcile / audit |
| Monitoring | 10 | includes notifier + shadow-NAV |
| Paper trading | 6 | 3 broker impls + engine/funding/prices; 13 books |
| Orchestration | 8 | plan → risk → execute → report + approvals |
| Ops / cost model | 2 | scheduler, market-impact cost model |
| Features (labeling) | 2 | triple-barrier + fractional differentiation |
| **Total** | **~76** | plus tests, docs, configs, frontend |

---

## 5. What is real vs. simulated

Being explicit about this is part of the honesty thesis:

- **Real:** the data pipeline, the leakage-safe modeling, the walk-forward
  validation, the backtest math, the accounting/reconciliation, the monitoring,
  the tests (600+ passing, research-integrity assertions included). These run on
  real historical market/on-chain data.
- **Simulated:** all trading. There is **no real broker and no real capital.**
  The 13 books are *virtual* — each starts at a notional NAV and trades against
  historical/near-real-time prices through an internal simulator broker (an
  Alpaca paper adapter exists but is off by default). The "NAV," "fills," and
  "fees" are paper. Nothing here is a live track record, and the system never
  claims one.

---

## 6. The honest roadmap to a verified edge

A negative result is a starting point, not a dead end. The path to a *verified*
edge — without abandoning the rigor — is laid out in
[`docs/SCALING_ROADMAP.md`](SCALING_ROADMAP.md). In brief:

1. **Wider, deeper features** — derivatives (funding/OI/basis), cross-exchange
   microstructure, and on-chain flow are now collected but under-exploited.
2. **Genuinely market-neutral construction** — the delta-neutral carry and
   stat-arb sleeves target factor-hedged returns that don't need beta to win.
3. **Higher-frequency / shorter-horizon** signals where costs are the binding
   constraint the current 20 bps gate is designed to respect.
4. **Live paper track record** — let the 13 books accumulate genuine
   out-of-sample NAV over months; the monitoring + reconciliation stack already
   exists to keep that record honest.

Only after a candidate clears the *same* `BacktestAgent` gate **and** holds up in
forward paper trading would CHF flip `alpha_verified`.

---

## 7. How to run

```bash
# 0. Environment (no API keys required for the local demo)
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.txt

# 1. One-command end-to-end demo on shipped local data:
./scripts/demo.sh
#    -> prints the alpha_verified verdict, advances the 13 paper books,
#       reconciles dual-book NAV (0 bps), runs ops monitors, writes the
#       monthly investor letter, and points at the dashboards.

# 2. Full research pipeline from scratch (needs data providers):
./run_all.sh
#    or stage-by-stage:
python main.py universe  &&  python main.py market   &&  python main.py onchain
python main.py features  &&  python main.py labels   &&  python main.py model
python main.py portfolio &&  python main.py backtest        # <- prints alpha_verified

# 3. Enable the advanced ML models (XGBoost/CatBoost/stacking), opt-in:
CHF_MODELING_ADVANCED=1 python main.py model --config configs/run_config.yaml

# 4. Full test suite (research-integrity + unit):
python -m pytest tests/ -q

# 5. Dashboards:
./run_dashboard.sh            # research dashboard  (Streamlit)
./run_product_dashboard.sh    # product dashboard   (Streamlit)
python main.py serve          # FastAPI service
```

---

*CHF's pitch to a fund is not "look at this Sharpe." It is: here is a researcher
who built the full institutional apparatus — survivorship-free data, leakage
guards, a single alpha authority, reconciled accounting, live monitoring — and
then used it to honestly reject their own strategies. That is the person you want
touching real capital.*
