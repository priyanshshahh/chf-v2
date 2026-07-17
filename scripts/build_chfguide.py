"""Generate chfguide.docx — a complete, beginner-friendly guide to the CHF project.

Run: python3 scripts/build_chfguide.py
Produces: chfguide.docx in the project root.
"""
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

doc = Document()

# ----- base styling -----
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)

ACCENT = RGBColor(0x1F, 0x3A, 0x5F)
GOOD = RGBColor(0x1B, 0x7A, 0x3D)
BAD = RGBColor(0xB0, 0x2A, 0x2A)


def h1(text):
    p = doc.add_heading(text, level=1)
    for r in p.runs:
        r.font.color.rgb = ACCENT
    return p


def h2(text):
    return doc.add_heading(text, level=2)


def h3(text):
    return doc.add_heading(text, level=3)


def para(text="", bold=False, italic=False, color=None, size=None):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = bold
    r.italic = italic
    if color is not None:
        r.font.color.rgb = color
    if size is not None:
        r.font.size = Pt(size)
    return p


def bullet(text, bold_lead=None):
    p = doc.add_paragraph(style="List Bullet")
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
        p.add_run(text)
    else:
        p.add_run(text)
    return p


def numbered(text, bold_lead=None):
    p = doc.add_paragraph(style="List Number")
    if bold_lead:
        r = p.add_run(bold_lead)
        r.bold = True
        p.add_run(text)
    else:
        p.add_run(text)
    return p


def callout(text, color=ACCENT):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.bold = True
    r.font.color.rgb = color
    return p


def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, htext in enumerate(headers):
        hdr[i].text = ""
        run = hdr[i].paragraphs[0].add_run(htext)
        run.bold = True
        run.font.size = Pt(9)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(val))
            run.font.size = Pt(9)
    doc.add_paragraph()
    return t


# ============================================================ TITLE
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = title.add_run("CHF — The Complete Guide")
r.bold = True
r.font.size = Pt(26)
r.font.color.rgb = ACCENT

sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = sub.add_run("Understand the whole project in plain English: what it does, every file, "
                "the results, and how to make it better")
r.italic = True
r.font.size = Pt(12)

meta = doc.add_paragraph()
meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
meta.add_run("Crypto Hybrid Framework (CHF) · Quantitative crypto research pipeline\n"
             "Written as a walkthrough for someone new to the project").font.size = Pt(10)

doc.add_paragraph()

# ============================================================ 0. READ ME FIRST
h1("0. Read this first (don't panic)")
para("You are not stupid, and nothing here is beyond you. CHF is a large, professional-grade "
     "research codebase with ~30 Python modules, three user interfaces, and five years of market "
     "data. Nobody understands a system this size by staring at the files. This guide is the map.")
para("Here is the single most important idea, and once you have it the rest falls into place:")
callout("CHF is a research experiment that asks ONE question: \"Can we use machine learning on "
        "crypto market and blockchain data to reliably beat simply holding Bitcoin?\" The honest, "
        "verified answer it found is: NO. And finding a trustworthy \"no\" is a real, legitimate "
        "scientific result.")
para("Everything in the codebase exists to make that \"no\" trustworthy — to make sure we did not "
     "accidentally cheat, fool ourselves, or overclaim. That is why it looks so complicated: most "
     "of the machinery is there to PREVENT lying to ourselves, not to make money.")
para("This document answers, in order: the quick questions you asked (are we paper trading? long or "
     "short? high-frequency?), how the whole thing works step by step, what every folder and file "
     "does, the actual results and how to read them, where to literally see the portfolio, the real "
     "gaps, and a concrete plan to make it better — including the website and \"real agentic AI\".")

# ============================================================ 1. QUICK ANSWERS
h1("1. Your questions, answered in 30 seconds")
para("Before the deep dive, here are direct answers to everything you asked. Each is expanded later "
     "with evidence from the code.")

table(
    ["Your question", "Short answer", "Detail"],
    [
        ["Are we paper trading?",
         "No. Not even paper trading.",
         "It is a pure HISTORICAL BACKTEST (a simulation on past data). There is no broker, no "
         "exchange connection for orders, no paper account, no live or real money. It is research only."],
        ["Is it long or short?",
         "Long-only.",
         "The config sets allow_short:false and long_only:true. Every position is a 'buy', weights "
         "are always positive, and the code rejects any negative weight."],
        ["Is it high-frequency trading?",
         "No, the opposite.",
         "It rebalances WEEKLY and predicts 7, 14, or 30 days into the future. HFT is "
         "microseconds-to-minutes. This is slow, medium-term investing."],
        ["Is it market-neutral / hedged?",
         "No.",
         "Long-only and unhedged — when crypto falls, the portfolio falls. It is a 'pick the best "
         "coins to hold' strategy, not a hedge fund market-neutral book."],
        ["What did we find?",
         "No verified alpha.",
         "alpha_verified = false for every strategy tested. The ML strategies did not reliably beat "
         "Bitcoin and a 50/50 BTC-ETH blend after costs."],
        ["Are we doing the correct steps?",
         "Yes — the method is sound.",
         "The scientific method here (leakage control, walk-forward testing, costs, multiple "
         "benchmarks, a single 'alpha authority') is genuinely research-grade. The weak spots are "
         "the product/website and some data coverage, not the core science."],
        ["How do I see the portfolio?",
         "One parquet file.",
         "data/allocations/allocations_from_predictions.parquet (and per-strategy copies). Section 7 "
         "gives copy-paste code to open it."],
    ],
)

