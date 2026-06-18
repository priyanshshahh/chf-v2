#!/usr/bin/env bash
# Project CHF local scheduler launcher.
# Runs until the terminal is stopped.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  Project CHF — Local Scheduler"
echo "============================================"
echo "Runs scheduled local pipeline commands until stopped."
echo "This does not run BacktestAgent daily by default."
echo ""

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

if ! python3 -c "import apscheduler" &>/dev/null; then
    echo "ERROR: APScheduler is not installed."
    echo "Install project dependencies first:"
    echo "  python3 -m pip install -r requirements.txt"
    exit 1
fi

export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

echo "Starting scheduler. Press Ctrl+C to stop."
python3 jobs/scheduler.py
