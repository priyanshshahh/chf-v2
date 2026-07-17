#!/usr/bin/env python3
"""storage.py — atomic, idempotent Parquet upsert.

``upsert(df, key, path)`` reads the existing Parquet (if present), concatenates
the new frame, drops duplicates on ``key`` keeping the last occurrence, sorts by
``key``, writes a temp file, and ``os.replace()``s it onto ``path``. Applying the
same input twice yields an identical file and identical row count (SC-004).

Imported by every collector and by ``maintain.py``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pandas as pd


def upsert(df: pd.DataFrame, key: List[str], path: Path) -> int:
    """Merge ``df`` into the Parquet at ``path`` keyed on ``key``; return row count.

    - keep="last" lets a re-fetched period correct prior values without dupes.
    - Atomic temp-file + os.replace prevents partial-run corruption.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        # Schema guard: silent concat of mismatched column sets would corrupt
        # the dataset with NaN-padded columns. Identical set (any order) is OK.
        # A zero-column frame (collector fetched nothing new) is exempt.
        if len(df.columns) and set(existing.columns) != set(df.columns):
            added = sorted(set(df.columns) - set(existing.columns))
            missing = sorted(set(existing.columns) - set(df.columns))
            raise ValueError(
                f"upsert schema mismatch for {path}: "
                f"columns added={added} missing={missing}"
            )
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df.copy()

    combined = (
        combined.drop_duplicates(subset=key, keep="last")
        .sort_values(key)
        .reset_index(drop=True)
    )

    tmp = path.with_suffix(path.suffix + ".tmp")
    combined.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return len(combined)
