#!/usr/bin/env bash
# CHF — Full Pipeline Runner
# ===========================
# Runs the entire pipeline from data collection to backtest.
# Usage: ./run_all.sh [--demo]
#
# Options:
#   --demo    Use synthetic demo data instead of live API calls

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "CHF — Full Pipeline Runner"
echo "========================================"
echo "Working directory: $SCRIPT_DIR"
echo "Python: $(python3 --version)"
echo ""

DEMO_MODE=false
for arg in "$@"; do
    if [ "$arg" = "--demo" ]; then
        DEMO_MODE=true
    fi
done

if [ "$DEMO_MODE" = true ]; then
    echo "[1/1] Generating demo data..."
    python3 main.py demo
    echo ""
    echo "Demo data generated. Launch dashboard with:"
    echo "  streamlit run app/dashboard.py"
    exit 0
fi

echo "[1/8] Running UniverseAgent..."
python3 main.py universe
python3 scripts/verify_universe_run.py
echo ""

echo "[2/8] Running MarketDataAgent..."
python3 main.py market
python3 scripts/verify_market_run.py
echo ""

echo "[3/8] Running OnChainAgent..."
python3 scripts/verify_market_run.py
python3 main.py onchain
python3 scripts/verify_onchain_run.py
echo ""

echo "[4/8] Running FeatureAgent..."
python3 main.py features
python3 scripts/verify_feature_run.py
echo ""

echo "[5/8] Running LabelAgent..."
python3 scripts/verify_feature_run.py
python3 main.py labels
python3 scripts/verify_label_run.py
echo ""

echo "[6/8] Running ModelAgent..."
python3 scripts/verify_label_run.py
python3 main.py model
python3 scripts/verify_model_run.py
echo ""

echo "[7/8] Running PortfolioAgent..."
python3 main.py portfolio
python3 scripts/verify_portfolio_run.py
echo ""

echo "[8/8] Running BacktestAgent..."
python3 main.py backtest
python3 scripts/verify_backtest_run.py
echo ""

echo "========================================"
echo "Pipeline complete!"
echo ""
echo "Next steps:"
echo "  streamlit run app/dashboard.py   # Launch dashboard"
echo "  mlflow ui --backend-store-uri mlruns --port 5000  # MLflow UI"
echo "========================================"
