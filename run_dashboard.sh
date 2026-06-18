#!/usr/bin/env bash
# Project CHF read-only research dashboard launcher.
# Usage: ./run_dashboard.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  Project CHF — Research Dashboard"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Check streamlit
if ! python3 -c "import streamlit" &>/dev/null; then
    echo "ERROR: streamlit is not installed in this Python environment."
    echo "Install project dependencies first:"
    echo "  python3 -m pip install -r requirements.txt"
    exit 1
fi

# Set PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

echo "Starting read-only Streamlit dashboard..."
echo "URL: http://localhost:8501"
echo "This dashboard does not rerun research or require API secrets."
echo ""
echo "Press Ctrl+C to stop."
echo ""

python3 -m streamlit run app/dashboard.py \
    --server.port 8501 \
    --server.address localhost \
    --server.headless false \
    --browser.gatherUsageStats false \
    --theme.base dark \
    --theme.primaryColor "#00d4aa"
