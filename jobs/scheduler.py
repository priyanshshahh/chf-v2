"""
Project CHF local scheduler.

The scheduler calls existing CLI entrypoints through subprocess. It does not
implement research logic and is disabled unless explicitly started.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "scheduler"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chf.scheduler")


def run_cli(label: str, command: list[str]) -> None:
    """Run a supported CHF CLI command and raise on failure."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"{stamp}_{label}.log"
    logger.info("Starting %s: %s", label, " ".join(command))
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    log_path.write_text(
        "\n".join(
            [
                f"label: {label}",
                f"command: {' '.join(command)}",
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
    if proc.returncode != 0:
        logger.error("%s failed. See %s", label, log_path)
        raise RuntimeError(f"{label} failed with return code {proc.returncode}")
    logger.info("%s completed. Log: %s", label, log_path)


def start_scheduler() -> None:
    try:
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
