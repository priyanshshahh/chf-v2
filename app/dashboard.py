"""
Project CHF Control Center.

Streamlit dashboard for local review, demo, and safe pipeline control. The app
does not change research logic or recompute results unless a user explicitly
confirms and clicks a local pipeline command button.

Security note: command execution should be disabled or protected behind
authentication before any public deployment.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA = ROOT / "data"
LOG_DIR = ROOT / "logs" / "dashboard_runs"

RELEASE_TAG = "v1.0-research-release"
FINAL_RESULT = "No verified alpha found under tested configurations."
BENCHMARK_WINDOW = ("2022-12-15", "2026-03-24")

PIPELINE = [
    {
        "agent": "UniverseAgent",
        "command": ["python3", "main.py", "universe", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_universe_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Builds the eligible crypto universe.",
        "inputs": "Provider APIs / config exclusions",
        "outputs": ["data/raw/universe/universe_monthly.parquet", "data/raw/universe/universe_manifest.json"],
        "frequency": "Monthly",
    },
    {
        "agent": "MarketDataAgent",
        "command": ["python3", "main.py", "market", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_market_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Ingests and validates OHLCV data.",
        "inputs": "Universe outputs",
        "outputs": ["data/raw/market/market_ohlcv.parquet", "data/raw/market/market_manifest.json"],
        "frequency": "Daily",
    },
    {
        "agent": "OnChainAgent",
        "command": ["python3", "main.py", "onchain", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_onchain_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Ingests CoinMetrics, DeFiLlama, and optional provider metrics.",
        "inputs": "Universe and market outputs",
        "outputs": ["data/raw/onchain/onchain_observations.parquet", "data/raw/onchain/onchain_manifest.json"],
        "frequency": "Daily",
    },
    {
        "agent": "FeatureAgent",
        "command": ["python3", "main.py", "features", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_feature_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Builds leakage-safe market and on-chain features.",
        "inputs": "Market and on-chain outputs",
        "outputs": ["data/features/full_features.parquet", "data/features/full_features_pruned.parquet"],
        "frequency": "Daily after data",
    },
    {
        "agent": "LabelAgent",
        "command": ["python3", "main.py", "labels", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_label_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Creates exact forward calendar labels.",
        "inputs": "Feature and market outputs",
        "outputs": ["data/labels/label_matrix.parquet", "data/labels/modeling_dataset.parquet"],
        "frequency": "Daily after features",
    },
    {
        "agent": "ModelAgent",
        "command": ["python3", "main.py", "model", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_model_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Runs purged walk-forward signal screening.",
        "inputs": "Modeling dataset",
        "outputs": ["data/predictions/model_predictions.parquet", "data/predictions/model_leaderboard.parquet"],
        "frequency": "Weekly or manual",
    },
    {
        "agent": "PortfolioAgent",
        "command": ["python3", "main.py", "portfolio", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_portfolio_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Creates deterministic allocations from prediction-safe files.",
        "inputs": "Prediction outputs and market data",
        "outputs": ["data/allocations/allocations_from_predictions.parquet", "data/allocations/allocation_manifest.json"],
        "frequency": "Weekly",
    },
    {
        "agent": "BacktestAgent",
        "command": ["python3", "main.py", "backtest", "--config", "configs/run_config.yaml"],
        "verifier": ["python3", "scripts/verify_backtest_run.py", "--config", "configs/run_config.yaml"],
        "purpose": "Final alpha authority after costs and benchmark comparison.",
        "inputs": "Allocations and market data",
        "outputs": ["data/backtests/backtest_summary.parquet", "data/backtests/alpha_report.json"],
        "frequency": "Manual validation",
    },
]

FULL_PIPELINE_COMMAND = ["bash", "run_all.sh"]
SCHEDULER_COMMAND = ["python3", "main.py", "schedule", "--config", "configs/run_config.yaml"]

BENCHMARK_ROWS = [
    {"Benchmark": "BTC", "Total Return": "305.50%"},
    {"Benchmark": "ETH", "Total Return": "69.85%"},
    {"Benchmark": "BTC/ETH 50-50", "Total Return": "178.04%"},
    {"Benchmark": "Equal-weight universe", "Total Return": "30.39%"},
]

CANDIDATE_ROWS = [
    {
        "Candidate": "lightgbm / market_only / raw_forward_return / 14d",
        "Best Strategy": "top_20_vol_scaled",
        "Return": "45.39%",
        "CAGR": "12.10%",
        "Sharpe": "0.5030",
        "Max DD": "-71.45%",
        "Alpha Verified": "false",
    },
    {
        "Candidate": "linear_ridge / market_only / raw_forward_return / 30d",
        "Best Strategy": "top_5_equal_weight",
        "Return": "147.36%",
        "CAGR": "31.84%",
        "Sharpe": "0.7521",
        "Max DD": "-59.40%",
        "Alpha Verified": "false",
    },
    {
        "Candidate": "random_forest / market_only / raw_forward_return / 14d",
        "Best Strategy": "top_5_equal_weight",
        "Return": "-30.40%",
        "CAGR": "-10.47%",
        "Sharpe": "0.2288",
        "Max DD": "-86.86%",
        "Alpha Verified": "false",
    },
]

DOCS_TO_SHOW = {
    "README": ROOT / "README.md",
    "Final Reviewer Packet": DOCS / "FINAL_REVIEWER_PACKET.md",
    "Benchmark Verification": DOCS / "BENCHMARK_VERIFICATION.md",
    "Research Results Summary": DOCS / "RESEARCH_RESULTS_SUMMARY.md",
    "Alpha Backtest Verification": DOCS / "ALPHA_BACKTEST_VERIFICATION_REPORT.md",
    "Reproducibility Checklist": DOCS / "REPRODUCIBILITY_CHECKLIST.md",
    "Artifact Manifest": DOCS / "ARTIFACT_MANIFEST.md",
    "Final Release Audit": DOCS / "FINAL_RELEASE_AUDIT.md",
}

SAFE_DIRS = {
    "docs": DOCS,
    "reports": ROOT / "reports",
    "artifacts": ROOT / "artifacts",
    "configs": ROOT / "configs",
    "tests": ROOT / "tests",
    "app": ROOT / "app",
}
SAFE_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}
BLOCKED_PARTS = {".env", ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


st.set_page_config(page_title="Project CHF Control Center", page_icon="CHF", layout="wide")


def run_git(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return "unavailable"


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def file_status(paths: list[str]) -> tuple[str, str]:
    existing = [ROOT / p for p in paths if (ROOT / p).exists()]
    if not existing:
        return "missing", "No expected outputs found"
    newest = max(existing, key=lambda p: p.stat().st_mtime)
    age = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
    return "available", age.strftime("%Y-%m-%d %H:%M UTC")


def run_command(command: list[str], label: str) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = label.lower().replace(" ", "_").replace("/", "_")
    log_path = LOG_DIR / f"{stamp}_{safe_label}.log"
    started = datetime.now(timezone.utc)
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    finished = datetime.now(timezone.utc)
    content = [
        f"label: {label}",
        f"command: {' '.join(command)}",
        f"started_utc: {started.isoformat()}",
        f"finished_utc: {finished.isoformat()}",
        f"returncode: {proc.returncode}",
        "",
        "STDOUT:",
        proc.stdout,
        "",
        "STDERR:",
        proc.stderr,
    ]
    log_path.write_text("\n".join(content), encoding="utf-8")
    st.session_state["last_command_result"] = {
        "label": label,
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "log_path": rel(log_path),
    }
    return st.session_state["last_command_result"]


@st.cache_data(show_spinner=False)
def read_text(path: str) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def safe_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in BLOCKED_PARTS or part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() in SAFE_EXTS:
            files.append(p)
    return sorted(files)


def render_safe_file(path: Path) -> None:
    resolved = path.resolve()
    allowed = any(resolved.is_relative_to(base.resolve()) for base in SAFE_DIRS.values() if base.exists())
    if not allowed or any(part in BLOCKED_PARTS or part.startswith(".") for part in resolved.parts):
        st.error("Blocked by safe file browser policy.")
        return
    if resolved.suffix.lower() not in SAFE_EXTS:
        st.error("File extension is not allowed.")
        return
    if not resolved.exists():
        st.warning(f"File not found: `{rel(resolved)}`")
        return
    if resolved.suffix.lower() == ".csv":
        st.dataframe(pd.read_csv(resolved), use_container_width=True)
    elif resolved.suffix.lower() == ".json":
        try:
            st.json(json.loads(resolved.read_text(encoding="utf-8")))
        except Exception:
            st.code(resolved.read_text(encoding="utf-8", errors="replace")[:20000])
    elif resolved.suffix.lower() in {".yaml", ".yml"}:
        st.code(resolved.read_text(encoding="utf-8", errors="replace")[:20000], language="yaml")
    elif resolved.suffix.lower() == ".md":
        st.markdown(resolved.read_text(encoding="utf-8", errors="replace")[:20000])
    else:
        st.code(resolved.read_text(encoding="utf-8", errors="replace")[:20000])


branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
commit = run_git(["rev-parse", "--short=8", "HEAD"])

with st.sidebar:
    st.title("Project CHF")
    st.caption("Production-style local control center")
    page = st.radio(
        "Navigate",
        [
            "Home / Executive Summary",
            "Pipeline Control Center",
            "Scheduler / Automation",
            "Results Explorer",
            "Project Explorer",
            "Architecture / Methodology",
            "Reproducibility",
            "Logs / Run History",
        ],
    )
    st.divider()
    st.metric("Branch", branch)
    st.metric("Commit", commit)
    st.metric("Release tag", RELEASE_TAG)
    st.caption("Do not deploy command controls publicly without authentication.")

st.title("Project CHF Control Center")
st.caption("Local demo and operations UI for the frozen research release.")

if page == "Home / Executive Summary":
    st.header("Home / Executive Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alpha verified", "false")
    c2.metric("Candidates tested", "3")
    c3.metric("Benchmark window", "2022-12-15 → 2026-03-24")
    c4.metric("Release", RELEASE_TAG)
    st.success(FINAL_RESULT)
    st.warning("Research and education only. Not financial advice. No live trading or investment recommendation is implied.")
    st.markdown(
        """
