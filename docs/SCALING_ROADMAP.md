# CHF Scaling Roadmap — Cloning a Real Hedge Fund into CHF

**Date:** 2026-07-06. Strategic plan for taking CHF from "research-grade infrastructure that works" to "a fund that could actually run capital." Companion to the tactical backlog in `docs/NEXT_STEPS.md` and the research synthesis in `docs/FUND_PRACTICES_RESEARCH.md`. Everything here stays deterministic Python — no LLM trading agents.

---

## 0. The blunt verdict on where we are

CHF has **institution-grade plumbing and an honest negative research result.** The gap to a real fund is not code quality — it's (a) no verified edge yet, (b) data breadth, (c) live/continuous operation, and (d) the last-mile realism gaps that separate a backtest from a fill. A real quant fund is ~20% strategy and ~80% data + execution + risk + ops. CHF has the ops/risk skeleton; it is thin on data breadth and execution realism, and it has not yet found an edge.

---

## 1. Are the results accurate? (Honest audit)

**Yes — accurately computed, and accurately negative.** The methodology is sound: purged+embargoed walk-forward CV, leakage guards at every stage, a single alpha authority (BacktestAgent), prediction-only portfolio inputs, real (non-degenerate) benchmarks after the pagination fix, survivorship-free universe.

But read the numbers correctly:
- **In-sample / CV Rank IC of 0.06 (canonical) to 0.127 (best expanded elastic_net) is real predictive signal — but it does NOT survive out-of-sample.** The top expanded candidates (IC 0.127, t-stat 9.6) lost 20–38% in the verification backtests. That is the single most important fact in the whole project: **statistical signal ≠ tradable alpha after costs and benchmarks.** The system is correctly refusing to fool itself.
- **`alpha_verified=false` everywhere is trustworthy**, not a bug. The earlier flattering numbers (ridge +147%) were artifacts of the truncated-data bug, now fixed.
- **The paper books have ~2 days of history — statistically meaningless.** Their value is forward, and only accrues over months.

**Accuracy caveats still open:**
- On-chain data is **sparse and 61 days stale** — any on-chain signal is currently under-powered.
- The **carry sleeve holds only the spot leg** (a documented proxy) — it is not truly delta-neutral, so its "carry" P&L is not what a real basis trade would earn.
- Transaction costs are a **flat 20 bps** — no size/liquidity-dependent slippage, which flatters high-turnover strategies.
- **Long-only** paper engine — every market-neutral / short idea is only half-representable today.

**Bottom line:** trust the pipeline, trust the "no alpha yet" verdict, distrust any single IC number as evidence of profitability, and treat the paper track record as not-yet-existent.

---

## 2. Data — what we have, what's missing, and every free source to add

### Have
Daily OHLCV (268k rows, survivorship-free, 2021→2026), CMC quotes/map/global-metrics/indices/fear-greed/altcoin-season, sparse CoinMetrics + DeFiLlama on-chain, OKX funding rates (846 rows).

### The gap
A real fund fuses **market + derivatives + on-chain + sentiment + macro**. CHF is strong on market, thin on the other four. Breadth of *orthogonal* data is where edges actually come from.

### Free / freemium APIs to integrate (priority order)

**Derivatives (highest value — this is where crypto alpha lives):**
- **Binance / OKX / Bybit public** — funding rates, open interest, long/short ratio, mark/index price. Keyless. (Binance is geo-blocked here → 451; OKX/Bybit work.) → enables true basis/carry, funding-momentum, OI-divergence signals.
- **Coinglass (free tier)** — aggregated funding, OI, liquidations across venues. → liquidation-cascade mean-reversion (the ANB signal).
- **Deribit public** — options IV, term structure, put/call skew. → volatility strategies, a real vol-regime signal.

**On-chain (free, no key — expand aggressively):**
- **DeFiLlama** (keyless) — TVL by chain/protocol, **yields** (staking/lending APYs), stablecoin flows, DEX volumes, fees/revenue. → a whole DeFi-yield sleeve + fundamental valuation (P/F, P/S ratios).
- **CoinMetrics Community** (keyless) — NVT, MVRV, active addresses, realized cap, SOPR. Already partially wired — refresh + widen coverage.
- **Etherscan / Blockchair** (free key, 5 rps) — gas, whale flows, exchange in/outflows.
- **The Graph / Dune** (free tier, key) — custom on-chain queries (DEX LP, governance, unlocks).
- **Artemis, Token Terminal, Glassnode** (limited free) — fundamentals; use sparingly.

**Sentiment / flow:**
- **Alternative.me Fear & Greed** (keyless) — have it.
- **CryptoPanic** (free news API), **LunarCrush** (limited free — social volume/sentiment), **Santiment** (limited). → deterministic sentiment *scores* (counts/z-scores, not LLM).

