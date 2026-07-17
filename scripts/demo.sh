#!/usr/bin/env bash
# =============================================================================
# CHF — One-command end-to-end demo
# =============================================================================
# Runs the CHF system on the LOCAL data that already ships in this repo. No API
# keys, no network, no external services required. Safe to run repeatedly
# (idempotent): every stage reads committed research artifacts and only appends
# to the local virtual paper-trading books / NAV ledgers.
#
# What it does, in order:
#   1. Prints the honest research verdict (alpha_verified) from the canonical
#      backtest manifest.
#   2. main.py papertrade  — advances every virtual paper book one cycle.
#   3. main.py nav         — dual-book accounting reconciliation + independent NAV.
#   4. main.py monitor     — ops monitors (data quality, model decay, risk, ...).
#                            NOTE: monitor exits non-zero when it raises alerts;
#                            that is expected on a research system with stale
#                            research data and is NOT a demo failure.
#   5. main.py letter      — generates the institutional monthly letter.
#   6. Points you at the dashboards.
#
# Usage:   ./scripts/demo.sh
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Prefer the project venv; fall back to python3 on PATH.
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
CFG="configs/run_config.yaml"

hr()     { printf '%s\n' "----------------------------------------------------------------------"; }
banner() { hr; printf '  %s\n' "$1"; hr; }

banner "CHF — end-to-end local demo (no API keys required)"
echo "Python:  $PY"
echo "Root:    $ROOT"
echo ""

# ---------------------------------------------------------------------------
# 1. Research verdict — the honest headline
# ---------------------------------------------------------------------------
banner "1/6  Research verdict (single alpha authority: BacktestAgent)"
"$PY" - <<'PYEOF'
import json, pathlib
mpath = pathlib.Path("data/backtests/backtest_manifest.json")
if not mpath.exists():
    print("  (no canonical backtest manifest found; run `python main.py backtest` first)")
else:
    m = json.loads(mpath.read_text())
    verdict = m.get("alpha_verified")
    print(f"  alpha_verified          = {verdict}")
    print(f"  signal_gate_passed      = {m.get('signal_gate_passed')}")
    print(f"  benchmark_sanity_passed = {m.get('benchmark_sanity_passed')}")
    print(f"  transaction_cost_bps    = {m.get('transaction_cost_bps')}")
    print("")
    if verdict is False:
        print("  >> No verified alpha under tested configurations. This is the honest,")
        print("     expected result — CHF refuses to claim edge it cannot prove.")
    elif verdict is True:
        print("  >> Alpha VERIFIED by the backtest authority (re-check the evidence).")
PYEOF
echo ""

# ---------------------------------------------------------------------------
# 2. Paper trading — advance the virtual books
# ---------------------------------------------------------------------------
banner "2/6  Paper trading  (main.py papertrade)"
"$PY" main.py papertrade --config "$CFG" || {
  echo "  [demo] papertrade returned non-zero (see output above)"; }
echo ""

# ---------------------------------------------------------------------------
# 3. Accounting — reconciliation + independent NAV
# ---------------------------------------------------------------------------
banner "3/6  Accounting: dual-book reconciliation + independent NAV  (main.py nav)"
"$PY" main.py nav || {
  echo "  [demo] nav reported a reconciliation break (see data/accounting/reconciliation_report.json)"; }
echo ""

# ---------------------------------------------------------------------------
# 4. Monitoring — ops alerts (non-zero exit on alert is EXPECTED)
# ---------------------------------------------------------------------------
banner "4/6  Monitoring: ops alerts  (main.py monitor)"
if "$PY" main.py monitor; then
  echo "  [demo] all monitors clean."
else
  echo "  [demo] monitor raised alerts (expected on stale research data — not a demo failure)."
fi
echo ""

# ---------------------------------------------------------------------------
# 5. Institutional monthly letter
# ---------------------------------------------------------------------------
banner "5/6  Monthly investor letter  (main.py letter)"
"$PY" main.py letter || { echo "  [demo] letter generation returned non-zero"; }
LATEST_LETTER="$(ls -t artifacts/letters/*.md 2>/dev/null | head -1 || true)"
[ -n "${LATEST_LETTER:-}" ] && echo "  Latest letter: $LATEST_LETTER"
echo ""

# ---------------------------------------------------------------------------
# 6. Dashboards
# ---------------------------------------------------------------------------
banner "6/6  Dashboards"
cat <<'EOF'
  Research dashboard (Streamlit):   ./run_dashboard.sh          -> http://localhost:8501
  Product dashboard  (Streamlit):   ./run_product_dashboard.sh  -> http://localhost:8501
  Static site (baked JSON):         cd frontend && npm run bake && npm run dev
  FastAPI service:                  python main.py serve         -> http://localhost:8000
EOF
echo ""
banner "Demo complete."
echo "  Verdict stands: see section 1. Paper books + NAV + letter refreshed above."
