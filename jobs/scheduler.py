"""
Project CHF local scheduler.

The scheduler calls existing CLI entrypoints through subprocess. It does not
implement research logic and is disabled unless explicitly started.

Hardening:
- bounded retry (2 retries, exponential backoff) around every subprocess call
- EVENT_JOB_ERROR / EVENT_JOB_MISSED listeners appended to
  logs/scheduler/failures.jsonl
- per-job heartbeat written to logs/scheduler/heartbeat.json after each run
- operational monitoring jobs (monitoring/ package); BacktestAgent remains
  intentionally excluded / manual-only.
- daily collector jobs for the new data sources (derivatives_okx/bybit/coinglass,
  defillama, macro_fred) at 05:00 UTC, ahead of market ingestion.
- deduplicated alert delivery via monitoring/notifier.py when a monitor reports
  an alert or a job errors/misses (last-sent hashes in
  logs/scheduler/alert_state.json prevent per-run spam).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "scheduler"
FAILURES_PATH = LOG_DIR / "failures.jsonl"
HEARTBEAT_PATH = LOG_DIR / "heartbeat.json"
ALERT_STATE_PATH = LOG_DIR / "alert_state.json"
MONITORING_REPORTS_DIR = ROOT / "data" / "reports" / "monitoring"

MAX_RETRIES = 2                # total attempts = 1 + MAX_RETRIES
BACKOFF_BASE_SECONDS = 30.0    # 30s, then 60s

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chf.scheduler")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_failure(record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(FAILURES_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def _write_heartbeat(job: str, last_start: str, last_success: str | None, last_exit_code: int | None) -> None:
    """Persist {job, last_start, last_success, last_exit_code} per job."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    doc: dict = {"updated_utc": _utcnow_iso(), "jobs": {}}
    if HEARTBEAT_PATH.exists():
        try:
            with open(HEARTBEAT_PATH, "r") as f:
                existing = json.load(f)
            if isinstance(existing.get("jobs"), dict):
                doc["jobs"] = existing["jobs"]
        except (json.JSONDecodeError, OSError):
            pass
    prev = doc["jobs"].get(job, {})
    doc["jobs"][job] = {
        "job": job,
        "last_start": last_start,
        "last_success": last_success if last_success is not None else prev.get("last_success"),
        "last_exit_code": last_exit_code,
    }
    tmp = HEARTBEAT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
    tmp.replace(HEARTBEAT_PATH)


def run_cli(
    label: str,
    command: list[str],
    ok_returncodes: Iterable[int] = (0,),
    alert_returncodes: Iterable[int] = (),
) -> int:
    """Run a supported CHF CLI command with bounded retry; raise on failure.

    ``alert_returncodes`` are accepted (monitoring modules exit 1 to signal an
    operational alert, which is a result, not a job failure) but logged.

    Returns the accepted return code (0 for success, or the alert code) so the
    caller can react to an alert; raises RuntimeError only on genuine failure.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ok_codes: Tuple[int, ...] = tuple(ok_returncodes)
    alert_codes: Tuple[int, ...] = tuple(alert_returncodes)
    accepted = set(ok_codes) | set(alert_codes)
    started = _utcnow_iso()
    last_rc: int | None = None

    for attempt in range(1 + MAX_RETRIES):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = LOG_DIR / f"{stamp}_{label}.log"
        logger.info("Starting %s (attempt %d/%d): %s", label, attempt + 1, 1 + MAX_RETRIES, " ".join(command))
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        last_rc = proc.returncode
        log_path.write_text(
            "\n".join(
                [
                    f"label: {label}",
                    f"command: {' '.join(command)}",
                    f"attempt: {attempt + 1}",
                    f"returncode: {proc.returncode}",
                    "",
                    "STDOUT:",
                    proc.stdout,
                    "",
                    "STDERR:",
                    proc.stderr,
                ]
            ),
            encoding="utf-8",
        )
        if proc.returncode in accepted:
            if proc.returncode in alert_codes and proc.returncode not in ok_codes:
                logger.warning("%s completed with ALERT (exit %d). Log: %s", label, proc.returncode, log_path)
            else:
                logger.info("%s completed. Log: %s", label, log_path)
            _write_heartbeat(label, started, _utcnow_iso(), proc.returncode)
            return proc.returncode
        logger.error("%s failed with return code %d. See %s", label, proc.returncode, log_path)
        if attempt < MAX_RETRIES:
            delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            logger.info("Retrying %s in %.0fs", label, delay)
            time.sleep(delay)

    _write_heartbeat(label, started, None, last_rc)
    _append_failure(
        {
            "ts_utc": _utcnow_iso(),
            "event": "job_failed_after_retries",
            "job": label,
            "command": command,
            "returncode": last_rc,
            "attempts": 1 + MAX_RETRIES,
        }
    )
    raise RuntimeError(f"{label} failed with return code {last_rc} after {1 + MAX_RETRIES} attempts")


# ---------------------------------------------------------------------------
# alert delivery + deduplication
# ---------------------------------------------------------------------------
# Alerts are delivered through monitoring/notifier.py (deterministic, no LLM).
# To avoid spamming the same alert every scheduler run we track a per-alert
# last-sent timestamp in logs/scheduler/alert_state.json and suppress repeats
# inside the notifier's dedup_ttl_hours window.
def _load_alert_state() -> Dict[str, str]:
    if ALERT_STATE_PATH.exists():
        try:
            with open(ALERT_STATE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_alert_state(state: Dict[str, str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(ALERT_STATE_PATH)


def _dedup_key(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _dedup_ttl_hours() -> float:
    try:
        from monitoring import notifier

        cfg = notifier.load_config(ROOT)
        return float(cfg["notifications"].get("dedup_ttl_hours", 12))
    except Exception:  # noqa: BLE001 - never let config issues break alerting
        return 12.0


def _should_send(key: str, ttl_hours: float) -> bool:
    last = _load_alert_state().get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
    return age_hours >= ttl_hours


def _mark_sent(key: str) -> None:
    state = _load_alert_state()
    state[key] = _utcnow_iso()
    _save_alert_state(state)


def _load_monitor_report(module: str) -> Optional[Dict[str, Any]]:
    """Load a monitor's JSON report (data/reports/monitoring/<name>.json)."""
    name = module.split(".")[-1]
    path = MONITORING_REPORTS_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _failing_check_names(report: Dict[str, Any]) -> List[str]:
    bad = []
    for c in report.get("checks", []) or []:
        if isinstance(c, dict) and str(c.get("status", "")).lower() in ("warn", "fail", "critical", "error"):
            bad.append(str(c.get("name", "?")))
    return sorted(bad)