callout("Bottom line: CHF is a careful, honest, long-only, weekly, research-only backtest. It found "
        "no reliable edge over Bitcoin — and it proved that carefully. The project's value is the "
        "rigor, not a winning strategy.")

# ============================================================ 2. WHAT CHF IS
h1("2. What CHF actually is (plain English)")
para("Imagine you want to test a theory: \"If I look at price patterns and blockchain activity for "
     "the top ~100 cryptocurrencies, can a machine-learning model tell me which coins will go up "
     "the most over the next couple of weeks — well enough to beat just holding Bitcoin?\"")
para("To test that honestly you need to do eight things in order, and CHF is exactly those eight "
     "things, each automated as a 'stage':")
numbered("Decide which coins are even allowed in the experiment (the 'universe').", "Pick the players: ")
numbered("Download their daily prices (open/high/low/close/volume).", "Get price data: ")
numbered("Download blockchain activity (active addresses, fees, total value locked, etc.).", "Get on-chain data: ")
numbered("Turn raw data into 'features' — momentum, volatility, network growth, and so on.", "Build clues (features): ")
numbered("Define what we are trying to predict: the actual return N days into the future.", "Define the answer (labels): ")
numbered("Train ML models to predict those future returns, tested carefully to avoid cheating.", "Train models: ")
numbered("Turn predictions into an actual portfolio: which coins, what weight, each week.", "Build a portfolio: ")
numbered("Simulate trading that portfolio with realistic costs and compare to simple benchmarks.", "Backtest & judge: ")
para("That is the whole project. Everything else — the config files, the database of runs, the "
     "verifiers, the dashboards — is supporting machinery around these eight steps.")

callout("Why so much machinery for a simple idea? Because in quantitative finance it is shockingly "
        "easy to fool yourself into seeing profit that isn't real. CHF spends ~70% of its code "
        "preventing that self-deception. That discipline is the whole point.")

# ============================================================ 3. THE PIPELINE
h1("3. The pipeline, stage by stage")
para("The stages run in a fixed order. Each stage reads files produced by the previous stage and "
     "writes new files for the next one. They never pass data in memory — always through files on "
     "disk under the data/ folder. This is deliberate: you can inspect the output of any stage at "
     "any time, and re-run just one stage.")
para("The order is: universe → market → onchain → features → labels → models → portfolio → "
     "backtest. Here is what each one does, what it produces, and the file it writes.")

h2("Stage 1 — Universe (which coins are allowed)")
bullet("Picks the eligible coins for each month: roughly the top 100 by market cap that are mature "
       "(exist 365+ days), tradable on real exchanges, and not stablecoins/wrapped tokens.", "What it does: ")
bullet("data/raw/universe/universe_monthly.parquet (one row per coin per month).", "Writes: ")
bullet("agents/universe_agent.py, agents/universe_sources.py.", "Code: ")
bullet("This is where 'survivorship bias' is fought. A naive version would use TODAY's top 100 and "
       "pretend they were always the top 100 — which hides coins that died (FTT, LUNA). CHF can use "
       "a point-in-time source (the keyless CoinMarketCap dataset you already extracted) so dead "
       "coins are correctly included in the months they were alive.", "Why it matters: ")

h2("Stage 2 — Market data (prices)")
bullet("Downloads daily OHLCV (open, high, low, close, volume) for every eligible coin, from "
       "Coinbase, then Kraken, then other free sources if needed. Binance and USDT pairs are "
       "deliberately banned for consistency.", "What it does: ")
bullet("data/raw/market/market_ohlcv.parquet.", "Writes: ")
bullet("agents/market_data_agent.py, providers/ccxt_market.py, providers/market_fallbacks.py.", "Code: ")

h2("Stage 3 — On-chain (blockchain activity)")
bullet("Downloads blockchain metrics — active addresses, transaction counts, fees, total value "
       "locked (TVL), MVRV — from CoinMetrics, DeFiLlama, Etherscan, etc.", "What it does: ")
bullet("data/raw/onchain/onchain_wide.parquet.", "Writes: ")
bullet("agents/onchain_agent.py plus the matching files in providers/.", "Code: ")
bullet("On-chain coverage is SPARSE — many smaller coins simply have no good data, so this is one of "
       "the project's real limitations.", "Honest caveat: ")

h2("Stage 4 — Features (turning data into clues)")
bullet("Computes the actual predictive signals: returns over 1/7/30/90 days, momentum, volatility, "
       "drawdown, beta to Bitcoin, plus on-chain growth ratios (NVT, MVRV z-score, TVL growth). Then "
       "it cleans them — clips extreme outliers and removes redundant/duplicated features.", "What it does: ")
bullet("data/features/full_features.parquet plus feature_keep_list.json (which features survived).", "Writes: ")
bullet("agents/feature_agent.py, features/feature_engineering.py.", "Code: ")
bullet("Every feature is lagged so it only uses information available at the time — no peeking into "
       "the future. This is the first big anti-cheating safeguard.", "Anti-leakage: ")

