"""
CHF APScheduler Job Runner
Schedules automated pipeline execution with configurable cron expressions.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from configs.config import get_config
from configs.logging_config import get_logger, setup_logging

logger = get_logger("jobs.scheduler")


def run_universe_job():
    """Scheduled job: update universe."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_universe()


def run_market_data_job():
    """Scheduled job: fetch market data."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_market_data()


def run_onchain_job():
    """Scheduled job: fetch on-chain data."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_onchain()


def run_features_job():
    """Scheduled job: compute features."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_clean()
    runner.run_features()
    runner.run_labels()


def run_models_job():
    """Scheduled job: train models."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_models()


def run_portfolio_job():
    """Scheduled job: generate portfolio allocations."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_portfolio()


def run_backtest_job():
    """Scheduled job: run backtests."""
    from pipelines.pipeline_runner import PipelineRunner
    runner = PipelineRunner()
    runner.run_backtest()


def start_scheduler():
    """Start the APScheduler with all configured jobs."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install apscheduler")
        return

    cfg = get_config()
    sched_cfg = cfg.get("scheduler", {})

    scheduler = BlockingScheduler(timezone="UTC")

    # Universe update: 1st of month at 02:00 UTC
    scheduler.add_job(
        run_universe_job,
        CronTrigger.from_crontab(sched_cfg.get("universe_update_cron", "0 2 1 * *")),
        id="universe_update",
        name="Universe Update",
        misfire_grace_time=3600,
    )

    # Market data: daily at 06:00 UTC
    scheduler.add_job(
        run_market_data_job,
        CronTrigger.from_crontab(sched_cfg.get("market_data_cron", "0 6 * * *")),
        id="market_data",
        name="Market Data Fetch",
        misfire_grace_time=3600,
    )

    # On-chain data: daily at 07:00 UTC
    scheduler.add_job(
        run_onchain_job,
        CronTrigger.from_crontab(sched_cfg.get("on_chain_cron", "0 7 * * *")),
        id="onchain_data",
        name="On-Chain Data Fetch",
        misfire_grace_time=3600,
    )

    # Features: daily at 08:00 UTC
    scheduler.add_job(
        run_features_job,
        CronTrigger.from_crontab(sched_cfg.get("feature_cron", "0 8 * * *")),
        id="features",
        name="Feature Engineering",
        misfire_grace_time=3600,
    )

    # Models: 1st of month at 10:00 UTC
    scheduler.add_job(
        run_models_job,
        CronTrigger.from_crontab(sched_cfg.get("model_cron", "0 10 1 * *")),
        id="models",
        name="Model Training",
        misfire_grace_time=7200,
    )

    # Portfolio: every Monday at 12:00 UTC
    scheduler.add_job(
        run_portfolio_job,
        CronTrigger.from_crontab(sched_cfg.get("portfolio_cron", "0 12 * * 1")),
        id="portfolio",
        name="Portfolio Allocation",
        misfire_grace_time=3600,
    )

    # Backtest: 1st of month at 14:00 UTC
    scheduler.add_job(
        run_backtest_job,
        CronTrigger.from_crontab(sched_cfg.get("backtest_cron", "0 14 1 * *")),
        id="backtest",
        name="Backtest Run",
        misfire_grace_time=7200,
    )

    logger.info("CHF Scheduler starting with jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    setup_logging(level="INFO", json_output=False)
    start_scheduler()