def _notify_monitor_alert(label: str, module: str) -> None:
    """Dispatch a deduplicated notification when a monitor reports an alert."""
    try:
        from monitoring import notifier
    except Exception as exc:  # noqa: BLE001 - missing notifier must not crash the job
        logger.error("notifier import failed; alert for %s logged only: %s", label, exc)
        return

    name = module.split(".")[-1]
    report = _load_monitor_report(module)
    if report is None:
        report = {
            "monitor": name,
            "status": "fail",
            "summary": "monitor exited with an alert but its JSON report was unavailable",
        }
    key = _dedup_key("monitor", name, report.get("status"), *_failing_check_names(report))
    ttl = _dedup_ttl_hours()
    if not _should_send(key, ttl):
        logger.info("notifier: duplicate alert for %s suppressed (dedup, ttl=%.0fh)", name, ttl)
        return
    try:
        result = notifier.notify_report(report, monitor=name, root=ROOT)
    except Exception as exc:  # noqa: BLE001 - delivery must never crash the scheduler
        logger.error("notifier: dispatch for %s failed: %s", name, exc)
        return
    # only record as sent when it was actually attempted (not skipped by config/severity)
    if not result.get("skipped"):
        _mark_sent(key)


def run_monitor(label: str, module: str) -> None:
    """Run a monitoring module; exit code 1 means alert, not failure.

    On an alert exit code the monitor's report is loaded and a deduplicated
    notification is dispatched via monitoring/notifier.py.
    """
    rc = run_cli(label, ["python3", "-m", module], ok_returncodes=(0,), alert_returncodes=(1,))
    if rc == 1:
        _notify_monitor_alert(label, module)


