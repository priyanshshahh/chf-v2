#!/usr/bin/env python3
"""
CHF Bootstrap Script
====================
Sets up the project environment for first-time use on a clean machine.

What it does:
  1. Creates all required data directories
  2. Copies .env.example to .env (if .env does not exist)
  3. Verifies all required Python packages are importable
  4. Verifies all project modules are importable
  5. Prints a summary of what is ready and what is missing

Usage:
  python scripts/bootstrap.py
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_DIRS = [
    "data/raw/market",
    "data/raw/universe",
    "data/raw/onchain",
    "data/staged",
    "data/cleaned",
    "data/features",
    "data/labels",
    "data/predictions",
    "data/allocations",
    "data/backtests",
    "data/reports",
    "artifacts",
    "metadata",
    "mlruns",
    "logs",
    "docs",
]

REQUIRED_PACKAGES = [
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("scikit-learn", "sklearn"),
    ("lightgbm", "lightgbm"),
    ("streamlit", "streamlit"),
    ("plotly", "plotly"),
    ("mlflow", "mlflow"),
    ("vectorbt", "vectorbt"),
    ("numba", "numba"),
    ("pyarrow", "pyarrow"),
    ("pydantic", "pydantic"),
    ("requests", "requests"),
    ("ccxt", "ccxt"),
    ("apscheduler", "apscheduler"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("duckdb", "duckdb"),
    ("scipy", "scipy"),
    ("yaml", "yaml"),
    ("dotenv", "dotenv"),
]

PROJECT_MODULES = [
    "configs.config",
    "agents.base",
    "agents.universe_agent",
    "agents.market_data_agent",
    "agents.onchain_agent",
    "agents.feature_agent",
    "agents.label_agent",
    "agents.model_agent",
    "agents.portfolio_agent",
    "agents.backtest_agent",
    "features.feature_engineering",
    "models.walk_forward",
    "models.ablation",
    "pipelines.pipeline_runner",
    "pipelines.duckdb_engine",
    "jobs.scheduler",
    "app.api",
    "app.dashboard",
]


def check_dirs() -> list:
    """Create all required directories and return list of created ones."""
    created = []
    for d in REQUIRED_DIRS:
        p = PROJECT_ROOT / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(str(p))
    return created


def check_env() -> bool:
    """Copy .env.example to .env if .env does not exist."""
    env_path = PROJECT_ROOT / ".env"
    example_path = PROJECT_ROOT / ".env.example"
    if not env_path.exists():
        if example_path.exists():
            shutil.copy(example_path, env_path)
            print(f"  [OK] Copied .env.example -> .env")
            print(f"       Edit {env_path} to add your API keys.")
            return True
        else:
            print(f"  [WARN] .env.example not found. Creating empty .env.")
            env_path.touch()
            return True
    return True


def check_packages() -> tuple:
    """Check all required packages are importable."""
    ok = []
    missing = []
    for pkg_name, import_name in REQUIRED_PACKAGES:
        try:
            mod = importlib.import_module(import_name)
            version = getattr(mod, "__version__", "?")
            ok.append((pkg_name, version))
        except ImportError:
            missing.append(pkg_name)
    return ok, missing


def check_modules() -> tuple:
    """Check all project modules are importable."""
    ok = []
    failed = []
    for mod_name in PROJECT_MODULES:
        try:
            importlib.import_module(mod_name)
            ok.append(mod_name)
        except Exception as e:
            failed.append((mod_name, str(e)))
    return ok, failed


def main():
    print("=" * 60)
    print("CHF Bootstrap")
    print("=" * 60)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Python       : {sys.version}")
    print()

    # 1. Directories
    print("[1/4] Creating data directories...")
    created = check_dirs()
    if created:
        for d in created:
            print(f"  [CREATED] {d}")
    else:
        print("  [OK] All directories already exist.")
    print()

    # 2. .env
    print("[2/4] Checking .env file...")
    check_env()
    print()

    # 3. Packages
    print("[3/4] Checking Python packages...")
    ok_pkgs, missing_pkgs = check_packages()
    for name, version in ok_pkgs:
        print(f"  [OK] {name} ({version})")
    for name in missing_pkgs:
        print(f"  [MISSING] {name} — install with: pip install {name}")
    print()

    # 4. Project modules
    print("[4/4] Checking project modules...")
    ok_mods, failed_mods = check_modules()
    for name in ok_mods:
        print(f"  [OK] {name}")
    for name, err in failed_mods:
        print(f"  [FAIL] {name}: {err}")
    print()

    # Summary
    print("=" * 60)
    print("BOOTSTRAP SUMMARY")
    print("=" * 60)
    print(f"  Packages OK     : {len(ok_pkgs)}/{len(REQUIRED_PACKAGES)}")
    print(f"  Packages missing: {len(missing_pkgs)}")
    print(f"  Modules OK      : {len(ok_mods)}/{len(PROJECT_MODULES)}")
    print(f"  Modules failed  : {len(failed_mods)}")
    print()

    if missing_pkgs:
        print("To install missing packages:")
        print(f"  pip install {' '.join(missing_pkgs)}")
        print()

    if failed_mods:
        print("Failed modules (check for import errors above):")
        for name, err in failed_mods:
            print(f"  {name}: {err}")
        print()
        sys.exit(1)
    else:
        print("Bootstrap complete. Ready to run!")
        print()
        print("Quick start:")
        print("  python main.py demo        # Generate demo data")
        print("  streamlit run app/dashboard.py  # Launch dashboard")
        print("  python main.py full        # Run live pipeline (needs API keys)")


if __name__ == "__main__":
    main()