h2("Stage 5 — Labels (defining the right answer)")
bullet("For each coin and each day, computes the ACTUAL future return — e.g. the log return 14 days "
       "ahead. That is the 'correct answer' the model is trying to learn.", "What it does: ")
bullet("data/labels/label_matrix.parquet and modeling_dataset.parquet (features + answers joined).", "Writes: ")
bullet("agents/label_agent.py.", "Code: ")
bullet("The label is an EXACT forward calendar return (close in 14 days ÷ close today). It only "
       "keeps rows where that exact future day exists, so there's no approximation or leakage.", "Key detail: ")

h2("Stage 6 — Models (the machine learning)")
bullet("Trains three model types and compares them: LightGBM (gradient-boosted trees, the main one), "
       "Random Forest, and a simple baseline. (Linear Ridge appears in the alpha-research grid too.) "
       "Each is trained separately for the 7, 14, and 30-day horizons and for market-only vs "
       "market+on-chain feature sets.", "What it does: ")
bullet("data/predictions/model_predictions.parquet and model_leaderboard.parquet.", "Writes: ")
bullet("agents/model_agent.py, agents/alpha_research_agent.py, models/walk_forward.py.", "Code: ")
bullet("The validation is 'purged + embargoed walk-forward cross-validation'. In plain terms: train "
       "on a block of the past, leave a gap (so the answer can't leak through overlapping windows), "
       "then test on the next unseen block, and roll forward. This is the gold-standard way to test "
       "a trading model honestly (see the Glossary).", "Anti-leakage: ")
bullet("The model is judged mainly by 'Rank IC' — does it rank coins in roughly the right order? A "
       "Rank IC of 0.0275 (the best found) means a weak-but-real ability to sort winners from losers "
       "before costs.", "How it's judged: ")

h2("Stage 7 — Portfolio (turning predictions into positions)")
bullet("Each week, takes the latest predictions, keeps only positive-signal coins, ranks them, and "
       "builds long-only weights using several strategies (top-5/10/20 equal weight, volatility-"
       "scaled, score-weighted, turnover-controlled). Caps any single coin at 15%.", "What it does: ")
bullet("data/allocations/allocations_from_predictions.parquet (THIS is the portfolio — see Section 7).", "Writes: ")
bullet("agents/portfolio_agent.py, portfolio/.", "Code: ")
bullet("It is forbidden from seeing realized returns or labels — it can ONLY use predictions. The "
       "code literally scans for and rejects any column named like 'actual', 'future', 'realized'.", "Anti-leakage: ")

h2("Stage 8 — Backtest (the judge)")
bullet("Simulates actually holding each weekly portfolio, subtracts 20 bps (0.20%) trading cost on "
       "turnover, builds an equity curve, and compares against four benchmarks: BTC, ETH, 50/50 "
       "BTC-ETH, and an equal-weight basket of the whole universe.", "What it does: ")
bullet("data/backtests/strategy_comparison.parquet, equity_curves.parquet, vbt_stats.json.", "Writes: ")
bullet("agents/backtest_agent.py.", "Code: ")
callout("BacktestAgent is the ONLY part of the entire system allowed to declare 'this is real alpha'. "
        "Every other stage is explicitly forbidden from claiming success. This 'single source of "
        "truth' design is what makes the negative result credible.")

# ============================================================ 4. FILE BY FILE
h1("4. The codebase, folder by folder and file by file")
para("You asked to understand 'every line'. Reading 30,000 lines literally is not useful — but "
     "understanding what every FILE is responsible for is. This section is your dictionary: find any "
     "file here and you'll know what it does and why it exists.")

h2("Top-level files")
table(["File", "What it is"],
      [["main.py", "The main command-line entrypoint. You type 'python main.py <stage>' and it runs "
        "that stage. Also has 'full' (run everything), 'demo' (fake data), 'serve' (API), 'schedule'."],
       ["run_all.sh", "A shell script that runs every stage in order AND runs a verifier after each "
        "one. The belt-and-suspenders way to run the pipeline."],
       ["Makefile", "Shortcuts. 'make setup', 'make full', 'make dashboard', 'make test', etc."],
       ["requirements.txt", "The Python libraries the project needs."],
       ["README.md / LOCAL_HANDOFF.md", "Human documentation and handoff notes."],
       ["CLAUDE.md", "Instructions describing the project's rules and research-integrity contracts."],
       ["run_dashboard.sh / run_product_dashboard.sh / run_scheduler.sh", "Launchers for the Streamlit "
        "dashboard, the React website, and the background scheduler."]])

h2("configs/ — the control panel")
para("This is the single most important folder for changing behavior. Almost nothing is hardcoded; "
     "it is all driven from here.")
table(["File", "What it does"],
      [["run_config.yaml", "The 1,500-line master settings file. Universe size, date ranges, which "
        "exchanges, model hyperparameters, rebalance frequency (W = weekly), transaction cost (20 "
        "bps), benchmark drawdown limits — everything. It has 'sections' and lighter variants "
        "(_smoke for quick tests, _pit for survivorship-free runs)."],
       ["config.py", "The loader. Reads the YAML, applies any .env overrides, resolves file paths, "
        "and hands a single config object to every stage. Also computes a 'config hash' so each run "
        "is reproducible."],
       ["universe_exclusions.yaml", "List of coins to always exclude (stablecoins, wrapped tokens)."]])