def start_scheduler() -> None:
    try:
        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise SystemExit("APScheduler is missing. Install dependencies with: python3 -m pip install -r requirements.txt") from exc

    scheduler = BlockingScheduler(timezone="UTC")

    jobs = [
        ("universe", "UniverseAgent monthly", ["python3", "main.py", "universe", "--config", "configs/run_config.yaml"], "0 2 1 * *"),
        ("market", "MarketDataAgent daily", ["python3", "main.py", "market", "--config", "configs/run_config.yaml"], "0 6 * * *"),
        ("onchain", "OnChainAgent daily", ["python3", "main.py", "onchain", "--config", "configs/run_config.yaml"], "0 7 * * *"),
        ("features", "FeatureAgent daily", ["python3", "main.py", "features", "--config", "configs/run_config.yaml"], "0 8 * * *"),
        ("labels", "LabelAgent daily", ["python3", "main.py", "labels", "--config", "configs/run_config.yaml"], "30 8 * * *"),
        ("model", "ModelAgent weekly", ["python3", "main.py", "model", "--config", "configs/run_config.yaml"], "0 10 * * 1"),
        ("portfolio", "PortfolioAgent weekly", ["python3", "main.py", "portfolio", "--config", "configs/run_config.yaml"], "0 12 * * 1"),
        ("papertrade", "Papertrade daily", ["python3", "main.py", "papertrade", "--config", "configs/run_config.yaml"], "0 13 * * *"),
    ]

    for job_id, label, command, cron in jobs:
        scheduler.add_job(
            run_cli,
            CronTrigger.from_crontab(cron),
            args=[job_id, command],
            id=job_id,
            name=label,
            misfire_grace_time=3600,
            max_instances=1,
        )

    # Daily collectors for the new data sources. These run at 05:00 UTC, before
    # the data-quality gate (05:45) and market ingestion (06:00), so their
    # outputs are available to the downstream pipeline. Staggered by a few
    # minutes to avoid launching five subprocesses simultaneously. Each calls
    # its module CLI (python -m providers.<name>) via the run_cli pattern.
    collector_jobs = [
        ("derivatives_okx", "Collector: OKX derivatives daily", ["python3", "-m", "providers.derivatives_okx"], "0 5 * * *"),
        ("derivatives_bybit", "Collector: Bybit derivatives daily", ["python3", "-m", "providers.derivatives_bybit"], "5 5 * * *"),
        ("defillama", "Collector: DefiLlama daily", ["python3", "-m", "providers.defillama"], "10 5 * * *"),
        ("macro_fred", "Collector: FRED macro daily", ["python3", "-m", "providers.macro_fred"], "15 5 * * *"),
        ("derivatives_coinglass", "Collector: Coinglass derivatives daily", ["python3", "-m", "providers.derivatives_coinglass"], "20 5 * * *"),
    ]
    for job_id, label, command, cron in collector_jobs:
        scheduler.add_job(
            run_cli,
            CronTrigger.from_crontab(cron),
            args=[job_id, command],
            id=job_id,
            name=label,
            misfire_grace_time=3600,
            max_instances=1,
        )

    # Operational monitors (monitoring/ package). data_quality gates the day
    # before market ingestion; the 13:30 block runs after papertrade at 13:00.
    monitor_jobs = [
        ("data_quality", "Monitoring: data quality gate daily", "monitoring.data_quality", "45 5 * * *"),
        ("signal_health", "Monitoring: signal health daily", "monitoring.signal_health", "0 9 * * *"),
        ("risk_report", "Monitoring: risk report daily", "monitoring.risk_report", "30 13 * * *"),
        ("shadow_nav", "Monitoring: shadow NAV daily", "monitoring.shadow_nav", "30 13 * * *"),
        ("model_decay", "Monitoring: model decay daily", "monitoring.model_decay", "30 13 * * *"),
        ("watchdog", "Monitoring: pipeline watchdog daily", "monitoring.watchdog", "0 14 * * *"),
        ("champion_challenger", "Monitoring: champion/challenger weekly", "monitoring.champion_challenger", "30 14 * * 1"),
    ]
    for job_id, label, module, cron in monitor_jobs:
        scheduler.add_job(
            run_monitor,
            CronTrigger.from_crontab(cron),
            args=[job_id, module],
            id=job_id,
            name=label,
            misfire_grace_time=3600,
            max_instances=1,
        )

    def _on_job_event(event) -> None:
        is_missed = event.code == EVENT_JOB_MISSED
        record = {
            "ts_utc": _utcnow_iso(),
            "event": "job_missed" if is_missed else "job_error",
            "job": event.job_id,
            "scheduled_run_time": str(getattr(event, "scheduled_run_time", None)),
            "exception": str(getattr(event, "exception", None)),
        }
        _append_failure(record)
        logger.error("Scheduler event recorded: %s %s", record["event"], event.job_id)

        # Notify (deduplicated). A missed job is a warning; a job error is critical.
        try:
            from monitoring import notifier

            severity = "warn" if is_missed else "critical"
            cfg = notifier.load_config(ROOT)
            prefix = str(cfg["notifications"].get("subject_prefix", "[CHF]"))
            key = _dedup_key("job_event", record["event"], event.job_id)
            if _should_send(key, _dedup_ttl_hours()):
                subject = f"{prefix} scheduler {record['event']}: {event.job_id}"
                body = (
                    f"Job: {event.job_id}\n"
                    f"Event: {record['event']}\n"
                    f"Scheduled: {record['scheduled_run_time']}\n"
                    f"Exception: {record['exception']}"
                )
                result = notifier.send(subject, body, severity, root=ROOT)
                if not result.get("skipped"):
                    _mark_sent(key)
        except Exception as exc:  # noqa: BLE001 - alerting must never crash the listener
            logger.error("notifier: job-event dispatch failed: %s", exc)

    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    logger.info("Project CHF scheduler starting. Press Ctrl+C to stop.")
    logger.info("BacktestAgent is intentionally manual/research-validation only by default.")
    for job in scheduler.get_jobs():
        logger.info("%s -> %s", job.name, job.trigger)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    start_scheduler()