**Macro (free, no key — funds always condition on this):**
- **FRED** (keyless) — DXY, 2y/10y yields, VIX, M2, real rates. → risk-on/off macro regime overlay.
- **yfinance** — SPX, gold, NASDAQ correlations.

**Execution / data cross-check:**
- **Alpaca** (free paper + keyless historical crypto bars + websocket streams) — already adapted; add the `/v2/account/portfolio/history` endpoint for an independent equity series, and use its data feed to cross-check the price pipeline.
- **ccxt** — unify all exchange access (already used for ingestion).

### Storage scaling
Move from flat parquet files to a **DuckDB or ClickHouse** backend for the query layer (CHF already has `pipelines/duckdb_engine.py` — lean into it). Parquet is fine for artifacts; a columnar DB is needed once intraday/multi-source data lands.

---

## 3. ML models — what we use, what's weak, what to add

### Have
Baseline cross-sectional mean, Ridge, RandomForest, LightGBM (canonical); ElasticNet, GradientBoosting, rule-based alphas (research agent); SHAP attribution; Optuna (now wired, off by default); purged walk-forward CV.

### Weaknesses
- Only tree + linear models — no gradient-boosting diversity (XGBoost/CatBoost), no sequence models.
- Labels are mostly raw/excess forward return — the **labeling** is where most quant edge is lost.
- No meta-labeling, no position-sizing model, no regime-conditional models.

### Add (in value order)
1. **Better labels (biggest lever):** triple-barrier labeling (López de Prado — take-profit/stop/time barriers instead of fixed-horizon return), meta-labeling (a second model that decides *whether to act* on the primary signal — improves precision dramatically), sample weighting by uniqueness/return-attribution. This alone often matters more than the model choice.
2. **Model diversity + stacking:** XGBoost, CatBoost, and a regularized linear model, combined by a walk-forward stacker. Ensembles beat any single model out-of-sample.
3. **Regime-conditional models:** train separate models per regime (the `strategies/regime.py` classifier already exists) — a bull-market momentum model and a bear-market defensive model, selected by live regime.
4. **Fractional differentiation** of features (make series stationary while keeping memory — López de Prado) before feeding models.
5. **Quantile regression / conformal prediction** for uncertainty — size positions by predicted confidence, not just predicted sign.
6. **Careful sequence models (LSTM/Temporal Fusion Transformer)** — only after the above, with heavy purging; crypto's low signal-to-noise punishes over-parameterized nets. Treat as experimental.
7. **Kelly / risk-parity position sizing** on top of the signal, replacing equal-weight.

---

## 4. Strategies — clone the real fund playbook

### Have (as paper sleeves)
Parsimonious trend (ANB-style), BTC-relative cross-sectional momentum with stablecoin gate (Solidum), funding carry (spot-leg proxy), 5-family technical ensemble (virattt), regime classifier, Sortino-keyed allocator.

