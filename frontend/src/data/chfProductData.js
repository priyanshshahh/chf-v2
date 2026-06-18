import {
  Activity,
  Archive,
  BarChart3,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  ClipboardCheck,
  Cloud,
  Code2,
  Database,
  FileCheck2,
  GitBranch,
  KeyRound,
  Layers3,
  LineChart,
  Lock,
  Network,
  PackageCheck,
  Radar,
  Rocket,
  Scale,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TerminalSquare,
  Workflow,
  Zap
} from "lucide-react";

export const release = {
  tag: "v1.0-research-release",
  title: "CHF Alpha Research OS",
  subtitle:
    "Agentic crypto quant research infrastructure for signal discovery, portfolio construction, and benchmark-aware validation.",
  window: "2022-12-15 → 2026-03-24",
  tests: "54 targeted tests passed",
  benchmarkSet: "BTC, ETH, BTC/ETH 50-50, Equal-weight universe",
  researchMode: "Purged walk-forward validation",
  costConvention: "20 bps benchmark cost convention",
  result: "No verified alpha found under tested configurations."
};

export const heroBullets = [
  "Automates crypto research from universe construction to backtest verification",
  "Combines market data, on-chain signals, ML models, and portfolio rules",
  "Forces benchmark discipline with transaction-cost-aware evaluation",
  "Packages results into reproducible artifacts for reviewers, researchers, and future product users"
];

export const productFacts = [
  { label: "Agent pipeline", value: "8 agents", detail: "Universe to Backtest", icon: Workflow },
  { label: "Validation suite", value: "54 tests", detail: "Targeted checks passed", icon: CheckCircle2 },
  { label: "Release tag", value: "v1.0", detail: "Frozen research state", icon: GitBranch },
  { label: "Benchmark window", value: "2022–2026", detail: "Exact candidate window", icon: CalendarClock },
  { label: "Benchmark set", value: "4 baselines", detail: "BTC, ETH, blend, universe", icon: Scale },
  { label: "Research mode", value: "Walk-forward", detail: "Leakage-aware validation", icon: Radar }
];

export const painPoints = [
  {
    pain: "Crypto data is fragmented across APIs",
    solution: "Agentic ingestion standardizes universe, market, and on-chain inputs.",
    icon: Database
  },
  {
    pain: "Look-ahead bias is easy to introduce",
    solution: "Feature, label, and model stages enforce leakage-aware contracts.",
    icon: ShieldCheck
  },
  {
    pain: "Backtests often ignore transaction costs",
    solution: "BacktestAgent applies transaction-cost-aware evaluation and cost sweeps.",
    icon: SlidersHorizontal
  },
  {
    pain: "Weak benchmarks create weak alpha claims",
    solution: "CHF compares against BTC, ETH, BTC/ETH 50-50, and equal-weight universe.",
    icon: BarChart3
  },
  {
    pain: "ML forecasts do not automatically become portfolio alpha",
    solution: "PortfolioAgent turns prediction-safe files into deterministic allocations.",
    icon: BrainCircuit
  },
  {
    pain: "Results are hard to reproduce",
    solution: "Release audits, manifests, checklists, and reviewer packets preserve evidence.",
    icon: FileCheck2
  }
];

export const consoleCards = [
  { title: "Research Pipeline", status: "Active MVP module", detail: "Eight-agent orchestration", icon: Workflow },
  { title: "Scheduler", status: "Local automation ready", detail: "Daily/weekly/monthly jobs", icon: CalendarClock },
  { title: "Benchmark Engine", status: "Verified", detail: "Window-aligned baselines", icon: Scale },
  { title: "Reproducibility Pack", status: "Complete", detail: "Reviewer docs and release audit", icon: PackageCheck },
  { title: "Product UI", status: "React MVP", detail: "Fintech-style presentation layer", icon: Rocket },
  { title: "Research UI", status: "Streamlit control dashboard", detail: "Local pipeline controls", icon: TerminalSquare }
];

export const pipelineTimeline = [
  { label: "Universe", status: "MVP", purpose: "Define assets", output: "universe manifest", value: "Repeatable research surface" },
  { label: "Market Data", status: "MVP", purpose: "Collect OHLCV", output: "market_ohlcv parquet", value: "Price/liquidity foundation" },
  { label: "On-Chain", status: "MVP", purpose: "Collect network metrics", output: "onchain observations", value: "Crypto-native context" },
  { label: "Features", status: "MVP", purpose: "Engineer signals", output: "feature store", value: "Model-ready inputs" },
  { label: "Labels", status: "MVP", purpose: "Create targets", output: "label matrix", value: "Exact outcome definitions" },
  { label: "Models", status: "MVP", purpose: "Forecast returns", output: "predictions", value: "Signal discovery" },
  { label: "Portfolio", status: "MVP", purpose: "Allocate weights", output: "allocation files", value: "Portfolio intelligence" },
  { label: "Backtest", status: "MVP", purpose: "Verify alpha", output: "alpha report", value: "Benchmark discipline" },
  { label: "Reports", status: "Complete", purpose: "Package evidence", output: "reviewer docs", value: "Reproducibility" }
];