h2("agents/ — the eight stages (the heart of the project)")
para("Every stage is an 'agent' — a Python class that follows the same three-step lifecycle defined "
     "in agents/base.py: prepare() (check inputs are valid), run() (do the work), persist() (write "
     "outputs). The base class wraps these with automatic retries, logging, and a record written to "
     "a small database so you have a full history of every run.")
table(["File", "Responsibility"],
      [["base.py", "The shared lifecycle (prepare/run/persist + retries + run registry). Every agent "
        "inherits from this. You rarely touch it."],
       ["universe_agent.py", "Stage 1. Selects eligible coins per month."],
       ["universe_sources.py / universe_agent_cmc.py", "Helpers that turn raw CoinMarketCap data "
        "into the monthly universe (the survivorship-free path you extracted)."],
       ["market_data_agent.py", "Stage 2. Fetches daily OHLCV with exchange fallbacks."],
       ["onchain_agent.py", "Stage 3. Fetches blockchain metrics from multiple providers."],
       ["feature_agent.py", "Stage 4. Builds and prunes all features (market + on-chain)."],
       ["label_agent.py", "Stage 5. Builds forward-return labels (the prediction targets)."],
       ["model_agent.py", "Stage 6. Trains models with walk-forward CV, scores Rank IC."],
       ["alpha_research_agent.py", "A wider 'signal search' across many model/feature/horizon "
        "combinations. IMPORTANT: it screens for signal only and is forbidden from claiming alpha."],
       ["portfolio_agent.py", "Stage 7. Turns predictions into long-only weekly weights."],
       ["backtest_agent.py", "Stage 8. The sole alpha authority. Simulates, costs, benchmarks, "
        "verdict."]])

h2("providers/ — the data adapters")
para("One file per outside data source. They all share a smart HTTP client that caches every "
     "response to disk (so re-runs are free and reproducible) and respects rate limits.")
table(["File", "Source / role"],
      [["http_client.py", "Shared cache-first HTTP client with retries, throttling, and offline "
        "fixture support. The backbone all providers use."],
       ["coinmarketcap.py", "CoinMarketCap Pro API client (the keyed one)."],
       ["coingecko.py / coinpaprika.py / coincap...", "Free market-data fallbacks."],
       ["ccxt_market.py", "Pulls OHLCV from real exchanges (Coinbase, Kraken, KuCoin, Gemini) via "
        "the CCXT library."],
       ["market_fallbacks.py", "The failover logic — if one exchange fails, try the next source."],
       ["coinmetrics.py / defillama.py / etherscan.py / thegraph.py / blockchair.py / dune.py",
        "On-chain data adapters, one per provider."]])

h2("features/ and models/ — the math libraries")
table(["File", "What it does"],
      [["features/feature_engineering.py", "The actual formulas: safe log returns, rolling z-scores, "
        "downside volatility, beta to BTC, and the correlation/VIF pruning that removes redundant "
        "features."],
       ["models/walk_forward.py", "The honest-testing engine: generates purged + embargoed "
        "walk-forward splits, and computes Rank IC by date. This file is the scientific core."],
       ["models/ (others, e.g. ablation)", "Feature-importance and ablation analysis."]])

h2("portfolio/ and backtesting/ — construction and simulation")
table(["File", "What it does"],
      [["portfolio/ (helpers)", "Weight normalization with caps, turnover control, strategy math used "
        "by portfolio_agent.py."],
       ["backtesting/ (helpers)", "Equity-curve, Sharpe, drawdown, and benchmark math used by "
        "backtest_agent.py (with vectorbt when available, a manual fallback otherwise)."]])

h2("pipelines/, scripts/, jobs/")
table(["File", "What it does"],
      [["pipelines/pipeline_runner.py", "Runs all stages programmatically in order with validation "
        "between each. 'python main.py full' calls this."],
       ["scripts/verify_<stage>_run.py", "One verifier per stage. After a stage runs, its verifier "
        "checks the output is correct and leakage-free. This is the project's test harness."],
       ["scripts/build_cmc_*.py / coinmarketcap_extract/", "The CoinMarketCap extraction tools you "
        "already used for the professor bundle."],
       ["scripts/bootstrap.py / smoke_test.py", "Setup and a fast offline end-to-end check."],
       ["jobs/ (scheduler)", "APScheduler daemon to run the pipeline on a cron schedule."]])

h2("app/ and frontend/ — the user interfaces")
table(["File", "What it is"],
      [["app/api.py", "A small FastAPI web server exposing read-only endpoints (/weights, /signals, "
        "/metrics, /runs). Other programs can fetch results as JSON."],
       ["app/dashboard.py", "The Streamlit dashboard — a local control panel to run stages, view "
        "equity curves, browse files, and read the results. This is the genuinely useful UI."],
       ["frontend/", "A React + Vite website (the 'product' dashboard). Looks polished but is mostly "
        "a static presentation — see Section 9 for the honest assessment."]])

h2("data/, metadata/, mlruns/, logs/ — the outputs")
table(["Folder", "What's inside"],
      [["data/raw/", "Downloaded universe, market, and on-chain data."],
       ["data/features/, data/labels/, data/predictions/", "Engineered features, prediction targets, "
        "and model outputs."],
       ["data/allocations/", "THE PORTFOLIO — the weekly weights (see Section 7)."],
       ["data/backtests/", "Equity curves, benchmark comparison, and the alpha verdict."],
       ["metadata/agent_registry.db", "A SQLite database logging every stage run (when, with what "
        "config, what it produced)."],
       ["mlruns/", "MLflow experiment tracking (metrics and artifacts per run)."],
       ["logs/", "Run logs."]])

