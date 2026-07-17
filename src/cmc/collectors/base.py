#!/usr/bin/env python3
"""base.py — shared collector helpers (raw-sample retention + finalize)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Protocol

import pandas as pd

from ..manifest import write_manifest
from ..storage import upsert


class CollectorRun(Protocol):
    """Standard collector entry point wired into ``maintain.REGISTRY``.

    Contract for ``run(client, base_dir, start=None, end=None) -> dict``:

    * ``client``   — a CMCClient-shaped object exposing ``get(path, params)``.
    * ``base_dir`` — root data dir; the collector owns ``base_dir / DATASET``.
    * ``start``    — inclusive ISO date lower bound. ``maintain.py`` passes
      ``coverage_end + 1 day`` from the dataset manifest. Collectors registered
      with ``is_ts=True`` MUST honor ``start`` (restrict the requested fetch
      range to [start, end]) OR explicitly document in their module why they
      do not:
        - ``fear_greed`` always pages full history: the endpoint is cheap and
          re-fetching self-heals gaps (upsert dedupes on the date key).
        - ``altcoin_season`` is pinned to the endpoint's fixed 90-day trailing
          window; it cannot backfill, so it records ``gap_detected`` /
          ``gap_range`` in its manifest instead.
    * ``end``      — inclusive ISO date upper bound (default: today, UTC).
    * returns a manifest-style dict with at least ``row_count``,
      ``coverage_start`` and ``coverage_end``.

    Re-running any window MUST be idempotent: storage.upsert dedupes on the
    dataset's primary key with keep="last".
    """

    def __call__(self, client: Any, base_dir: Path,
                 start: Optional[str] = None,
                 end: Optional[str] = None) -> dict: ...


def coerce_float(v: Any) -> Optional[float]:
    """Coerce an API value to float: ``None``/``""`` → None; junk → None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def save_raw_sample(out_dir: Path, name: str, payload: Any) -> Path:
    """Persist a raw API response body under ``raw_api_samples/`` (Constitution II)."""
    raw_dir = Path(out_dir) / "raw_api_samples"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(json.dumps(payload, indent=2, default=str)[:5_000_000])
    return path


def finalize(
    df: pd.DataFrame,
    *,
    out_dir: Path,
    filename: str,
    dataset: str,
    source: str,
    key: List[str],
    generated_by: str,
    date_col: Optional[str] = "date",
    symbol_col: Optional[str] = None,
    run: Optional[dict] = None,
    manifest_name: str = "manifest.json",
) -> dict:
    """Upsert ``df`` and (re)write the dataset manifest; return the manifest dict."""
    out_dir = Path(out_dir)
    path = out_dir / filename
    row_count = upsert(df, key=key, path=path)

    full = pd.read_parquet(path)
    if date_col and date_col in full.columns and len(full):
        dates = full[date_col].dropna().astype(str)
        dates = dates[dates.str.match(r"\d{4}-\d{2}-\d{2}")]
        if len(dates):
            coverage_start, coverage_end = dates.min(), dates.max()
        else:
            coverage_start = coverage_end = None
    else:
        coverage_start = coverage_end = None
    symbol_count = (
        int(full[symbol_col].nunique()) if symbol_col and symbol_col in full else None
    )
    return _write(
        out_dir, dataset, source, coverage_start, coverage_end,
        row_count, symbol_count, generated_by, run, manifest_name,
    )


def _write(out_dir, dataset, source, cstart, cend, rows, syms, gen_by, run, mname):
    write_manifest(
        out_dir=out_dir, dataset=dataset, source=source,
        coverage_start=cstart, coverage_end=cend,
        row_count=rows, symbol_count=syms, generated_by=gen_by, run=run or {},
        name=mname,
    )
    return {
        "dataset": dataset, "source": source, "coverage_start": cstart,
        "coverage_end": cend, "row_count": rows, "symbol_count": syms,
    }