export const agents = [
  {
    name: "UniverseAgent",
    category: "Data",
    what: "Builds the investable crypto universe.",
    why: "Prevents arbitrary asset selection and supports repeatable experiments.",
    input: "Market-cap, liquidity, exclusion, and provider metadata.",
    output: "Eligible asset universe and manifest.",
    artifact: "data/raw/universe/universe_monthly.parquet",
    value: "Defines what the model is allowed to research.",
    icon: Network
  },
  {
    name: "MarketDataAgent",
    category: "Data",
    what: "Ingests and validates daily OHLCV data.",
    why: "Ensures strategy results are grounded in real tradable market histories.",
    input: "Eligible assets and market providers.",
    output: "Canonical market_ohlcv dataset and coverage report.",
    artifact: "data/raw/market/market_ohlcv.parquet",
    value: "Creates the price and liquidity foundation.",
    icon: Database
  },
  {
    name: "OnChainAgent",
    category: "Data",
    what: "Collects CoinMetrics, DeFiLlama, and optional provider metrics.",
    why: "Adds crypto-native network and protocol context beyond price data.",
    input: "Universe symbols and provider mappings.",
    output: "On-chain observations and wide metrics.",
    artifact: "data/raw/onchain/onchain_observations.parquet",
    value: "Turns sparse on-chain coverage into auditable research inputs.",
    icon: Activity
  },
  {
    name: "FeatureAgent",
    category: "Signal",
    what: "Builds leakage-safe market and on-chain features.",
    why: "Transforms raw data into standardized model-ready signal candidates.",
    input: "Market and on-chain canonical outputs.",
    output: "Full and pruned feature stores.",
    artifact: "data/features/full_features_pruned.parquet",
    value: "Creates the signal factory for research experiments.",
    icon: Layers3
  },
  {
    name: "LabelAgent",
    category: "Signal",
    what: "Creates exact forward calendar labels.",
    why: "Keeps target definitions explicit and avoids row-shift pseudo-horizons.",
    input: "Market closes and feature dates.",
    output: "Label matrix and modeling dataset.",
    artifact: "data/labels/label_matrix.parquet",
    value: "Defines exactly what models are trying to predict.",
    icon: ClipboardCheck
  },
  {
    name: "ModelAgent",
    category: "ML",
    what: "Runs purged walk-forward signal screening.",
    why: "Tests whether features forecast cross-sectional returns out of sample.",
    input: "Modeling dataset and feature groups.",
    output: "Prediction files, fold metrics, and signal leaderboard.",
    artifact: "data/predictions/model_leaderboard.parquet",
    value: "Separates promising signals from diagnostic baselines.",
    icon: BrainCircuit
  },
  {
    name: "AlphaResearchAgent",
    category: "ML",
    what: "Runs a signal-only research grid across models, feature sets, and label targets.",
    why: "Explores many hypotheses under leakage-safe walk-forward CV without ever claiming alpha.",
    input: "Feature store and label matrix.",
    output: "Research leaderboard, Rank IC metrics, and a signal-only manifest.",
    artifact: "data/research/research_leaderboard.parquet",
    value: "Wide signal discovery that defers every alpha decision to the BacktestAgent.",
    icon: Radar
  },
  {
    name: "PortfolioAgent",
    category: "Portfolio",
    what: "Converts prediction-safe files into deterministic allocations.",
    why: "Forces ML forecasts through position caps, risk scaling, execution lag, and turnover controls.",
    input: "Prediction-only files and market data.",
    output: "Allocation files, manifest, and QA report.",
    artifact: "data/allocations/allocations_from_predictions.parquet",
    value: "Turns signals into testable portfolio decisions.",
    icon: SlidersHorizontal
  },
  {
    name: "BacktestAgent",
    category: "Validation",
    what: "Verifies or rejects alpha after costs and benchmarks.",
    why: "Prevents attractive signal metrics from becoming unsupported performance claims.",
    input: "Allocations and market returns.",
    output: "Equity curves, benchmark comparison, and alpha report.",
    artifact: "data/backtests/alpha_report.json",
    value: "Final alpha authority.",
    icon: ShieldCheck
  }
];

export const benchmarks = [
  { name: "BTC", returnPct: 305.5, description: "Strongest passive benchmark in the tested window", color: "#f5b84b" },
  { name: "ETH", returnPct: 69.85, description: "Major smart-contract asset baseline", color: "#60a5fa" },
  { name: "BTC/ETH 50-50", returnPct: 178.04, description: "Blended crypto beta baseline", color: "#a78bfa" },
  { name: "Equal-weight universe", returnPct: 30.39, description: "Broad universe baseline", color: "#34d399" }
];

