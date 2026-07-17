# How Real Crypto Hedge Funds Operate — Research Synthesis → CHF Implementation

**Date:** 2026-07-05. Compiled from full reads of: mergersandinquisitions.com, cryptoresearch.report, godex.io, cryptoinsightsgroup.com (2025 industry guide), cv5capital (Medium), ai-street.co, Yahoo Finance (Magnetar), finantrix (25 use cases), stoic.ai/Cindicator, Binance Square posts, plus public profiles/interviews for 20 named funds and three open-source repos (51bitquant/ai-hedge-fund-crypto, virattt/ai-hedge-fund, Kshitij0O7/ai-crypto-hedge-fund-manager) and Alpaca's crypto API docs.

## The central finding

**The institutional/retail dividing line is not alpha — it is documented, independently verifiable process.** The 2025 industry guide's institutional checklist: audited financials, segregated/MPC custody, independent fund administration (monthly NAV by a third party), a documented valuation policy, and documented risk controls **with defined breach responses**. Even the most AI-forward funds (Man Group AlphaGPT, Bridgewater AIA, Magnetar) keep deterministic risk/execution/validation layers with human final authority — which is exactly CHF's approval-gate + single-alpha-authority philosophy. No LLM trading agents are used for decisions at any fund studied; LLMs appear only in research tooling behind mandatory human review.

## What the funds actually run (per-fund highlights)

- **Nickel Digital** (quant, London): pod-shop — 74+ external quant pods paid on **Sortino**; per-exchange caps, tri-party custody, whole-book-to-cash willingness; markets itself on the "vol cut" (77% BTC vol → 1.4–3.4% fund vol).
- **Pythagoras** (arb, since 2014): strictly market-neutral funding/cross-exchange arb; **≤10% of assets per exchange across 12+ venues** (contained FTX to ~3%); leads reporting with Sharpe 2 / Sortino 4 / Calmar 2.
- **Kbit** (quant): crypto-native cash-and-carry at scale; engineering-first; publishes nothing.
- **ANB Investments** (quant CTA): deliberately parsimonious — **BTC/ETH/SOL only, models distilled to 4 parameters** to fight overfitting; ~50% avg net exposure; delta-neutral book enters basis when 15–20% annualized; sizes inverse to vol and market cap; <1% max DD on the neutral book.
- **Active Digital**: five sleeves (funding basis, coin-margined basis, cross-exchange funding, vol arb, multi-strat), per-sleeve Sharpe reporting; 0/30 fee model.
- **Brevan Howard Digital**: 10+ PM pods under institutional risk with 24/7 monitoring; the 2025 -29.5% episode (correlations spiked, hedges failed) is the cautionary tale for regime-blind books.
- **Pantera / Galaxy / Apollo / Block AM**: sleeve/product architectures with hard construction bands (Block AM: ≤30% single position, sleeve bands, no leverage); Galaxy charges performance **only on alpha above BTC**.
- **Multicoin / Polychain**: conviction concentration (30–50% single positions) with venture horizons — the -91.4% (2022) counterexample to drawdown governance.
- **Lemvi**: same-venue-both-legs rule, ≤30% per counterparty, options book always long gamma; serially retires decayed edges.
- **Solidum**: BTC-relative momentum with a **rotate-to-stablecoins gate** when both asset and BTC trends are negative.
- **Wave**: covered-call overwriting (1-month ~20% OTM, ~1%/month distributions); Arca: regulation-first, weekly disciplined letter format; 3iQ: regulated wrappers + manager screening; Laser Digital: perp-funding carry + staking, targets excess-over-BTC across regimes.

## Convergent practices → what CHF now implements (all deterministic Python)

1. **Dual-book accounting** — administrator-style independent NAV rebuilt from an immutable ledger, reconciled daily against the trading engine; tamper-evident chained audit log; 2/20 fee accrual with high-water mark so results are net-of-fees honest. → `accounting/`
2. **Risk limits with defined breach responses** — per-asset caps (~10–20% norm), concentration/category caps, gross exposure caps; volatility targeting (EWMA cov → exposure multiplier, the "vol cut"); drawdown state machine (soft -10% halve / hard -20% flat / deterministic re-entry); ADV-based liquidity caps + days-to-liquidate stress table. → `portfolio/risk_*.py`
3. **Multi-strategy sleeve architecture** — independent sleeves (parsimonious trend, BTC-relative x-sectional momentum with stablecoin gate, funding-rate carry, technical ensemble), each a separate paper book; monthly Sortino-keyed capital allocator with floors/caps; regime multipliers (breadth, correlation-spike flag, BTC dominance, fear/greed). Promotion of capital is a human-gated proposal, never automatic. → `strategies/`
4. **Operational monitoring** — pre-flight data-quality gate, rolling-IC signal health, CUSUM model-decay detection (live vs backtest), daily risk dashboard with risk contributions, execution-quality/turnover-creep alerts, shadow NAV, champion–challenger book scoring, scheduler SLA watchdog + heartbeat. → `monitoring/` + hardened `jobs/scheduler.py`
5. **Institutional reporting** — monthly letter: NAV table, returns vs BTC and equal-weight index, Sharpe/Sortino/Calmar (√365 crypto convention), max DD, % positive months, alpha/beta vs BTC, downside capture, regime-sliced track record, attribution (beta vs residual vs costs), net-of-fees. → `reports/`

## Portable mechanisms adopted from open-source repos (non-LLM math only)

- virattt technical ensemble (5 families: EMA-trend/ADX, z-score+Bollinger mean-reversion, multi-window momentum with volume confirm, vol-regime, Hurst+skew stat-arb; weights .25/.25/.20/.15/.15) — implemented crypto-tuned (√365) in `strategies/technical_ensemble.py`.
- virattt risk-manager sizing (piecewise vol-based position limit multipliers × correlation-vs-book multipliers) — informs `portfolio/vol_target.py` risk-contribution caps.
- 51bitquant conventions: √365 annualization, rf/365 daily, equal-length data-integrity gate before backtests, multi-timeframe keying.
- Kshitij regime spec: asymmetric stops (tight for mean-reversion, wide for trend) and hard invariants enforced in code, not prompts.

## Alpaca reality check (paper execution)

Crypto supports **market and limit orders only** (no stop/bracket/OCO; TIF gtc/ioc), long-only, fractional/notional, 24/7; fees 15/25 bps maker/taker at tier 1 (worth modeling); free keyless historical crypto data + websocket streams; `GET /v2/account/portfolio/history` (with `intraday_reporting=continuous`) gives an independent equity series to reconcile against CHF's ledger. Stops must be synthetic (monitor + market close), which CHF's deterministic rule engine handles.

## Explicitly out of scope (documented, not built)

Legal fund structuring (Cayman/BVI), AML/KYC, real custody (MPC/cold storage), third-party administrators/auditors, redemption terms/gates (meaningless without external LPs), HFT/market making (needs tick data), smart-contract due diligence. These are production-readiness notes for if/when CHF manages external capital.
