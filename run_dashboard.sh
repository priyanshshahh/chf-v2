#!/bin/bash
# CHF Dashboard Launcher
# Usage: ./run_dashboard.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  CHF — Crypto Hedge Fund Dashboard"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Check streamlit
if ! python3 -c "import streamlit" &>/dev/null; then
    echo "Installing streamlit..."
    pip3 install streamlit --quiet
fi

# Set PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

echo "Starting Streamlit dashboard..."
echo "URL: http://localhost:8501"
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
