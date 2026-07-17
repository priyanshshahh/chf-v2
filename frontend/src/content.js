// Curated narrative copy for the CHF site.
// NOTE: No performance numbers live here — every metric the site shows is loaded
// from real pipeline output baked into /public/data/*.json. This file is prose only.

export const site = {
  name: "CHF",
  wordmark: "CHF",
  tagline: "Crypto Alpha Research OS",
  hero:
    "A reproducible, leakage-safe research platform that asks one honest question of crypto markets — is there tradable cross-sectional alpha after costs and benchmarks? — and reports the answer without flinching.",
  subhero:
    "A ~76-module system: nine research agents, an advanced ML layer, seven strategy sleeves, a short-side-capable paper-trading engine across 13 virtual books, dual-book accounting that reconciles at 0 bps, and a 10-module monitoring suite. The verdict is enforced by code, not opinion.",
  repo: "chf-v2",
};

// Human-friendly labels for the raw strategy keys in the baked JSON.
export const strategyLabels = {
  score_weighted_long_only: "Score-weighted (long only)",
  score_weighted_vol_scaled: "Score-weighted (vol-scaled)",
  top_5_equal_weight: "Top 5 · equal weight",
  top_5_vol_scaled: "Top 5 · vol-scaled",
  top_10_equal_weight: "Top 10 · equal weight",
  top_10_vol_scaled: "Top 10 · vol-scaled",
  top_20_equal_weight: "Top 20 · equal weight",
  top_20_vol_scaled: "Top 20 · vol-scaled",
  turnover_controlled: "Turnover-controlled",
  equal_weight_universe: "Equal-weight universe (benchmark)",
  // Paper-trading book names (virtual research books, not live holdings).
  canonical_best_model: "Canonical best model",
  ridge_30d_top5: "Ridge 30d · top 5",
  lightgbm_14d_top20_vs: "LightGBM 14d · top 20 vol-scaled",
  rf_14d_top20_vs: "Random forest 14d · top 20 vol-scaled",
  benchmark_btc: "Benchmark · BTC hold",
  benchmark_btc_eth_5050: "Benchmark · BTC/ETH 50-50",
};

export const pipeline = [
  {
    id: 1,
    name: "Universe",
    summary: "Builds the eligible asset universe with liquidity and listing filters.",
    detail: "Point-in-time-aware membership, exclusions, and coverage reporting.",
  },
  {
    id: 2,
    name: "Market data",
    summary: "Ingests OHLCV across providers with automatic failover.",
    detail: "CoinGecko, Binance/CCXT and others over a shared HTTP client.",
  },
  {
    id: 3,
    name: "On-chain",
    summary: "Adds on-chain metrics where coverage exists.",
    detail: "NVT, MVRV-proxy and TVL ratios joined to the market panel.",
  },
  {
    id: 4,
    name: "Features",
    summary: "Two-tier, leakage-aware feature store.",
    detail: "Market features from OHLCV; on-chain features layered on top.",
  },
  {
    id: 5,
    name: "Labels",
    summary: "Exact forward calendar returns.",
    detail: "No look-ahead — labels are constructed strictly forward in time.",
  },
  {
    id: 6,
    name: "Models",
    summary: "Purged, embargoed walk-forward cross-validation.",
    detail: "Rank IC is the primary signal-quality metric across folds.",
  },
  {
    id: 7,
    name: "Portfolio",
    summary: "Deterministic long-only construction from predictions only.",
    detail: "Liquidity and positive-signal filters; realized returns never used as input.",
  },
  {
    id: 8,
    name: "Backtest",
    summary: "The single source of truth on alpha.",
    detail: "Costs, benchmark sanity checks, and the final verified / rejected verdict.",
  },
];

export const integrity = [
  {
    title: "Leakage-safe by construction",
    body:
      "Labels are exact forward returns. Dedicated leakage guards run across the feature, label, model, portfolio and backtest stages.",
  },
  {
    title: "Prediction-only portfolios",
    body:
      "Allocations are built from as-of predictions with liquidity and positive-signal filters. Realized returns and labels are rejected as inputs.",
  },
  {
    title: "One alpha authority",
    body:
      "Only the BacktestAgent can verify or reject alpha — after transaction costs and benchmark sanity checks. Nothing else may claim it.",
  },
  {
    title: "Benchmark discipline",
    body:
      "Every strategy is measured against BTC, ETH, a 50/50 blend and the equal-weight universe, with a transaction-cost convention applied throughout.",
  },
];

export const painPoints = [
  {
    pain: "Crypto data is fragmented across APIs",
    solution: "Agentic ingestion standardizes universe, market and on-chain inputs behind one contract.",
  },
  {
    pain: "Look-ahead bias is trivially easy to introduce",
    solution: "Feature, label and model stages enforce leakage-aware contracts and purged walk-forward CV.",
  },
  {
    pain: "Backtests quietly ignore transaction costs",
    solution: "The backtest applies a cost convention and sweeps 0–100 bps to test fragility.",
  },
  {
    pain: "Weak benchmarks manufacture fake alpha",
    solution: "Results are judged against four disciplined baselines before any alpha claim is allowed.",
  },
];

