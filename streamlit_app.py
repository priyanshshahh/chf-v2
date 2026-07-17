"""Streamlit Community Cloud entry point for the CHF research dashboard.

Deploys the dashboard as a read-only, offline demo:

- No API keys or secrets are required (the dashboard never calls external
  APIs; `configs/config.py` tolerates a missing .env).
- Pipeline command execution is disabled via CHF_DASHBOARD_READ_ONLY=1,
  per the security note in app/dashboard.py.
- Generated artifacts under data/ are gitignored, so a fresh cloud checkout
  has none. On first boot this entry point generates the synthetic demo
  artifacts (same as `python main.py demo`) before starting the dashboard.
  On a local machine with real research outputs, the sentinel check below
  means nothing is overwritten.

Streamlit Community Cloud setup: point the app's "Main file path" at this
file. Local equivalent: `streamlit run streamlit_app.py`.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Cloud deployments are read-only demos: never execute pipeline commands.
os.environ.setdefault("CHF_DASHBOARD_READ_ONLY", "1")

# Sentinel: the last artifact `main.generate_demo_artifacts` writes. If any
# backtest summary exists (demo or real), leave data/ untouched.
_SENTINEL = ROOT / "data" / "backtests" / "backtest_summary.parquet"
if not _SENTINEL.exists():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from configs.config import load_config
    from main import generate_demo_artifacts

    generate_demo_artifacts(load_config())

runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
