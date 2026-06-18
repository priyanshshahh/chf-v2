#!/usr/bin/env bash
# Project CHF React product dashboard launcher.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "============================================"
echo "  Project CHF — React Product MVP Dashboard"
echo "============================================"
echo ""

if ! command -v npm &>/dev/null; then
    echo "ERROR: npm not found. Install Node.js/npm first."
    exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "ERROR: frontend/node_modules is missing."
    echo "Install frontend dependencies first:"
    echo "  cd frontend"
    echo "  npm install"
    exit 1
fi

cd "$FRONTEND_DIR"

echo "Starting React dashboard at http://127.0.0.1:5173"
echo "This does not run the Python pipeline or touch research outputs."
echo ""
npm run dev