export const limitations = [
  "All trading is virtual. The 13 books are paper — notional NAV, simulated fills and fees. Nothing here is a live track record, and the system never claims one.",
  "On-chain coverage is sparse relative to market coverage, so on-chain features add little marginal Rank IC in the current study.",
  "The advanced ML models (XGBoost, CatBoost, stacking) do not turn the non-verified signal into a verified one — more model capacity did not change the conclusion.",
  "The out-of-sample window (2025-03 → 2026-05) is a single, predominantly bear regime — exactly where cross-sectional momentum is known to underperform. Bull and recovery regimes sit in the training split, untested out-of-sample.",
  "The book is long-only, so a purely cross-sectional (market-neutral) ranking edge cannot be expressed — beta is forced to ~1 into a -46% market. The null is about this constraint set, not proof the signal is worthless.",
  "Research and education only — there is no real-money execution engine wired in by default.",
];

// Seven deterministic strategy sleeves, each with its own virtual paper book.
export const sleeves = [
  { name: "Trend", book: "sleeve_trend", kind: "Directional",
    body: "Time-series momentum / trend-following on the liquid majors. Long-biased, regime-aware." },
  { name: "Cross-sectional momentum", book: "sleeve_xsmom", kind: "Long-short",
    body: "Ranks the universe on relative strength and trades the cross-section — the classic academic factor." },
  { name: "Carry", book: "sleeve_carry", kind: "Directional",
    body: "Harvests funding / basis carry where it is positive, sized by conviction." },
  { name: "Technical ensemble", book: "sleeve_technical", kind: "Directional",
    body: "A voted ensemble of deterministic technical rules; no single indicator can dominate." },
  { name: "Delta-neutral carry", book: "sleeve_carry_neutral", kind: "Market-neutral",
    body: "True carry with the directional beta hedged out — targets funding/basis return independent of price." },
  { name: "Stat-arb (pairs)", book: "sleeve_statarb", kind: "Market-neutral",
    body: "Mean-reversion on cointegrated pairs; factor-hedged so it need not rely on market beta to win." },
  { name: "DeFi yield", book: "sleeve_defi_yield", kind: "Yield",
    body: "Allocates to on-chain yield opportunities from DeFiLlama coverage, sized and risk-gated." },
];

// System capability layers — what the platform does beyond the research pipeline.
export const capabilities = [
  { icon: "BrainCircuit", title: "Advanced ML layer",
    body: "XGBoost, CatBoost and a leakage-safe walk-forward stacking ensemble, plus triple-barrier labels and meta-labeling — all run head-to-head against the baselines, with no authority to claim alpha." },
  { icon: "FlaskConical", title: "Paper-trading engine",
    body: "A short-side-capable simulator broker advances 13 virtual books daily — canonical, candidate, benchmark and seven strategy sleeves — as ongoing out-of-sample validation." },
  { icon: "Scale", title: "Dual-book accounting",
    body: "An append-only ledger and an independent NAV recomputation reconcile to ~1e-12 bps against a 1.0 bps tolerance, with gross/net-of-fees NAV, a high-water-mark fee schedule and a hash-chained audit log." },
  { icon: "ShieldCheck", title: "Monitoring & ops",
    body: "Ten monitors — data quality, signal health, execution quality, model decay, risk, shadow-NAV, watchdog, champion/challenger and a notifier — plus an orchestration layer with a human approval queue." },
  { icon: "Network", title: "Data breadth",
    body: "21 provider adapters, most keyless-capable: OHLCV, on-chain, derivatives (OKX / Bybit / Coinglass funding & open interest), DeFiLlama TVL & yields, and FRED macro." },
  { icon: "LineChart", title: "Institutional reporting",
    body: "A deterministic monthly investor letter, a research dashboard, a product dashboard and a FastAPI service — the reporting surface a real desk expects." },
];

// Provider adapters grouped by data domain (providers/*.py).
export const dataProviders = [
  { group: "Market / OHLCV", items: ["CoinGecko", "Binance (CCXT)", "CoinCap", "CoinPaprika", "CryptoCompare", "CoinMarketCap"] },
  { group: "On-chain", items: ["CoinMetrics", "Etherscan", "Blockchair", "The Graph", "Dune"] },
  { group: "Derivatives", items: ["OKX funding/OI", "Bybit funding/OI", "Coinglass", "Funding rates"] },
  { group: "DeFi & macro", items: ["DeFiLlama TVL/yields", "FRED macro"] },
];

// Grand module tally (see docs/CASE_STUDY.md for the breakdown).
export const moduleTally = [
  { label: "Research agents", n: 11, sub: "9 stages + universe variants" },
  { label: "Strategy sleeves", n: 11, sub: "7 concrete sleeve classes" },
  { label: "Data providers", n: 21, sub: "5 new keyless collectors" },
  { label: "Accounting", n: 5, sub: "ledger · NAV · fees · reconcile · audit" },
  { label: "Monitoring", n: 10, sub: "incl. notifier + shadow-NAV" },
  { label: "Paper trading", n: 6, sub: "13 virtual books" },
  { label: "Orchestration", n: 8, sub: "plan → risk → execute → report" },
  { label: "Features (labeling)", n: 2, sub: "triple-barrier + frac-diff" },
];

