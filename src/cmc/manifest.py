#!/usr/bin/env python3
"""manifest.py — read/write the per-dataset ``manifest.json``.

Every dataset directory carries a manifest recording its source endpoint, the
date coverage actually achieved, row/symbol counts, what generated it, and a
free-form ``run`` block (e.g. graceful-degradation notes). Used by every
collector and by ``maintain.py``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

MANIFEST_NAME = "manifest.json"


def write_manifest(
    out_dir: Path,
    dataset: str,
    source: str,
    coverage_start: Optional[str],
    coverage_end: Optional[str],
    row_count: int,
    symbol_count: Optional[int],
    generated_by: str,
    run: Optional[Dict[str, Any]] = None,
    name: str = MANIFEST_NAME,
) -> Path:
    """Write ``<out_dir>/manifest.json`` and return its path.

    Dates are ``YYYY-MM-DD`` strings; monetary context lives in the datasets
    (all USD). ``run`` carries collector-specific provenance/degradation notes.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": dataset,
        "source": source,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "row_count": int(row_count),
        "symbol_count": None if symbol_count is None else int(symbol_count),
        "generated_by": generated_by,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run": run or {},
    }
    path = out_dir / name
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    tmp.replace(path)
    return path


def read_manifest(out_dir: Path, name: str = MANIFEST_NAME) -> Optional[Dict[str, Any]]:
    """Return the parsed manifest dict for a dataset dir, or ``None`` if absent."""
    path = Path(out_dir) / name
    if not path.exists():
        return None
    return json.loads(path.read_text())