export const validationProof = [
  { label: "Python compile", status: "PASS", icon: Code2 },
  { label: "Targeted tests", status: "54 passed", icon: CheckCircle2 },
  { label: "Markdown/link sanity", status: "PASS", icon: FileCheck2 },
  { label: "Release audit", status: "Complete", icon: Archive },
  { label: "Reviewer packet", status: "Complete", icon: PackageCheck }
];

export const researchArtifacts = [
  { name: "Benchmark verification report", path: "docs/BENCHMARK_VERIFICATION.md", status: "Complete" },
  { name: "Final reviewer packet", path: "docs/FINAL_REVIEWER_PACKET.md", status: "Complete" },
  { name: "Research results summary", path: "docs/RESEARCH_RESULTS_SUMMARY.md", status: "Complete" },
  { name: "Alpha backtest verification report", path: "docs/ALPHA_BACKTEST_VERIFICATION_REPORT.md", status: "Complete" },
  { name: "Reproducibility checklist", path: "docs/REPRODUCIBILITY_CHECKLIST.md", status: "Complete" },
  { name: "Artifact manifest", path: "docs/ARTIFACT_MANIFEST.md", status: "Complete" },
  { name: "Final release audit", path: "docs/FINAL_RELEASE_AUDIT.md", status: "Complete" }
];

export const portfolioModules = [
  { title: "Signal ranking engine", detail: "Rank assets cross-sectionally from model or rule forecasts.", icon: Sparkles },
  { title: "Top-K constructor", detail: "Convert ranked signals into deterministic research portfolios.", icon: SlidersHorizontal },
  { title: "Risk-aware allocation layer", detail: "Volatility scaling, max-weight caps, and residual cash handling.", icon: ShieldCheck },
  { title: "Turnover and cost monitor", detail: "Track trading intensity and transaction-cost drag.", icon: LineChart },
  { title: "Benchmark comparison engine", detail: "Compare every strategy against disciplined crypto baselines.", icon: Scale },
  { title: "Strategy leaderboard", detail: "Promote candidates only after BacktestAgent verification.", icon: BarChart3 },
  { title: "Experiment registry", detail: "Roadmap module for tracking experiments and artifacts.", icon: ClipboardCheck },
  { title: "Live allocation monitor", detail: "Future module for monitoring, not current live trading.", icon: Radar }
];

export const trustBadges = [
  { label: "Fixed release tag", detail: "v1.0-research-release", icon: GitBranch },
  { label: "Validation passed", detail: "54 targeted tests", icon: CheckCircle2 },
  { label: "Benchmark verified", detail: "Exact window + cost convention", icon: Scale },
  { label: "Docs complete", detail: "Reviewer packet and audit docs", icon: FileCheck2 },
  { label: "No hidden recomputation", detail: "Frozen release references", icon: Lock }
];

export const roadmapColumns = [
  {
    title: "MVP Now",
    items: [
      "React product dashboard",
      "Streamlit research dashboard",
      "Scheduler",
      "Agent pipeline",
      "Benchmark verification",
      "Reproducibility package"
    ]
  },
  {
    title: "Next",
    items: [
      "FastAPI backend for dashboard",
      "Authenticated command execution",
      "Experiment registry",
      "Strategy comparison engine",
      "Artifact viewer",
      "Richer report ingestion"
    ]
  },
  {
    title: "Future",
    items: [
      "Cloud deployment",
      "Live data refresh",
      "Model monitoring",
      "Paper/report generator",
      "Signal subscription product",
      "Institutional research portal"
    ]
  }
];

export const demoAudiences = [
  {
    audience: "Professor / reviewer",
    cares: "Research rigor, reproducibility, honest limitations",
    show: "Reproducibility / Trust and Results / Verified Outputs"
  },
  {
    audience: "Quant recruiter",
    cares: "Pipeline design, validation discipline, modeling workflow",
    show: "Agent Pipeline and Benchmark Intelligence"
  },
  {
    audience: "Startup / product viewer",
    cares: "Product story, MVP clarity, roadmap",
    show: "Landing, Product Console, and Product Roadmap"
  },
  {
    audience: "Future user",
    cares: "What they can inspect, automate, and compare",
    show: "Portfolio Intelligence and Project Access"
  }
];

export const accessModules = [
  { name: "Research Dashboard", path: "app/dashboard.py", detail: "Streamlit control dashboard for local research operations." },
  { name: "React Product MVP", path: "frontend/", detail: "Fintech-style product dashboard for demos and product storytelling." },
  { name: "Docs Package", path: "docs/", detail: "Reviewer, benchmark, reproducibility, and limitation reports." },
  { name: "Scheduler", path: "jobs/scheduler.py", detail: "Local APScheduler wrapper around supported CLI commands." },
  { name: "Agent Pipeline", path: "agents/", detail: "Deterministic research pipeline modules." },
  { name: "Tests", path: "tests/", detail: "Research-integrity and regression tests." }
];
