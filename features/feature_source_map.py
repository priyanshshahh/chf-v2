"""Explicit per-column feature source tagging (market vs onchain).

Historically the ``market_only`` / ``market_plus_onchain`` feature-set split was
decided by substring matching against an ``ONCHAIN_HINTS`` tuple in
``agents/model_agent.py`` and ``agents/alpha_research_agent.py``. That is
brittle: a renamed or new feature can silently land in the wrong bucket
(NEXT_STEPS item [4]).

This module defines the canonical, explicit contract instead:

- ``FeatureAgent`` emits ``data/features/feature_source_map.json`` mapping every
  emitted feature column to its true source (``"market"`` or ``"onchain"``),
  derived from which builder actually produced the column — not from its name.
- ``ModelAgent`` / ``AlphaResearchAgent`` load that map when present and only
  fall back to the legacy substring hints for columns absent from the map
  (backward compatibility with pre-map feature artifacts).
- ``scripts/backfill_feature_source_map.py`` generates the map for existing
  (frozen) feature artifacts using the legacy hint logic, so behaviour is
  provably unchanged until features are rebuilt.

File format (both accepted by :func:`load_feature_source_map`):

- wrapped (written by this module)::

    {"schema_version": 1, "generated_by": "...", "columns": {"tx_count": "onchain", ...}}

- flat: ``{"tx_count": "onchain", "log_ret_7d": "market", ...}``
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

FEATURE_SOURCE_MAP_FILENAME = "feature_source_map.json"
VALID_SOURCES = ("market", "onchain")


def classify_feature_source_by_hints(column: str, onchain_hints: Iterable[str]) -> str:
    """Legacy substring heuristic: any hint substring in the (lowercased) column
    name marks it ``onchain``; everything else is ``market``."""
    lower = column.lower()
    return "onchain" if any(hint in lower for hint in onchain_hints) else "market"


def build_feature_source_map_from_hints(
    columns: Iterable[str],
    onchain_hints: Iterable[str],
    exclude: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """Build a column -> source map from the legacy ONCHAIN_HINTS heuristic.

    Used only by the backfill path for feature artifacts that predate explicit
    source tagging; new feature builds get the map straight from FeatureAgent.
    """
    hints = tuple(onchain_hints)
    excluded = set(exclude or ())
    return {
        col: classify_feature_source_by_hints(col, hints)
        for col in columns
        if col not in excluded
    }


def write_feature_source_map(
    path: Path | str,
    column_sources: Dict[str, str],
    *,
    generated_by: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write the wrapped-format source map JSON. Validates source values."""
    bad = {c: s for c, s in column_sources.items() if s not in VALID_SOURCES}
    if bad:
        raise ValueError(f"Invalid feature sources (must be one of {VALID_SOURCES}): {bad}")
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "generated_by": generated_by,
        "columns": {col: column_sources[col] for col in sorted(column_sources)},
    }
    if extra_meta:
        payload.update(extra_meta)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load_feature_source_map(path: Path | str) -> Optional[Dict[str, str]]:
    """Load a column -> source map from ``path``.

    Returns ``None`` when the file is absent or unusable (callers then fall back
    to the legacy substring hints — the map is an upgrade, never a hard
    dependency). Accepts both the wrapped format and a flat column->source dict.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    columns = raw.get("columns", raw)
    if not isinstance(columns, dict):
        return None
    cleaned = {
        str(col): str(src)
        for col, src in columns.items()
        if isinstance(src, str) and src in VALID_SOURCES
    }
    return cleaned or None
