#!/usr/bin/env bash
# Project CHF supervised scheduler loop.
#
# A dependency-free supervisor for machines without Docker or systemd: it runs
# the scheduler and restarts it on crash with exponential backoff (capped),
# resetting the backoff once a run has been healthy for a while.
#
# Usage:
#   ./deploy/run_supervised.sh
#   PYTHON=/opt/chf/.venv/bin/python ./deploy/run_supervised.sh   # custom interp
#
# Stop with Ctrl+C (SIGINT/SIGTERM are forwarded to the child).
set -u

# Resolve repo root (this script lives in <root>/deploy).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

# Interpreter: prefer the project venv python. NEVER use .venv/bin/pip here — it
# is broken in this project (targets a python3.13 lib dir while the interpreter
# is 3.11). See deploy/README.md.
PYTHON="${PYTHON:-${ROOT}/.venv/bin/python}"
if [ ! -x "${PYTHON}" ]; then
  PYTHON="$(command -v python3 || command -v python)"
fi

# Load .env if present so SMTP_*/WEBHOOK_URL/API keys reach the scheduler.
if [ -f "${ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${ROOT}/.env"
  set +a
fi
export TZ="${TZ:-UTC}"
export PYTHONUNBUFFERED=1

LOG_DIR="${ROOT}/logs/scheduler"
mkdir -p "${LOG_DIR}"
SUP_LOG="${LOG_DIR}/supervisor.log"

MIN_BACKOFF=5           # seconds
MAX_BACKOFF=300         # seconds cap
HEALTHY_SECONDS=60      # a run lasting this long is "healthy" -> reset backoff
backoff="${MIN_BACKOFF}"

CHILD_PID=""
terminate() {
  echo "$(date -u +%FT%TZ) supervisor: caught signal, stopping child ${CHILD_PID}" | tee -a "${SUP_LOG}"
  if [ -n "${CHILD_PID}" ]; then
    kill -TERM "${CHILD_PID}" 2>/dev/null || true
    wait "${CHILD_PID}" 2>/dev/null || true
  fi
  exit 0
}
trap terminate INT TERM

echo "$(date -u +%FT%TZ) supervisor: starting; python=${PYTHON}" | tee -a "${SUP_LOG}"

while true; do
  start_ts=$(date +%s)
  echo "$(date -u +%FT%TZ) supervisor: launching scheduler" | tee -a "${SUP_LOG}"

  "${PYTHON}" -m jobs.scheduler &
  CHILD_PID=$!
  wait "${CHILD_PID}"
  rc=$?
  CHILD_PID=""

  end_ts=$(date +%s)
  ran=$(( end_ts - start_ts ))

  if [ "${rc}" -eq 0 ]; then
    echo "$(date -u +%FT%TZ) supervisor: scheduler exited cleanly (rc=0); stopping" | tee -a "${SUP_LOG}"
    exit 0
  fi

  if [ "${ran}" -ge "${HEALTHY_SECONDS}" ]; then
    backoff="${MIN_BACKOFF}"   # was healthy long enough; reset backoff
  fi

  echo "$(date -u +%FT%TZ) supervisor: scheduler crashed (rc=${rc}, ran=${ran}s); restarting in ${backoff}s" | tee -a "${SUP_LOG}"
  sleep "${backoff}"

  backoff=$(( backoff * 2 ))
  if [ "${backoff}" -gt "${MAX_BACKOFF}" ]; then
    backoff="${MAX_BACKOFF}"
  fi
done