# ============================================================ 5. HOW TO RUN
h1("5. How to actually run it")
para("First-time setup (once):")
table(["Command", "What it does"],
      [["make setup", "Creates a virtual environment and installs all libraries."],
       ["python scripts/bootstrap.py", "Creates the data folders and the .env file."]])
para("Run the whole thing:")
table(["Command", "What it does"],
      [["python main.py full", "Runs all 8 stages end to end (needs internet & possibly API keys; "
        "can take a while)."],
       ["python main.py demo", "Generates FAKE synthetic data instantly so you can play with the "
        "dashboards without any API keys. Great for first exploration."],
       ["./run_all.sh", "Full pipeline plus a verifier after each stage (the rigorous way)."],
       ["python main.py <stage>", "Run a single stage, e.g. 'python main.py market'."]])
para("View the results:")
table(["Command", "Opens"],
      [["./run_dashboard.sh", "The Streamlit dashboard at http://localhost:8501 (recommended)."],
       ["python main.py serve", "The API at http://localhost:8000."],
       ["make mlflow", "MLflow experiment UI at http://localhost:5000."],
       ["./run_product_dashboard.sh", "The React website at http://127.0.0.1:5173."]])
callout("New here? Run 'python main.py demo' then './run_dashboard.sh'. You'll see the whole system "
        "working with fake data in under a minute, with zero API keys.")

# ============================================================ 6. PAPER TRADING / STYLE
h1("6. Are we trading? Long or short? High-frequency?")
para("These deserve a clear, evidence-backed answer because they define what this project IS.")

h2("Not trading at all — it's a historical simulation")
para("There is no broker, no exchange order code, no paper-trading account, and no real money "
     "anywhere in the system. The 'backtest' replays past prices and asks 'if we had held these "
     "weights, what would have happened?'. The config even labels the output "
     "allocation_mode = 'diagnostic_not_live_trading'. It is a research instrument, full stop.")

h2("Long-only")
para("Proven in three places: run_config.yaml sets allow_short:false and long_only:true; "
     "portfolio_agent.py hardcodes side='long' on every position and clips weights to be non-negative; "
     "and backtest_agent.py rejects the run if any weight is negative. So the strategy can only ever "
     "BUY coins it likes — it can never bet against (short) a coin, and it never uses leverage.")

h2("Weekly and medium-term — the opposite of high-frequency")
para("It rebalances once a week (rebalance_frequency='W') and the models predict 7, 14, or 30 days "
     "ahead. Over the ~4-year test that's roughly 200 rebalances total. High-frequency trading makes "
     "thousands of trades per second; this is patient, slow, cross-sectional investing — 'which "
     "handful of coins should I hold this week'.")

# ============================================================ 7. SEE THE PORTFOLIO
h1("7. How to see the portfolio it chose")
para("The portfolio lives in one file:")
callout("data/allocations/allocations_from_predictions.parquet")
para("It has ~28,000 rows. Each row says: on this date, for this strategy, hold this symbol at this "
     "weight. There are also per-strategy copies like allocations_top_5_equal_weight.parquet.")
para("Key columns: date_ts (when the position is held), symbol (the coin), weight (fraction of the "
     "portfolio, 0–0.15), strategy_name, predicted_return (the model's forecast), prediction_rank "
     "(1 = most favored), and side (always 'long').")
para("Copy-paste this into a Python file or notebook to view it:")

code = doc.add_paragraph()
code_run = code.add_run(
    "import pandas as pd\n\n"
    "alloc = pd.read_parquet('data/allocations/allocations_from_predictions.parquet')\n\n"
    "# What does the 'top 5 equal weight' strategy hold on its latest date?\n"
    "s = alloc[alloc.strategy_name == 'top_5_equal_weight']\n"
    "latest = s[s.date_ts == s.date_ts.max()]\n"
    "print(latest[['date_ts','symbol','weight','predicted_return','prediction_rank']])\n\n"
    "# List the weekly rebalance dates\n"
    "print(sorted(alloc.date_ts.unique()))"
)
code_run.font.name = "Consolas"
code_run.font.size = Pt(9)
para("Or, with no code at all: run ./run_dashboard.sh, open the Streamlit dashboard, and use the "
     "'Results Explorer' / 'Portfolio' page to see holdings and the equity curve visually.")
callout("Remember: these holdings were never actually bought. They are the simulated portfolio the "
        "backtest evaluated. It's a 'what if we had held this' record, not a real account.")

# ============================================================ 8. RESULTS
h1("8. The results — real numbers and how to read them")
para("This is what the whole pipeline produced. The headline: no strategy beat the market reliably "
     "after costs.")

h2("The benchmarks (what we had to beat), 2022-12 to 2026-03")
table(["Benchmark", "Total return", "CAGR (per year)", "Sharpe", "Worst drop"],
      [["Bitcoin (BTC)", "+305.5%", "53.3%", "1.13", "-49.6%"],
       ["Ethereum (ETH)", "+69.9%", "17.6%", "0.57", "-63.8%"],
       ["50/50 BTC-ETH", "+178.0%", "36.6%", "0.85", "-55.5%"],
       ["Equal-weight universe", "+30.4%", "8.4%", "0.46", "-69.2%"]])
