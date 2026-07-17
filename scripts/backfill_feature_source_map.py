#!/usr/bin/env python
"""One-off backfill of data/features/feature_source_map.json for frozen feature artifacts.

The canonical feature artifacts (last built 2026-06-18) predate explicit
per-column source tagging: FeatureAgent now emits ``feature_source_map.json``
alongside ``full_features.parquet``, but the frozen run does not have one.

This script generates the map from the existing ``full_features.parquet``
column schema using the *current* ``agents.model_agent.ONCHAIN_HINTS``
substring logic, so downstream feature-set selection (ModelAgent,
AlphaResearchAgent) is IDENTICAL with the map to what the hints produced —
the map only removes the brittleness going forward. When features are next
rebuilt, FeatureAgent overwrites this file with builder-provenance tags.

Usage:
    python scripts/backfill_feature_source_map.py [--features PATH] [--out PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pyarrow.parquet as pq  # noqa: E402

from agents.model_agent import MODEL_METADATA_COLUMNS, ONCHAIN_HINTS  # noqa: E402
from features.feature_source_map import (  # noqa: E402
    FEATURE_SOURCE_MAP_FILENAME,
    build_feature_source_map_from_hints,
    write_feature_source_map,
)

# Non-feature columns present in full_features.parquet that must not be tagged.
NON_FEATURE_COLUMNS = set(MODEL_METADATA_COLUMNS) | {"date_ts", "symbol"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        default=str(PROJECT_ROOT / "data/features/full_features.parquet"),
        help="Path to the existing full_features.parquet (schema only is read).",
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data/features" / FEATURE_SOURCE_MAP_FILENAME),
        help="Output JSON path for the feature source map.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the map without writing.")
    args = parser.parse_args()

    features_path = Path(args.features)
    if not features_path.exists():
        print(f"ERROR: features file not found: {features_path}", file=sys.stderr)
        return 1

    columns = pq.read_schema(features_path).names
    source_map = build_feature_source_map_from_hints(
        columns,
        ONCHAIN_HINTS,
        exclude=NON_FEATURE_COLUMNS,
    )
    counts = Counter(source_map.values())
    print(f"Classified {len(source_map)} feature columns from {features_path.name}: {dict(counts)}")
    if args.dry_run:
        for col in sorted(source_map):
            print(f"  {source_map[col]:8s} {col}")
        return 0

    out = write_feature_source_map(
        Path(args.out),
        source_map,
        generated_by="scripts/backfill_feature_source_map.py",
        extra_meta={
            "method": "onchain_hints_backfill",
            "source_file": str(features_path),
            "note": (
                "Backfilled from the frozen feature artifacts using the legacy "
                "ONCHAIN_HINTS substring heuristic for backward compatibility; "
                "the next FeatureAgent run replaces this with builder-provenance tags."
            ),
        },
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