Project CHF is a reproducible crypto quant research system. It tests whether
market and on-chain features can produce cross-sectional alpha after
leakage-safe validation, deterministic portfolio construction, transaction
costs, benchmark sanity checks, and out-of-sample backtesting.
"""
    )
    st.dataframe(pd.DataFrame(CANDIDATE_ROWS), use_container_width=True, hide_index=True)

elif page == "Pipeline Control Center":
    st.header("Pipeline Control Center")
    st.warning("Running commands may update local generated outputs under `data/`. Nothing runs automatically.")
    confirm = st.checkbox("I understand these buttons execute local pipeline commands and may update local outputs.")

    rows = []
    for item in PIPELINE:
        status, modified = file_status(item["outputs"])
        rows.append(
            {
                "Agent": item["agent"],
                "Purpose": item["purpose"],
                "Inputs": item["inputs"],
                "Outputs": ", ".join(item["outputs"]),
                "Status": status,
                "Last Modified": modified,
                "Frequency": item["frequency"],
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    tabs = st.tabs([item["agent"] for item in PIPELINE] + ["Full Pipeline"])
    for tab, item in zip(tabs[:-1], PIPELINE):
        with tab:
            st.write(item["purpose"])
            st.code(" ".join(item["command"]), language="bash")
            run = st.button(f"Run {item['agent']}", key=f"run_{item['agent']}", disabled=not confirm)
            verify = st.button(f"Run verifier for {item['agent']}", key=f"verify_{item['agent']}", disabled=not confirm)
            if run:
                with st.spinner(f"Running {item['agent']}..."):
                    result = run_command(item["command"], item["agent"])
                st.success("Command completed" if result["returncode"] == 0 else "Command failed")
            if verify:
                with st.spinner(f"Running verifier for {item['agent']}..."):
                    result = run_command(item["verifier"], f"{item['agent']} verifier")
                st.success("Verifier completed" if result["returncode"] == 0 else "Verifier failed")

    with tabs[-1]:
        st.write("Runs `run_all.sh`, which executes pipeline stages and verifiers.")
        st.code("bash run_all.sh", language="bash")
        if st.button("Run full pipeline", disabled=not confirm):
            with st.spinner("Running full pipeline..."):
                result = run_command(FULL_PIPELINE_COMMAND, "Full pipeline")
            st.success("Full pipeline completed" if result["returncode"] == 0 else "Full pipeline failed")

    if "last_command_result" in st.session_state:
        res = st.session_state["last_command_result"]
        st.subheader("Latest Command Result")
        st.write(f"Command: `{res['command']}`")
        st.write(f"Return code: `{res['returncode']}`")
        st.write(f"Log: `{res['log_path']}`")
        with st.expander("stdout"):
            st.code(res["stdout"][-10000:] or "(empty)")
        with st.expander("stderr"):
            st.code(res["stderr"][-10000:] or "(empty)")

elif page == "Scheduler / Automation":
    st.header("Scheduler / Automation")
    st.info("Scheduler is local-only and runs until the terminal/server is stopped unless deployed as a service.")
    schedule = pd.DataFrame(
        [
            {"Stage": "UniverseAgent", "Frequency": "Monthly", "Default": "1st of month, 02:00 UTC"},
            {"Stage": "MarketDataAgent", "Frequency": "Daily", "Default": "06:00 UTC"},
            {"Stage": "OnChainAgent", "Frequency": "Daily", "Default": "07:00 UTC"},
            {"Stage": "FeatureAgent", "Frequency": "Daily", "Default": "08:00 UTC"},
            {"Stage": "LabelAgent", "Frequency": "Daily", "Default": "After features"},
            {"Stage": "ModelAgent", "Frequency": "Weekly/manual", "Default": "Monday, 10:00 UTC"},
            {"Stage": "PortfolioAgent", "Frequency": "Weekly", "Default": "Monday, 12:00 UTC"},
            {"Stage": "BacktestAgent", "Frequency": "Manual/research validation", "Default": "Not daily by default"},
        ]
    )
    st.dataframe(schedule, use_container_width=True, hide_index=True)
    st.subheader("Start scheduler locally")
    st.code("./run_scheduler.sh\n# or\npython3 main.py schedule --config configs/run_config.yaml", language="bash")
    st.warning("The scheduler can update local outputs. Start it intentionally from a terminal.")

elif page == "Results Explorer":
    st.header("Results Explorer")
    st.success(FINAL_RESULT)
    left, right = st.columns(2)
    with left:
        st.subheader("Candidate Results")
        st.dataframe(pd.DataFrame(CANDIDATE_ROWS), use_container_width=True, hide_index=True)
    with right:
        st.subheader("Benchmarks")
        st.write(f"Window: `{BENCHMARK_WINDOW[0]}` to `{BENCHMARK_WINDOW[1]}`")
        st.dataframe(pd.DataFrame(BENCHMARK_ROWS), use_container_width=True, hide_index=True)

    equity_files = sorted(DATA.glob("backtests_candidate_*/equity_curves.parquet"))
    if equity_files:
        selected = st.selectbox("Equity curve file", equity_files, format_func=lambda p: rel(p))
        df = pd.read_parquet(selected)
        if {"date_ts", "strategy_name", "portfolio_value"}.issubset(df.columns):
            strategies = st.multiselect(
                "Strategies",
                sorted(df["strategy_name"].dropna().unique().tolist()),
                default=["BTC", "ETH", "BTC_ETH_50_50"] if "BTC" in set(df["strategy_name"]) else None,
            )
            plot_df = df[df["strategy_name"].isin(strategies)] if strategies else df.head(0)
            st.line_chart(plot_df, x="date_ts", y="portfolio_value", color="strategy_name")
        else:
            st.warning("Equity file does not contain expected columns.")
    else:
        st.warning("No candidate equity curve files found under `data/backtests_candidate_*/`.")

    doc = st.selectbox("Open result report", list(DOCS_TO_SHOW.keys()))
    render_safe_file(DOCS_TO_SHOW[doc])

elif page == "Project Explorer":
    st.header("Project Explorer")
    st.caption("Safe browser limited to whitelisted directories and file extensions.")
    area = st.selectbox("Area", list(SAFE_DIRS.keys()))
    files = safe_files(SAFE_DIRS[area])
    if not files:
        st.warning("No safe files found in this area.")
    else:
        selected = st.selectbox("File", files, format_func=lambda p: rel(p))
        st.caption(rel(selected))
        render_safe_file(selected)

elif page == "Architecture / Methodology":
    st.header("Architecture / Methodology")
    st.markdown(
        """