para("Read this as: just holding Bitcoin turned $100 into ~$405 and was the hardest thing to beat. "
     "Sharpe is return per unit of risk — higher is better; BTC's 1.13 is strong.")

h2("The best signal the models found")
para("The strongest model was LightGBM on market-only features at a 14-day horizon, with a Rank IC "
     "of 0.0275 (t-stat 7.10). That t-stat means the weak predictive ability is statistically real, "
     "not luck — but 'real and weak' is not the same as 'profitable after costs'.")

h2("The three candidate strategies after full backtesting")
table(["Strategy", "Total return", "Sharpe", "Worst drop", "Beat BTC?", "Alpha?"],
      [["Linear Ridge / 30d / top-5", "+147.4%", "0.75", "-59.4%", "No", "FALSE"],
       ["LightGBM / 14d / top-20 vol", "+45.4%", "0.50", "-71.5%", "No", "FALSE"],
       ["Random Forest / 14d / top-5", "-30.4%", "0.23", "-86.9%", "No", "FALSE"]])
para("The best one (Linear Ridge) made a respectable +147% and beat ETH and the equal-weight basket "
     "— but Bitcoin made +305% and the 50/50 blend made +178%. So even the winner LOST to simply "
     "holding the two biggest coins. The worst one actually lost money.")

h2("The search behind it")
bullet("80 model experiments were run (out of ~1,170 possible — limited by a compute budget).", "")
bullet("3 signals passed the statistical 'signal screen'.", "")
bullet("0 of them passed the full alpha test after portfolio construction, costs, and benchmarks.", "")
bullet("Every winning signal used market features only — on-chain data did not help in this run.", "")

h2("What 'no verified alpha' really means")
para("It does NOT mean the code failed or the strategies all lost money. It means: after honest "
     "testing with realistic costs, none of the ML strategies reliably beat the simple benchmark of "
     "holding Bitcoin (or a BTC-ETH blend). In other words, the machine learning did not add value "
     "over a passive crypto position — for the data and configurations tested.")
callout("Why is a 'no' valuable? Because the system was engineered to REJECT false positives. Most "
        "amateur backtests accidentally 'find' alpha that evaporates in real life due to leakage, "
        "cherry-picking, or ignored costs. CHF's honest 'no' is more trustworthy than a flashy 'yes' "
        "— and that rigor is exactly what a research/professor audience respects.")

# ============================================================ 9. GAPS
h1("9. The real gaps (honest assessment)")
para("You asked what's missing and whether this is up to research / professional standard. The "
     "SCIENCE is close to research standard. The PRODUCT (website, visuals, polish) and some DATA "
     "coverage are not yet. Here is the honest list, grouped.")

h2("Data & universe gaps")
bullet("The production universe in places is still a 'latest-survivor' list, not fully point-in-time. "
       "You already extracted the survivorship-free CoinMarketCap data (cmc_web_pit) — the gap is "
       "fully switching the production run to it everywhere.", "Survivorship: ")
bullet("Many smaller coins have little or no on-chain data, so on-chain features are thin and "
       "probably under-tested.", "On-chain sparsity: ")
bullet("True daily OHLCV from CoinMarketCap isn't available on the current plan; the project uses "
       "quotes (close/volume) instead. Fine, but worth stating.", "OHLCV source: ")

h2("Modeling gaps")
bullet("Only 80 of ~1,170 experiments ran (a budget cap). The search space is under-explored — "
       "there could be signals not yet tested.", "Coverage: ")
bullet("The signal-gate thresholds (e.g. Rank IC > 0.01, t-stat > 1.5) are hand-chosen and not "
       "corrected for multiple testing — a statistician would want a formal correction.", "Thresholds: ")
bullet("Feature pruning (correlation > 0.85, VIF > 10) is a reasonable heuristic but not "
       "theoretically justified, and the VIF step samples rows.", "Pruning: ")
bullet("All models are fairly shallow trees / linear. No deep models, no ensembling, no "
       "hyperparameter optimization (Optuna is in requirements but unused).", "Model variety: ")

h2("Product, website & visualization gaps")
bullet("The React website (frontend/) looks professional but is mostly STATIC: its numbers and "
       "artifact counts are hardcoded in JavaScript files, and it does not actually load the real "
       "data files. It is a pitch deck, not a live tool.", "Website is a facade: ")
bullet("The Streamlit dashboard is genuinely useful but hardcodes some result tables instead of "
       "reading them live, and it can't stream progress while a stage runs.", "Dashboard: ")
bullet("Very few charts exist. There is one architecture image and two feature-importance CSVs. The "
       "rich, obvious visualizations (equity curves vs benchmarks, drawdown, Rank IC decay, feature "
       "importance heatmap, cost sensitivity) are mostly not rendered anywhere.", "Visuals: ")

h2("Engineering / process gaps")
bullet("There are two ways to run the pipeline (run_all.sh and pipeline_runner.py) that can drift "
       "out of sync if a stage is added to one but not the other.", "Two orchestrators: ")
