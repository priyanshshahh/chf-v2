"""
CHF Configuration Loader
Loads and validates run_config.yaml, merges with .env overrides.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _find_config_path() -> Path:
    """Locate run_config.yaml relative to project root."""
    candidates = [
        _PROJECT_ROOT / "configs" / "run_config.yaml",
        Path.cwd() / "configs" / "run_config.yaml",
        Path.cwd() / "run_config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"run_config.yaml not found. Searched: {[str(c) for c in candidates]}"
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and return the run configuration as a nested dict."""
    config_path = path or _find_config_path()
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    # Apply environment variable overrides
    if os.getenv("MLFLOW_TRACKING_URI"):
        cfg.setdefault("mlflow", {})["tracking_uri"] = os.getenv("MLFLOW_TRACKING_URI")
    if os.getenv("CHF_SEED"):
        cfg["project"]["seed"] = int(os.getenv("CHF_SEED"))
    if os.getenv("LOG_LEVEL"):
        cfg.setdefault("logging", {})["level"] = os.getenv("LOG_LEVEL", "INFO")

    # Resolve all paths relative to project root
    cfg["_project_root"] = str(_PROJECT_ROOT)
    cfg["_config_path"] = str(config_path)

    return cfg


def get_config_hash(cfg: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash of the config dict."""
    serialized = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def resolve_path(cfg: Dict[str, Any], key: str) -> Path:
    """Resolve a path key from config relative to project root."""
    root = Path(cfg["_project_root"])
    raw_path = cfg["paths"].get(key, key)
    p = Path(raw_path)
    if not p.is_absolute():
        p = root / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_root() -> Path:
    """Return the project root directory."""
    return _PROJECT_ROOT


# Singleton config instance
_config: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    """Return the singleton config, loading it if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