export const stack = [
  "Python",
  "pandas",
  "LightGBM",
  "scikit-learn",
  "vectorbt",
  "FastAPI",
  "React",
  "Vite",
  "Recharts",
];

// Per-agent page metadata. `icon` is a lucide-react export name (resolved in
// the UI). `data` is the baked JSON file the page reads from /public/data/.
export const agents = [
  {
    slug: "universe",
    n: "01",
    name: "Universe",
    icon: "Globe",
    tagline: "Point-in-time eligible asset set",
    data: "universe",
    intro:
      "Each month the universe agent reconstructs the real top-N crypto assets as they stood at that time — including coins later delisted — then filters to a clean, tradable, survivorship-bias-free set.",
    bullets: [
      "Monthly point-in-time membership from historical market-cap ranks",
      "Removes stablecoins, wrapped/bridged tokens, LSTs and immature listings",
      "Requires exchange tradability and on-chain data availability",
    ],
  },
  {
    slug: "market",
    n: "02",
    name: "Market data",
    icon: "CandlestickChart",
    tagline: "Daily OHLCV across providers",
    data: "market",
    intro:
      "The market agent ingests daily open/high/low/close/volume for every universe member, with multi-provider failover and quality flags for gaps, stale prices and anomalies.",
    bullets: [
      "Multi-provider ingestion with automatic failover",
      "Quality flags: forward-fill, stale price, anomaly, long-gap",
      "Dollar-volume and market-cap normalised per asset",
    ],
  },
  {
    slug: "onchain",
    n: "03",
    name: "On-chain",
    icon: "Network",
    tagline: "Does blockchain data add edge?",
    data: "onchain",
    intro:
      "The on-chain agent joins network metrics — active addresses, transactions, MVRV, TVL, fees, DEX volume — to the panel, then we ablate to measure whether they improve prediction at all.",
    bullets: [
      "NVT, MVRV-proxy, TVL, fees and DEX-volume features",
      "Coverage is sparse relative to market data",
      "Ablation measures the marginal Rank IC lift directly",
    ],
  },
  {
    slug: "features",
    n: "04",
    name: "Features",
    icon: "Layers",
    tagline: "Leakage-aware feature store",
    data: "features",
    intro:
      "The feature agent builds a two-tier, leakage-aware feature store — market features from OHLCV plus on-chain features — with cross-sectional z-scoring and per-feature QA.",
    bullets: [
      "Returns, momentum, volatility, liquidity and drawdown families",
      "Cross-sectional z-scores for rank-based modelling",
      "Every feature passes a coverage / finiteness QA gate",
    ],
  },
  {
    slug: "labels",
    n: "05",
    name: "Labels",
    icon: "Tag",
    tagline: "Exact forward returns, no look-ahead",
    data: "labels",
    intro:
      "Labels are exact forward calendar returns over 7-, 14- and 30-day horizons. They are constructed strictly forward in time, so no future information can leak into training.",
    bullets: [
      "Forward log-return, simple return, direction and rank buckets",
      "Three horizons: 7d, 14d, 30d",
      "Incomplete / non-exact-horizon rows are dropped, not filled",
    ],
  },
  {
    slug: "models",
    n: "06",
    name: "Models",
    icon: "BrainCircuit",
    tagline: "Purged walk-forward, scored by Rank IC",
    data: "models",
    intro:
      "The model agent runs dozens of experiments — linear, tree-based and rule models across feature sets and horizons — under purged, embargoed walk-forward CV, ranked by Rank IC.",
    bullets: [
      "Purged + embargoed walk-forward cross-validation",
      "Rank IC and its t-statistic are the primary metrics",
      "A strict signal gate decides what may reach the backtest",
    ],
  },
  {
    slug: "portfolio",
    n: "07",
    name: "Portfolio",
    icon: "Wallet",
    tagline: "Prediction-only long-only allocation",
    data: "portfolio",
    intro:
      "The portfolio agent turns as-of predictions into deterministic long-only weights with liquidity and positive-signal filters. Realized returns are never used as an input.",
    bullets: [
      "Score- and rank-based weighting schemes",
      "Liquidity and positive-signal filters applied",
      "Diagnostic mode — not live trading instructions",
    ],
  },
  {
    slug: "backtest",
    n: "08",
    name: "Backtest",
    icon: "LineChart",
    tagline: "The single source of truth on alpha",
    data: "backtest",
    intro:
      "The backtest agent is the only authority that may verify or reject alpha. It applies transaction costs, benchmark sanity checks and sub-period analysis before issuing a verdict.",
    bullets: [
      "Net-of-cost equity, drawdown and turnover",
      "Sub-period and cost-sweep robustness checks",
      "Verdict for this study: alpha_verified = false",
    ],
  },
];