bullet("A few file paths are hardcoded in pipeline_runner.py instead of read from config.", "Hardcoded paths: ")
bullet("The 'agents' are NOT AI agents — they are deterministic pipeline steps (see Section 11).", "Naming: ")

# ============================================================ 10. HOW TO IMPROVE
h1("10. How to make it better (concrete plan)")
para("Prioritized, concrete, and achievable. Roughly ordered by value-for-effort.")

h2("Quick wins (days)")
numbered("Make the Streamlit dashboard read the real result files (model_leaderboard.parquet, "
         "strategy_comparison.parquet) instead of hardcoded tables. ~30 lines.", "")
numbered("Add the obvious charts (equity curve vs benchmarks, drawdown, feature importance) as PNGs "
         "the dashboard and website can show. The data already exists.", "")
numbered("Write one 'Getting Started in 5 minutes' page with exact copy-paste commands.", "")
numbered("Fully switch the production run to the survivorship-free cmc_web_pit universe and re-run, "
         "so every reported number is point-in-time clean.", "")

h2("Medium effort (weeks)")
numbered("Make the React website load real artifacts (via the FastAPI endpoints) instead of "
         "hardcoded JS — turn the pitch deck into a live tool.", "")
numbered("Expand the model search beyond 80 experiments; add Optuna hyperparameter tuning.", "")
numbered("Add a formal multiple-testing correction (e.g. Deflated Sharpe / Benjamini-Hochberg) so "
         "the signal gate is statistically defensible.", "")
numbered("Densify on-chain coverage or clearly scope the universe to coins that have it.", "")

h2("Larger / research-grade")
numbered("Add a proper transaction-cost and liquidity model (slippage, market impact), not just a "
         "flat 20 bps.", "")
numbered("Test long/short and market-neutral variants to separate 'coin selection skill' from "
         "'crypto went up'.", "")
numbered("Walk-forward retrain in a true out-of-sample 'paper' mode on the most recent unseen data.", "")

# ============================================================ 11. AGENTIC AI
h1("11. Making it a 'real agentic AI system'")
para("Important honesty first, because the word 'agent' is used two different ways:")
callout("Today's 'agents' in CHF are NOT AI. They are ordinary Python classes called UniverseAgent, "
        "ModelAgent, etc. — deterministic pipeline steps with a shared lifecycle. There is no LLM, no "
        "reasoning, no decision-making. The naming is a software pattern, not artificial intelligence.")
para("A 'real agentic AI system' means an LLM (like the one writing this) that can reason, choose "
     "what to do next, call tools, inspect results, and adapt — rather than following a fixed script. "
     "Here is a realistic path from what you have to that, in layers you can add one at a time:")

h2("Layer 1 — A research copilot (easiest, high value)")
bullet("Add an LLM assistant that can read the result files and answer plain-English questions: "
       "'why did the Random Forest strategy fail?', 'summarize this run', 'which feature mattered "
       "most?'. It reads data/ and docs/ and explains. No control, just understanding.", "")

h2("Layer 2 — An agent that proposes and runs experiments")
bullet("Give the LLM a set of TOOLS (functions): run_stage(name), read_results(), edit_config(param). "
       "Then it can form a hypothesis ('on-chain features might help at 30 days'), change the config, "
       "launch the run, read the leaderboard, and decide the next experiment — looping until it finds "
       "or rules out a signal. The walk-forward and backtest guards stay as the safety rails.", "")

h2("Layer 3 — A multi-agent research team")
bullet("Several specialized LLM agents: a 'feature-ideas' agent that proposes new signals, a "
       "'skeptic' agent that tries to prove any found alpha is leakage or luck, and an 'orchestrator' "
       "that schedules their work. This mirrors how a real quant research team operates.", "")

para("Crucially, CHF is unusually well-suited to this because it already has: clean file-based "
     "contracts (an LLM can read every stage's output), per-stage verifiers (automatic safety "
     "checks), and a single alpha authority (the LLM can never overclaim — BacktestAgent still "
     "decides). You would be adding a reasoning layer ON TOP of a sound, guard-railed engine, which "
     "is the right and safe way to build agentic AI.")

# ============================================================ 12. VISUALS
h1("12. Better visuals (what to add)")
para("Right now there is essentially one diagram and a couple of CSVs. These are the charts that "
     "would make the project instantly understandable and presentation-ready. All the underlying "
     "data already exists in data/backtests/ and data/predictions/.")
table(["Chart", "Why it matters"],
      [["Equity curve: each strategy vs BTC / ETH / 50-50 / equal-weight", "The single most important "
        "chart — instantly shows the strategies trailing Bitcoin."],
       ["Drawdown 'underwater' plot", "Shows how deep and how long each strategy was underwater "
        "(risk), not just the upside."],
       ["Rank IC over time / by horizon", "Shows whether the predictive signal is stable or decays — "
        "the core 'is there an edge' picture."],
       ["Feature-importance heatmap (models × features)", "Shows which signals the models actually "
        "used; great for intuition and for the professor."],
       ["Cost-sensitivity curve (return vs bps)", "Shows how quickly the edge dies as trading costs "
        "rise — a key honesty check."],
       ["Turnover / number-of-holdings timeline", "Shows how much the portfolio churns each week."],
       ["Per-fold out-of-sample performance", "Shows which time periods the model won or lost in — "
        "exposes regime dependence."]])
