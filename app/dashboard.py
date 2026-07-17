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
import os
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

# Read-only demo mode (set by streamlit_app.py for cloud deployments): the
# Pipeline Control Center is display-only and command execution is refused.
READ_ONLY = os.environ.get("CHF_DASHBOARD_READ_ONLY", "").strip().lower() in {"1", "true", "yes"}

RELEASE_TAG = "v1.0-research-release"
FINAL_RESULT = "No verified alpha found under tested configurations."
PIPELINE_HINT = "Run the pipeline to generate results (e.g. `bash run_all.sh` or `python3 main.py backtest`)."

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

def load_json_file(path: Path) -> Any:
    """Read a JSON artifact, returning None instead of crashing when absent."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_parquet_file(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def fmt_pct(x: Any, digits: int = 2) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_num(x: Any, digits: int = 4) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def canonical_results() -> dict[str, Any]:
    """Canonical results read from data files at render time (never hardcoded)."""
    return {
        "summary": load_json_file(DATA / "backtests" / "backtest_summary.json"),
        "manifest": load_json_file(DATA / "backtests" / "backtest_manifest.json"),
        "alpha": load_json_file(DATA / "backtests" / "alpha_report.json"),
        "allocation": load_json_file(DATA / "allocations" / "allocation_manifest.json"),
    }


def canonical_window(results: dict[str, Any]) -> tuple[str, str] | None:
    rows = results.get("summary") or []
    if not isinstance(rows, list) or not rows:
        return None
    starts = [r.get("start_date", "")[:10] for r in rows if r.get("start_date")]
    ends = [r.get("end_date", "")[:10] for r in rows if r.get("end_date")]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def strategy_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Strategy": r.get("strategy_name"),
                "Total Return": fmt_pct(r.get("total_return")),
                "CAGR": fmt_pct(r.get("cagr")),
                "Sharpe": fmt_num(r.get("sharpe")),
                "Max DD": fmt_pct(r.get("max_drawdown")),
                "Turnover (ann.)": fmt_num(r.get("annualized_turnover"), 2),
            }
            for r in rows
        ]
    )


def benchmark_frame(results: dict[str, Any]) -> pd.DataFrame | None:
    alpha = results.get("alpha") or {}
    rows = alpha.get("benchmark_rows")
    if not rows:
        bench = load_parquet_file(DATA / "backtests" / "benchmark_summary.parquet")
        if bench is None or bench.empty:
            return None
        rows = bench.to_dict("records")
    return pd.DataFrame(
        [
            {
                "Benchmark": r.get("strategy_name"),
                "Total Return": fmt_pct(r.get("total_return")),
                "Sharpe": fmt_num(r.get("sharpe")),
                "Max DD": fmt_pct(r.get("max_drawdown")),
            }
            for r in rows
        ]
    )


def candidate_studies() -> list[tuple[str, pd.DataFrame]]:
    """Legacy per-candidate backtests (frozen May 2026 study), if present."""
    studies = []
    for d in sorted(DATA.glob("backtests_candidate_*")):
        rows = load_json_file(d / "backtest_summary.json")
        if not isinstance(rows, list) or not rows:
            continue
        name = d.name.replace("backtests_candidate_", "")
        studies.append((name, strategy_frame(rows)))
    return studies

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
    if READ_ONLY:
        st.error("Read-only demo deployment: command execution is disabled.")
        return {"label": label, "command": " ".join(command), "returncode": -1,
                "stdout": "", "stderr": "disabled in read-only mode", "log_path": ""}
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
            "Paper Trading",
            "Fund Operations",
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
    results = canonical_results()
    manifest = results.get("manifest") or {}
    alpha = results.get("alpha") or {}
    allocation = results.get("allocation") or {}
    summary_rows = results.get("summary") or []
    window = canonical_window(results)

    c1, c2, c3, c4 = st.columns(4)
    if manifest:
        c1.metric("Alpha verified", str(manifest.get("alpha_verified", "unknown")).lower())
    else:
        c1.metric("Alpha verified", "n/a")
    c2.metric("Strategies backtested", str(len(summary_rows)) if summary_rows else "n/a")
    c3.metric("Backtest window", f"{window[0]} → {window[1]}" if window else "n/a")
    c4.metric("Release", RELEASE_TAG)

    if not manifest and not summary_rows:
        st.info(f"No backtest artifacts found. {PIPELINE_HINT}")
    else:
        st.success(FINAL_RESULT if not manifest.get("alpha_verified") else "See backtest manifest for the current alpha verdict.")
    st.warning("Research and education only. Not financial advice. No live trading or investment recommendation is implied.")
    st.markdown(
        """
