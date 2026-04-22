"""
CHF Logging Configuration
Structured JSON logging for all agents and modules.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Emit log records as JSON lines for structured log parsing."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_obj["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            log_obj.update(record.extra)
        return json.dumps(log_obj)


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[Path] = None,
    log_file: Optional[str] = None,
    json_output: bool = True,
) -> logging.Logger:
    """Configure root logger with console and optional file handlers."""
    level_num = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("chf")
    root.setLevel(level_num)

    if root.handlers:
        root.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level_num)
    if json_output:
        console_handler.setFormatter(JSONFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
        console_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(console_handler)

    # File handler
    if log_dir and log_file:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / log_file)
        file_handler.setLevel(level_num)
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the chf namespace."""
    return logging.getLogger(f"chf.{name}")