### The real-fund menu — what to build next
1. **True delta-neutral basis/carry** (fix the proxy): long spot + short perp, accrue funding, account both legs. This is the industry's bread-and-butter (Nickel, Pythagoras, ANB, Kbit, Active Digital all run it) — steady low-vol returns. **Highest priority strategy.** Needs perp funding + short-side accounting.
2. **DeFi yield sleeve** (Arca, Apollo, Laser) — rotate across DeFiLlama yields (staking, lending, LP) by risk-adjusted APY with protocol-age/TVL filters. Keyless data, genuinely uncorrelated returns.
3. **Statistical arbitrage / pairs** (market-neutral) — cointegration-based spreads between correlated assets (ETH/BTC, L1 baskets), z-score entry/exit. Needs short capability.
4. **Volatility strategies** (Wave's covered calls, vol-arb) — sell premium / trade IV-RV spread. Needs Deribit options data.
5. **Liquidation-cascade mean reversion** (ANB) — fade forced-liquidation spikes using Coinglass liquidation data.
6. **Cross-sectional value** — fundamental ratios (P/F, P/S from Token Terminal/DeFiLlama) as a long/short factor.
7. **Event-driven** — token unlock calendars (unlocks → supply shock → short), listing/delisting momentum.
8. **Macro overlay** — scale gross exposure by the FRED risk-on/off regime (the missing piece that would have saved Brevan Howard Digital in 2025).

**Then combine them the pod-shop way** (already scaffolded): each sleeve is an independent book, the allocator distributes capital by trailing Sortino with regime multipliers, humans approve. That IS the multi-strategy fund structure — CHF just needs more, better sleeves feeding it.

---

## 5. Agents — the 28, how they work, and the gaps

**Current 28 (all deterministic):** 9 pipeline (Universe→…→Backtest) + 5 orchestration (Planner/Risk/Execution/Reporting/Supervisor) + PaperTrade engine + 5 accounting + 8 monitoring + (4 sleeves + regime + allocator).

**How they work:** pipeline agents are a fixed DAG driven by config; orchestration adds goal→plan→risk-check→approve→execute→report with human gates; monitoring runs daily and alerts; accounting keeps independent dual books; sleeves generate signals into paper books.

**Gaps to close:**
- **No live-integration agent** — the risk pipeline and allocator produce *proposals/artifacts* that are not yet wired into the papertrade engine. Building that (behind the gate) is the step that makes risk controls actually bind.
- **No execution-quality/TCA agent for real fills** — only backtest-cost assumptions.
- **No data-ingestion agent for the new sources** (derivatives/DeFi/macro) — each needs a collector like `providers/funding_rates.py`.
- **Monitoring alerts go to files, not a channel** — need a real notifier (email/Slack/Telegram webhook, deterministic templates).
- **Scheduler is local** — no supervision/restart/cloud.

---

## 6. Scaling to working conditions (the ops leap)

1. **Continuous operation:** run `./run_scheduler.sh` under a process supervisor (systemd/pm2/Docker), with the watchdog + heartbeat already built, and wire alerts to a real channel.
2. **Cloud deployment:** the pipeline is a long-running Python system — it wants a small always-on VM (or a container), not edge functions. The React site can go to static hosting; the Streamlit console stays private.
3. **Database backend:** DuckDB now, ClickHouse if intraday/multi-source volume grows.
4. **Real-time layer:** websocket streams (Alpaca/OKX/Bybit) for intraday marks and faster monitoring — optional until strategies need sub-daily.
5. **Secrets management:** a real vault/`.env` discipline; the invalid CMC key and broken `.venv/bin/pip` (3.13 vs 3.11 mismatch) are immediate fixes.
6. **The capital path:** paper (now) → longer paper track record (months) → tiny real capital on Alpaca once a sleeve shows sustained, reconciled, net-of-fees out-performance and passes the promotion checklist → scale. Never skip a rung.

---

## 7. Defect / fix list (concrete, prioritized)

1. **Invalid `CMC_API_KEY` (401)** — blocks 3 books + T033. *You.*
2. **`.venv/bin/pip` broken** (targets py3.13, interpreter is 3.11) — use `python -m pip`; fix the venv.
3. **On-chain data 61 days stale + sparse** — refresh CoinMetrics/DeFiLlama, widen coverage.
4. **Carry sleeve = spot-leg proxy** — add the short-perp leg + funding accounting for true delta-neutrality.
5. **No short-selling in the paper engine** — add short/margin accounting (virattt's ledger is a portable template) to unlock market-neutral strategies.
6. **Flat 20 bps cost model** — add size/liquidity-aware slippage (ADV participation → impact).
7. **Risk pipeline & allocator not wired into execution** — integrate behind the approval gate.
8. **Sleeve/candidate books have ~2 days history** — needs the scheduler running continuously.
9. **Single venue (Alpaca) + Binance geo-block (451)** — add OKX/Bybit/Kraken via ccxt for cross-venue.
10. **Monitoring alerts not delivered** — add a notifier.
11. **~131 files uncommitted** — commit in logical chunks.

---

## 8. Prioritized roadmap (the order I'd do it in)

**Phase A — Make it run continuously & honestly (days):** fix CMC key + venv, refresh on-chain, commit everything, put the scheduler under supervision with real alerting, let paper books start accruing history. *Outcome: a live, monitored, self-reconciling paper fund.*

**Phase B — Data breadth (1–2 weeks):** add derivatives (OKX/Bybit funding+OI+liquidations), expand DeFiLlama (yields/TVL/fundamentals), add FRED macro. Each as a keyless collector. *Outcome: the orthogonal data real edges come from.*

**Phase C — Strategy depth (2–4 weeks):** true delta-neutral basis/carry (the priority), DeFi-yield sleeve, stat-arb pairs, macro overlay. Add short-side accounting to the engine first. *Outcome: a genuine multi-strategy book with market-neutral sleeves.*

**Phase D — Model/label sophistication (2–4 weeks):** triple-barrier + meta-labeling, model stacking (XGBoost/CatBoost), regime-conditional models, confidence-based sizing. Re-run research; let BacktestAgent judge. *Outcome: the best honest shot at a verified edge.*

**Phase E — Execution realism & the capital path (ongoing):** liquidity-aware costs, TCA, cross-venue, then the paper→tiny-real progression gated on sustained reconciled out-performance.

**The one thing that matters most:** an edge that survives BacktestAgent. Everything else is infrastructure to find, run, and protect it. CHF is now a very good machine for that hunt — Phases B, C, D are the hunt itself.