Project CHF is a reproducible crypto quant research system. It tests whether
market and on-chain features can produce cross-sectional alpha after
leakage-safe validation, deterministic portfolio construction, transaction
costs, benchmark sanity checks, and out-of-sample backtesting.
"""
    )
    if allocation:
        st.caption(
            "Selected model: "
            f"`{allocation.get('selected_model_name', 'n/a')}` · horizon "
            f"`{allocation.get('selected_horizon_days', 'n/a')}d` · features "
            f"`{allocation.get('selected_feature_set', 'n/a')}`"
        )
    if alpha.get("best_strategy_by_sharpe"):
        st.caption(f"Best strategy by Sharpe (still not verified alpha): `{alpha['best_strategy_by_sharpe']}`")
    if summary_rows:
        st.subheader("Canonical strategy results (net of costs)")
        st.dataframe(strategy_frame(summary_rows), use_container_width=True, hide_index=True)
    else:
        st.info(f"Strategy results unavailable. {PIPELINE_HINT}")

elif page == "Pipeline Control Center":
    st.header("Pipeline Control Center")
    if READ_ONLY:
        st.info("Read-only demo deployment: pipeline command execution is disabled. "
                "The table below documents the pipeline stages.")
        confirm = False
    else:
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
    results = canonical_results()
    summary_rows = results.get("summary") or []
    window = canonical_window(results)
    st.success(FINAL_RESULT)
    left, right = st.columns(2)
    with left:
        st.subheader("Canonical Strategy Results")
        if summary_rows:
            if window:
                st.write(f"Window: `{window[0]}` to `{window[1]}`")
            st.dataframe(strategy_frame(summary_rows), use_container_width=True, hide_index=True)
        else:
            st.info(f"No canonical backtest summary found. {PIPELINE_HINT}")
    with right:
        st.subheader("Benchmarks")
        bench = benchmark_frame(results)
        if bench is not None and not bench.empty:
            st.dataframe(bench, use_container_width=True, hide_index=True)
            st.caption(
                "Benchmarks are computed by the BacktestAgent for the same window and cost convention. "
                "A 0.00% benchmark indicates a flat placeholder series (prices not loaded for that run)."
            )
        else:
            st.info(f"No benchmark summary found. {PIPELINE_HINT}")

    studies = candidate_studies()
    if studies:
        st.subheader("Frozen May 2026 candidate study (legacy)")
        st.caption(
            "Per-candidate backtests from the frozen May 2026 study — kept for provenance. "
            "The canonical, current results are the tables above."
        )
        for name, frame in studies:
            with st.expander(f"Candidate: {name}"):
                st.dataframe(frame, use_container_width=True, hide_index=True)

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

elif page == "Paper Trading":
    st.header("Paper Trading (Virtual)")
    st.warning(
        "Virtual paper trading — research validation with simulated cash. "
        "Not financial advice. Not live holdings. No verified alpha is implied."
    )
    pt_manifest = load_json_file(DATA / "papertrade" / "papertrade_manifest.json")
    if not pt_manifest:
        st.info("No paper trading data yet — run: `python3 main.py papertrade`")
    else:
        books = pt_manifest.get("books") or {}
        ok_books = {n: b for n, b in books.items() if b.get("status") == "ok"}
        combined_nav = sum(b.get("nav") or 0.0 for b in ok_books.values())
        c1, c2, c3 = st.columns(3)
        c1.metric("Books (ok / total)", f"{len(ok_books)} / {len(books)}")
        c2.metric("Combined virtual NAV (ok books)", f"${combined_nav:,.0f}")
        c3.metric("Last run (UTC)", str(pt_manifest.get("last_run_utc", "n/a"))[:16].replace("T", " "))
        st.caption(f"As of: `{pt_manifest.get('as_of', 'n/a')}` · books update via `python3 main.py papertrade`")

        for name, info in books.items():
            status = info.get("status", "unknown")
            title = f"{name} — {status.upper()}"
            with st.expander(title, expanded=(status == "ok")):
                if status != "ok":
                    st.error(f"Book skipped this run. Reason: `{info.get('skip_reason') or 'unknown'}`")
                book_dir = DATA / "papertrade" / name
                state = load_json_file(book_dir / "state.json") or {}
                c1, c2, c3 = st.columns(3)
                nav = info.get("nav")
                c1.metric("Virtual NAV", f"${nav:,.0f}" if isinstance(nav, (int, float)) else "n/a")
                c2.metric("Virtual cash", f"${state.get('cash'):,.0f}" if isinstance(state.get("cash"), (int, float)) else "n/a")
                c3.metric("Last rebalance", str(state.get("last_rebalance") or "n/a"))

                equity = load_parquet_file(book_dir / "equity.parquet")
                if equity is not None and not equity.empty and {"date", "nav"}.issubset(equity.columns):
                    cum = equity.sort_values("date")["cum_return"].iloc[-1] if "cum_return" in equity.columns else None
                    if cum is not None and pd.notna(cum):
                        st.caption(f"Cumulative return: `{fmt_pct(cum)}` over {len(equity)} day(s)")
                    if len(equity) > 1:
                        st.line_chart(equity.sort_values("date"), x="date", y="nav")
                    else:
                        st.caption("Collecting history — the equity chart appears once the book has more than one daily NAV point.")
                else:
                    st.caption("No equity history recorded for this book yet.")

                positions = state.get("positions") or {}
                if positions:
                    st.write("Positions")
                    st.dataframe(
                        pd.DataFrame([{"Symbol": s, "Quantity": q} for s, q in positions.items()]),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No open virtual positions.")

                fills = load_parquet_file(book_dir / "fills.parquet")
                if fills is not None and not fills.empty:
                    st.write("Recent fills (last 50)")
                    st.dataframe(fills.sort_values("date").tail(50), use_container_width=True, hide_index=True)
                else:
                    st.caption("No fills recorded yet.")

    st.subheader("Scheduler status")
    sched_dir = ROOT / "logs" / "scheduler"
    entries = sorted(sched_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) if sched_dir.exists() else []
    if entries:
        newest = entries[0]
        newest_ts = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
        st.write(f"Latest scheduler log: `{rel(newest)}` (modified {newest_ts.strftime('%Y-%m-%d %H:%M UTC')})")
    else:
        st.info("No scheduler logs found under `logs/scheduler/` — the scheduler has not run recently on this machine.")
    st.caption(
        "Configured schedule (jobs/scheduler.py): paper trading runs daily at 13:00 UTC "
        "(cron `0 13 * * *`) via `python3 main.py papertrade`. Start locally with "
        "`python3 main.py schedule --config configs/run_config.yaml`."
    )

elif page == "Fund Operations":
    st.header("Fund Operations (Virtual)")
    st.warning(
        "Institutional fund-operations layers applied to VIRTUAL paper books: "
        "simulated cash and simulated fees. Not live holdings. Not financial advice. "
        "The research verdict remains `alpha_verified=false`."
    )

    # ---- Dual-book reconciliation -------------------------------------------------
    st.subheader("Dual-book reconciliation")
    rec = load_json_file(DATA / "accounting" / "reconciliation_report.json")
    if not rec:
        st.info("No reconciliation report yet — run `python3 main.py nav`.")
    else:
        meta = rec.get("_meta") or {}
        book_rows = [
            {
                "Book": name,
                "Status": r.get("status"),
                "Divergence (bps)": fmt_num(r.get("divergence_bps"), 4),
                "Max divergence (bps)": fmt_num(r.get("max_divergence_bps"), 4),
                "Days OK": f"{r.get('days_ok', '—')}/{r.get('days_checked', '—')}",
                "Break class": r.get("break_classification") or "—",
            }
            for name, r in sorted(rec.items())
            if name != "_meta" and isinstance(r, dict)
        ]
        n_ok = sum(1 for r in book_rows if r["Status"] == "OK")
        c1, c2, c3 = st.columns(3)
        c1.metric("Books reconciled OK", f"{n_ok} / {len(book_rows)}")
        c2.metric("Tolerance (bps)", fmt_num(meta.get("tolerance_bps"), 1))
        c3.metric("Report generated (UTC)", str(meta.get("generated_utc", "n/a"))[:16].replace("T", " "))
        st.dataframe(pd.DataFrame(book_rows), use_container_width=True, hide_index=True)

    # ---- Net-of-fees NAV ----------------------------------------------------------
    st.subheader("Independent NAV — gross vs net of fees")
    nav_files = sorted((DATA / "accounting").glob("nav_*.parquet")) if (DATA / "accounting").exists() else []
    if not nav_files:
        st.info("No accounting NAV series yet — run `python3 main.py nav`.")
    else:
        nav_rows = []
        for p in nav_files:
            nav = load_parquet_file(p)
            if nav is None or nav.empty or "nav" not in nav.columns:
                continue
            nav = nav.sort_values("date")
            last = nav.iloc[-1]
            nav_rows.append(
                {
                    "Book": p.stem.replace("nav_", ""),
                    "As of": str(last.get("date"))[:10],
                    "Gross NAV": f"${float(last['nav']):,.2f}",
                    "Net NAV": f"${float(last['net_nav']):,.2f}" if "net_nav" in nav.columns else "—",
                    "Mgmt fees (cum)": f"${float(last['mgmt_fee_cum']):,.2f}" if "mgmt_fee_cum" in nav.columns else "—",
                    "Perf fees (cum)": f"${float(last['perf_fee_cum']):,.2f}" if "perf_fee_cum" in nav.columns else "—",
                    "Days": len(nav),
                }
            )
        if nav_rows:
            st.dataframe(pd.DataFrame(nav_rows), use_container_width=True, hide_index=True)
            st.caption("Simulated 2%/20% fee schedule from `configs/accounting.yaml`, applied to virtual NAVs for realism only.")
        else:
            st.info("Accounting NAV files exist but could not be read.")

    # ---- Risk governance ----------------------------------------------------------
    st.subheader("Risk governance")
    risk_dir = DATA / "risk"
    audit_files = sorted(risk_dir.glob("risk_audit_*.json")) if risk_dir.exists() else []
    dd_files = sorted(risk_dir.glob("drawdown_state_*.json")) if risk_dir.exists() else []
    if not audit_files and not dd_files:
        st.info("No risk pipeline artifacts yet under `data/risk/`.")
    else:
        left, right = st.columns(2)
        with left:
            st.write("Latest risk audits")
            audit_rows = []
            for p in audit_files:
                a = load_json_file(p) or {}
                mult = a.get("multipliers") or {}
                audit_rows.append(
                    {
                        "Book": a.get("book") or p.stem.replace("risk_audit_", ""),
                        "As of": a.get("as_of", "—"),
                        "Vol-target mult": fmt_num(mult.get("vol_target"), 3),
                        "Drawdown mult": fmt_num(mult.get("drawdown"), 3),
                        "Final gross": fmt_pct(a.get("final_gross_exposure")),
                        "Cash": fmt_pct(a.get("final_cash_weight")),
                        "Positions": len(a.get("final_weights") or {}),
                    }
                )
            if audit_rows:
                st.dataframe(pd.DataFrame(audit_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No risk audit files.")
        with right:
            st.write("Drawdown states")
            dd_rows = []
            for p in dd_files:
                d = load_json_file(p) or {}
                last_nav, peak_nav = d.get("last_nav"), d.get("peak_nav")
                dd = (float(last_nav) / float(peak_nav) - 1.0) if last_nav and peak_nav else None
                dd_rows.append(
                    {
                        "Book": p.stem.replace("drawdown_state_", ""),
                        "State": d.get("state", "—"),
                        "Since": d.get("state_entered_date", "—"),
                        "Drawdown": fmt_pct(dd) if dd is not None else "—",
                    }
                )
            if dd_rows:
                st.dataframe(pd.DataFrame(dd_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No drawdown state files.")

    # ---- Regime & sleeves ---------------------------------------------------------
    st.subheader("Regime & sleeve allocation")
    regime = load_json_file(DATA / "strategies" / "regime_latest.json")
    if regime:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Regime", str(regime.get("regime", "n/a")))
        c2.metric("As of", str(regime.get("date", "n/a")))
        c3.metric("BTC trend", "up" if regime.get("btc_trend_up") else "down")
        fg = regime.get("fear_greed_value")
        c4.metric("Fear & Greed", f"{fg:.0f} ({regime.get('fear_greed_bucket', '—')})" if isinstance(fg, (int, float)) else "n/a")
        st.caption(
            f"Breadth above 50d MA: {fmt_pct(regime.get('breadth_pct_above_50d_ma'))} · "
            f"avg pairwise corr (60d, top 20): {fmt_num(regime.get('avg_pairwise_corr_60d_top20'), 2)} · "
            f"BTC dominance: {regime.get('btc_dominance_trend', '—')}"
        )
    else:
        st.info("No regime artifact yet under `data/strategies/`.")
    proposal = load_json_file(DATA / "strategies" / "sleeve_allocation_proposal.json")
    if proposal:
        approved = bool(proposal.get("approved", False))
        status_txt = "APPROVED" if approved else "PROPOSAL — not approved, never auto-executed"
        st.write(
            f"Sleeve allocation **{status_txt}** · as of `{proposal.get('as_of', 'n/a')}` · "
            f"regime `{proposal.get('regime', 'n/a')}` · proposed cash {fmt_pct(proposal.get('cash_weight'))}"
        )
        pt_manifest = load_json_file(DATA / "papertrade" / "papertrade_manifest.json") or {}
        pt_books = pt_manifest.get("books") or {}
        sleeve_rows = [
            {
                "Sleeve": name,
                "Base weight": fmt_pct(info.get("base_weight")),
                "Proposed weight": fmt_pct(info.get("proposed_weight")),
                "Sortino (90d)": fmt_num(info.get("sortino_90d"), 2),
                "Return source": info.get("return_source", "—"),
                "Paper NAV": f"${(pt_books.get(name) or {}).get('nav'):,.0f}"
                             if isinstance((pt_books.get(name) or {}).get("nav"), (int, float)) else "—",
            }
            for name, info in sorted((proposal.get("sleeves") or {}).items())
        ]
        st.dataframe(pd.DataFrame(sleeve_rows), use_container_width=True, hide_index=True)
        if proposal.get("note"):
            st.caption(proposal["note"])
    else:
        st.info("No sleeve allocation proposal yet under `data/strategies/`.")

    # ---- Monitoring ---------------------------------------------------------------
    st.subheader("Monitoring")
    mon_dir = DATA / "reports" / "monitoring"
    monitor_names = [
        "data_quality", "signal_health", "execution_quality", "model_decay",
        "risk_report", "shadow_nav", "watchdog", "champion_challenger",
    ]
    mod_rows, alert_rows = [], []
    for name in monitor_names:
        rep = load_json_file(mon_dir / f"{name}.json")
        if rep is None:
            continue
        status = str(rep.get("status", "unknown"))
        alerts = [str(a) for a in (rep.get("alerts") or [])]
        mod_rows.append({"Module": name, "Status": status, "Alerts": len(alerts)})
        severity = "alert" if status == "alert" else ("warn" if status == "warn" else "info")
        for msg in alerts:
            alert_rows.append({"Module": name, "Severity": severity, "Message": msg})
    if not mod_rows:
        st.info("No monitoring reports yet — run `python3 main.py monitor`.")
    else:
        verdict = load_json_file(DATA / "readiness" / "daily_quality_verdict.json") or {}
        n_alerting = sum(1 for r in mod_rows if r["Status"] == "alert")
        c1, c2, c3 = st.columns(3)
        c1.metric("Modules reporting", str(len(mod_rows)))
        c2.metric("Modules alerting", str(n_alerting))
        c3.metric("Daily quality verdict", str(verdict.get("status", "n/a")))
        st.dataframe(pd.DataFrame(mod_rows), use_container_width=True, hide_index=True)
        if alert_rows:
            st.write(f"Open alerts ({len(alert_rows)})")
            st.dataframe(pd.DataFrame(alert_rows), use_container_width=True, hide_index=True)
        else:
            st.success("No open monitoring alerts.")
    st.caption(
        "Fund operations update via `python3 main.py nav` (accounting), "
        "`python3 main.py monitor` (monitoring suite) and `python3 main.py letter` "
        "(monthly letter under `artifacts/letters/`)."
    )

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
