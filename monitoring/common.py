"""Shared plumbing for the CHF monitoring package.

Config loading (configs/monitoring.yaml over built-in defaults), report
writing (JSON + parquet under data/reports/monitoring/), safe parquet/JSON
readers that tolerate missing inputs, and small return/statistics helpers
reused by several monitors.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_CONFIG_RELPATH = "configs/monitoring.yaml"
ANNUALIZATION_DAYS = 365.0

DEFAULT_CONFIG: Dict[str, Any] = {
    "paths": {
        "market": "data/raw/market/market_ohlcv.parquet",
        "onchain": "data/raw/onchain/onchain_wide.parquet",
        "predictions": "data/predictions/model_predictions.parquet",
        "backtest_summary": "data/backtests/backtest_summary.json",
        "equity_curves": "data/backtests/equity_curves.parquet",
        "turnover_report": "data/backtests/turnover_report.parquet",
        "cost_sweep": "data/backtests/cost_sweep.parquet",
        "allocations": "data/allocations/allocations_top_5_equal_weight.parquet",
        "papertrade_dir": "data/papertrade",
        "reports_dir": "data/reports/monitoring",
        "readiness_verdict": "data/readiness/daily_quality_verdict.json",
        "memory_dir": "memory",
    },
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_today() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").normalize()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(root: Path, config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configs/monitoring.yaml (if present) merged over defaults."""
    path = Path(config_path) if config_path else root / DEFAULT_CONFIG_RELPATH
    if not path.is_absolute():
        path = root / path
    cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
    if path.exists():
        with open(path, "r") as f:
            loaded = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, loaded)
    return cfg


def build_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", default=None, help="Project root (default: repo root)")
    parser.add_argument("--config", default=None, help="Path to monitoring config yaml")
    return parser


def resolve_root(arg_root: Optional[str]) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    return Path(__file__).resolve().parents[1]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return None if math.isnan(value) else value
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def _clean_nan(value: Any) -> Any:
    """Recursively convert NaN/inf floats to None for stable JSON."""
    if isinstance(value, dict):
        return {k: _clean_nan(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_nan(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_report(
    root: Path,
    reports_dir: str,
    name: str,
    summary: Dict[str, Any],
    table: Optional[pd.DataFrame] = None,
) -> Dict[str, str]:
    """Write ``<name>.json`` (+ ``<name>.parquet`` if a table is given)."""
    out_dir = root / reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = _clean_nan(dict(summary))
    summary.setdefault("generated_utc", utcnow_iso())
    json_path = out_dir / f"{name}.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=_json_default)
    written = {"json": str(json_path)}
    if table is not None and len(table):
        pq_path = out_dir / f"{name}.parquet"
        table = table.copy()
        for col in table.columns:
            if table[col].dtype == object:
                # mixed-type columns (e.g. numeric + string thresholds) break arrow
                table[col] = table[col].map(lambda v: None if v is None else str(v))
        table.to_parquet(pq_path, index=False)
        written["parquet"] = str(pq_path)
    return written


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(_clean_nan(payload), f, indent=2, default=_json_default)


def read_parquet_safe(path: Path, columns: Optional[List[str]] = None) -> Optional[pd.DataFrame]:
    """Read a parquet file, returning None when missing/unreadable."""
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception:  # noqa: BLE001 - a corrupt input must not crash a monitor
        try:
            return pd.read_parquet(path)
        except Exception:
            return None


def read_json_safe(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# shared statistics helpers
# ---------------------------------------------------------------------------
def annualized_sharpe(returns: pd.Series) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if len(r) < 2 or float(r.std(ddof=1)) == 0.0:
        return None
    return float(r.mean() / r.std(ddof=1) * np.sqrt(ANNUALIZATION_DAYS))

def annualized_vol(returns: pd.Series) -> Optional[float]:
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return None
    return float(r.std(ddof=1) * np.sqrt(ANNUALIZATION_DAYS))


def max_drawdown(nav: pd.Series) -> Optional[float]:
    nav = pd.Series(nav).dropna()
    if len(nav) < 2:
        return None
    peaks = nav.cummax()
    return float((nav / peaks - 1.0).min())


def current_drawdown(nav: pd.Series) -> Optional[float]:
    nav = pd.Series(nav).dropna()
    if len(nav) < 1:
        return None
    return float(nav.iloc[-1] / nav.max() - 1.0)


def list_papertrade_books(root: Path, papertrade_dir: str) -> List[str]:
    base = root / papertrade_dir
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "state.json").exists())


def exit_code(alert: bool) -> int:
    return 1 if alert else 0