```text
UniverseAgent → MarketDataAgent → OnChainAgent → FeatureAgent → LabelAgent → ModelAgent → AlphaResearchAgent → PortfolioAgent → BacktestAgent
```

- Labels are exact forward calendar labels.
- Model validation is purged and embargoed walk-forward validation.
- PortfolioAgent consumes prediction-safe files only.
- BacktestAgent applies transaction costs and benchmark sanity checks.
- Benchmark comparisons include BTC, ETH, BTC/ETH 50-50, and equal-weight universe.
- No verified alpha is still a valid research result because the system rejected unsupported claims.
"""
    )
    st.subheader("Cost Convention")
    st.write("Benchmark verification uses CHF's exact candidate backtest window and BacktestAgent's 20 bps initial benchmark cost convention.")
    render_safe_file(DOCS / "BENCHMARK_VERIFICATION.md")

elif page == "Reproducibility":
    st.header("Reproducibility")
    st.code(
        "python3 -m py_compile main.py agents/*.py providers/*.py features/*.py models/*.py pipelines/*.py scripts/*.py app/*.py\n"
        "python3 -m pytest tests/test_alpha_research_agent.py tests/test_model_agent_research_mode.py tests/test_backtest_agent_research_mode.py -q",
        language="bash",
    )
    for label in ["Final Reviewer Packet", "Reproducibility Checklist", "Artifact Manifest", "Final Release Audit"]:
        with st.expander(label):
            render_safe_file(DOCS_TO_SHOW[label])

elif page == "Logs / Run History":
    st.header("Logs / Run History")
    st.caption("Dashboard-triggered command logs are written to `logs/dashboard_runs/`.")
    if not LOG_DIR.exists():
        st.info("No dashboard command logs found yet.")
    else:
        logs = sorted(LOG_DIR.glob("*.log"), reverse=True)
        if not logs:
            st.info("No dashboard command logs found yet.")
        else:
            selected = st.selectbox("Log file", logs, format_func=lambda p: rel(p))
            st.code(selected.read_text(encoding="utf-8", errors="replace")[-20000:])
