"""
CHF AgentBase
Base class for all pipeline agents. Provides lifecycle management,
logging, retries, config loading, snapshot handling, and QA hooks.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from configs.config import get_config, get_config_hash, resolve_path
from configs.logging_config import get_logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentBase(ABC):
    """
    Abstract base class for all CHF pipeline agents.

    Subclasses must implement:
        prepare()  — validate inputs, check preconditions
        run()      — execute core logic
        persist()  — write outputs to disk
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = config or get_config()
        self.logger = get_logger(self.__class__.__name__)
        self.run_id: str = str(uuid.uuid4())[:8]
        self.config_hash: str = get_config_hash(self.cfg)
        self.snapshot_id: Optional[str] = None
        self.status: str = "PENDING"
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self.output_paths: Dict[str, str] = {}
        self.metrics: Dict[str, float] = {}
        self._project_root = Path(self.cfg["_project_root"])
        self._registry_path = self._project_root / "metadata" / "agent_registry.db"
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_registry()

    # ─────────────────────────────────────────
    # Abstract interface
    # ─────────────────────────────────────────

    @abstractmethod
    def prepare(self) -> None:
        """Validate inputs and preconditions before running."""
        ...

    @abstractmethod
    def run(self) -> Any:
        """Execute the agent's core logic. Return output data."""
        ...

    @abstractmethod
    def persist(self, result: Any) -> None:
        """Persist outputs to disk."""
        ...

    # ─────────────────────────────────────────
    # Lifecycle management
    # ─────────────────────────────────────────

    def execute(self, max_retries: int = 3, retry_backoff: float = 2.0) -> bool:
        """
        Full agent lifecycle: prepare → run → persist.
        Handles retries, logging, and registry updates.
        Returns True on success, False on failure.
        """
        self.started_at = _utcnow()
        self.status = "RUNNING"
        self._update_registry()
        self.logger.info(
            f"Agent {self.__class__.__name__} starting | run_id={self.run_id}"
        )

        for attempt in range(1, max_retries + 1):
            try:
                self.prepare()
                result = self.run()
                self.persist(result)
                self.status = "SUCCESS"
                self.completed_at = _utcnow()
                self._update_registry()
                elapsed = (self.completed_at - self.started_at).total_seconds()
                output_count = len(self.output_paths)
                metric_keys = sorted(self.metrics.keys())
                metric_preview = metric_keys[:5]
                self.logger.info(
                    f"Agent {self.__class__.__name__} succeeded | "
                    f"run_id={self.run_id} | elapsed={elapsed:.1f}s | "
                    f"outputs={output_count} | metrics={metric_preview}"
                )
                return True
            except Exception as exc:
                self.error_message = traceback.format_exc()
                wait = retry_backoff ** attempt
                self.logger.warning(
                    f"Agent {self.__class__.__name__} attempt {attempt}/{max_retries} "
                    f"failed: {exc} | retrying in {wait:.1f}s"
                )
                if attempt < max_retries:
                    time.sleep(wait)

        self.status = "FAILED"
        self.completed_at = _utcnow()
        self._update_registry()
        self.logger.error(
            f"Agent {self.__class__.__name__} FAILED after {max_retries} attempts | "
            f"run_id={self.run_id}\n{self.error_message}"
        )
        return False

    # ─────────────────────────────────────────
    # Snapshot handling
    # ─────────────────────────────────────────

    def generate_snapshot_id(self, data_repr: str = "") -> str:
        """Generate a deterministic snapshot ID from config hash + data repr."""
        raw = f"{self.config_hash}:{data_repr}:{self.run_id}"
        self.snapshot_id = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return self.snapshot_id

    # ─────────────────────────────────────────
    # Path helpers
    # ─────────────────────────────────────────

    def get_path(self, key: str) -> Path:
        """Resolve a data path from config."""
        return resolve_path(self.cfg, key)

    # ─────────────────────────────────────────
    # QA hooks
    # ─────────────────────────────────────────

    def qa_check(self, df: Any, name: str) -> None:
        """Basic QA hook: log shape and null counts."""
        try:
            import pandas as pd
            if isinstance(df, pd.DataFrame):
                nulls = df.isnull().sum().sum()
                self.logger.info(
                    f"QA [{name}]: shape={df.shape}, nulls={nulls}"
                )
                self.metrics[f"qa_{name}_rows"] = len(df)
                self.metrics[f"qa_{name}_nulls"] = int(nulls)
        except Exception:
            pass

    # ─────────────────────────────────────────
    # SQLite run registry
    # ─────────────────────────────────────────

    def _init_registry(self) -> None:
        """Initialize the SQLite agent run registry."""
        with sqlite3.connect(self._registry_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    config_hash TEXT,
                    snapshot_id TEXT,
                    error_message TEXT,
                    output_paths TEXT,
                    metrics TEXT
                )
            """)
            conn.commit()

    def _update_registry(self) -> None:
        """Upsert the current run record into the registry."""
        try:
            with sqlite3.connect(self._registry_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO agent_runs
                    (run_id, agent_name, status, started_at, completed_at,
                     config_hash, snapshot_id, error_message, output_paths, metrics)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.run_id,
                    self.__class__.__name__,
                    self.status,
                    self.started_at.isoformat() if self.started_at else None,
                    self.completed_at.isoformat() if self.completed_at else None,
                    self.config_hash,
                    self.snapshot_id,
                    self.error_message,
                    json.dumps(self.output_paths),
                    json.dumps(self.metrics),
                ))
                conn.commit()
        except Exception as e:
            self.logger.warning(f"Registry update failed: {e}")

    @classmethod
    def get_run_history(cls, registry_path: Path) -> list:
        """Return all run records from the registry."""
        if not registry_path.exists():
            return []
        with sqlite3.connect(registry_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