para("Concretely: add a small reports/plots.py that reads the parquet files and saves these as PNGs "
     "into artifacts/plots/, then show them in both the Streamlit dashboard and the React site. This "
     "is a high-impact, low-risk improvement.")

# ============================================================ 13. WEBSITE
h1("13. The website — honest verdict and fix plan")
para("You said the website is unusable. You're partly right, and here's the precise diagnosis so "
     "you can fix it rather than feel bad about it. There are actually TWO UIs:")
h2("The Streamlit dashboard (app/dashboard.py) — keep and improve")
bullet("This one genuinely works: it reads real files, draws equity curves, lets you run stages "
       "safely, and browses docs. For research use, this is your real tool.", "Verdict: ")
bullet("Make it read all result tables live, stream stage progress, and add the charts from Section "
       "12.", "Fix: ")
h2("The React website (frontend/) — beautiful but hollow")
bullet("It looks like a polished startup product, but the numbers and 'artifacts discovered' counts "
       "are hardcoded in JavaScript (chfProductData.js, artifactIndex.js). It does not load the real "
       "data, so it can show stale or disconnected information. That mismatch is why it feels wrong "
       "to use.", "Verdict: ")
bullet("Point it at the FastAPI endpoints (app/api.py already serves /weights, /signals, /metrics) so "
       "it displays live results; render real equity curves; and regenerate the artifact index from "
       "the actual data/ folder after each run.", "Fix: ")
callout("For now, demo the project with the Streamlit dashboard, not the React site. The React site "
        "is a presentation shell that needs to be wired to real data before it's trustworthy.")

# ============================================================ 14. GLOSSARY
h1("14. Glossary (every scary term, in plain words)")
table(["Term", "Plain meaning"],
      [["Alpha", "Profit beyond what a simple benchmark (like holding Bitcoin) would give you. The "
        "holy grail. CHF found none that was verified."],
       ["Backtest", "A simulation of a strategy on past data. No real trades."],
       ["Long-only", "You can only buy and hold; you can't bet on prices falling (shorting)."],
       ["Universe", "The set of coins allowed in the experiment at a given time."],
       ["Survivorship bias", "The mistake of only studying coins that survived to today, ignoring "
        "ones that died — which flatters results. CHF fights this."],
       ["Feature", "A computed clue used for prediction (e.g. 30-day momentum)."],
       ["Label", "The correct answer being predicted (the actual future return)."],
       ["Leakage", "Accidentally using future information to predict the future — cheating. The whole "
        "system is built to prevent it."],
       ["Walk-forward CV", "Train on the past, test on the next unseen period, roll forward. The "
        "honest way to test a trading model over time."],
       ["Purge & embargo", "Deleting training data near the test period so overlapping returns can't "
        "leak the answer. A gap between train and test."],
       ["Rank IC", "Information Coefficient: how well the model RANKS coins from best to worst "
        "(Spearman correlation). The main quality score. ~0.03 = weak but real."],
       ["Sharpe ratio", "Return divided by risk (volatility). Higher = better risk-adjusted return."],
       ["CAGR", "Compound annual growth rate — the smoothed yearly return."],
       ["Max drawdown", "The worst peak-to-trough loss. -50% means it halved at some point."],
       ["Rebalance", "Updating the portfolio weights. CHF does this weekly."],
       ["Transaction cost (bps)", "Trading fee. 20 bps = 0.20% per unit traded."],
       ["bps", "Basis points. 1 bp = 0.01%. 100 bps = 1%."],
       ["Benchmark", "A simple baseline to beat (BTC, ETH, 50/50, equal-weight)."]])

# ============================================================ 15. PROFESSOR
h1("15. What to tell your professor (the honest pitch)")
para("You have something genuinely defensible. The pitch is not 'I built a money-making bot' — it's "
     "'I built a rigorous, leakage-safe research pipeline that honestly tested whether ML beats "
     "Bitcoin, and reported a clean negative result'. That is exactly how real quantitative research "
     "is supposed to work.")
bullet("I built an 8-stage automated pipeline (universe → data → features → labels → models → "
       "portfolio → backtest) on ~100 coins over 5 years.", "What I did: ")
bullet("I prevented look-ahead bias with purged, embargoed walk-forward cross-validation and a "
       "single 'alpha authority' that is the only component allowed to claim success.", "How I kept it honest: ")
bullet("Across 80 experiments, 3 signals were statistically real (best Rank IC 0.0275, t-stat 7.1), "
       "but ZERO beat Bitcoin and a 50/50 BTC-ETH blend after 20 bps costs. alpha_verified = false.", "What I found: ")
bullet("I additionally solved the survivorship-bias problem by extracting a free, point-in-time "
       "CoinMarketCap dataset (66 monthly + 1,095 daily snapshots including delisted coins).", "Data contribution: ")
bullet("A clean negative result, produced by a method engineered to reject false positives, is a "
       "credible scientific finding — and the pipeline is reusable for future signal research.", "Why it matters: ")

doc.add_paragraph()
end = doc.add_paragraph()
end.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = end.add_run("— End of guide —  You understand this project now. The complexity was never about "
                "you; it was the system. Keep this file as your map.")
r.italic = True
r.font.size = Pt(10)

doc.save("chfguide.docx")
print("Wrote chfguide.docx")
