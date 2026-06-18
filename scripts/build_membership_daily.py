#!/usr/bin/env python3
"""
build_membership_daily.py
=========================
Expand the monthly point-in-time universe (``universe_monthly.parquet``) into a
**daily** membership mask (``universe_membership_daily.parquet``).

Why this exists
---------------
``UniverseAgent`` produces a survivorship-free *monthly* membership: for each
month-start it records the eligible tradable top-N (incl. since-delisted coins in
the months they were actually ranked). But every downstream consumer needs to know,
for an *arbitrary daily date*, whether a given asset was a universe member on that
day. Without a daily mask, ``MarketDataAgent`` and ``FeatureAgent`` historically
collapsed to the single latest snapshot — silently re-introducing full survivorship
bias and discarding 65 of 66 months.

This builder produces the daily mask by **forward-holding** each month-start's
membership until the next month-start. The construction is *point-in-time correct*:
membership on calendar date ``d`` is derived only from the most recent snapshot whose
``snapshot_date <= d`` — never from a future snapshot. No look-ahead, no fabrication;
every output row traces to a real ``universe_monthly`` row.

Output schema (``universe_membership_daily.parquet``)
-----------------------------------------------------
``date_ts`` (daily, UTC-aware), ``cmc_id``, ``symbol``, ``name``, ``is_member`` (always
True in this file — absence of a row means "not a member that day"), ``market_cap_rank``,
``market_cap_usd``, ``snapshot_date`` (the month-start this membership derives from),
``snapshot_month`` (``YYYY-MM``), ``source``.

CLI
---
    python scripts/build_membership_daily.py \
        --universe data/raw/universe/universe_monthly.parquet \
        --out data/raw/universe/universe_membership_daily.parquet \
        [--end 2026-06-30]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Columns carried from universe_monthly into the daily mask (subset that exists).
_CARRY_COLUMNS = [
    "cmc_id",
    "symbol",
    "name",
    "market_cap_rank",
    "market_cap_usd",
    "source",
]

OUTPUT_COLUMNS = [
    "date_ts",
    "cmc_id",
    "symbol",
    "name",
    "is_member",
    "market_cap_rank",
    "market_cap_usd",
    "snapshot_date",
    "snapshot_month",
    "source",
]


class MembershipBuildError(RuntimeError):
    pass


def _to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def build_membership_daily(
    universe_monthly_path: Path | str,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Expand monthly eligible membership into a daily, point-in-time-correct mask.

    Parameters
    ----------
    universe_monthly_path:
        Path to ``universe_monthly.parquet`` (eligible rows only, one block per
        monthly snapshot).
    end_date:
        Optional inclusive end date (``YYYY-MM-DD``) for the *final* snapshot's
        forward-hold window. If omitted, the final snapshot is held to the end of
        its own calendar month. Earlier snapshots are always held only up to the day
        before the next snapshot (so interior windows never depend on this argument).

    Returns
    -------
    DataFrame in ``OUTPUT_COLUMNS`` order, sorted by (date_ts, market_cap_rank).
    """
    universe_monthly_path = Path(universe_monthly_path)
    if not universe_monthly_path.exists():
        raise MembershipBuildError(f"Missing universe file: {universe_monthly_path}")

    uni = pd.read_parquet(universe_monthly_path)
    if uni.empty:
        raise MembershipBuildError("universe_monthly.parquet is empty")

    required = {"snapshot_date", "cmc_id", "symbol"}
    missing = required - set(uni.columns)
    if missing:
        raise MembershipBuildError(f"universe_monthly missing columns: {sorted(missing)}")

    # The monthly file already contains only eligible rows. Defensive: honor is_eligible
    # if present so a re-keyed file still yields a clean member set.
    if "is_eligible" in uni.columns:
        uni = uni[uni["is_eligible"].fillna(False).astype(bool)].copy()
    if uni.empty:
        raise MembershipBuildError("No eligible rows found in universe_monthly")

    uni["snapshot_date"] = _to_utc(uni["snapshot_date"]).dt.normalize()
    uni["symbol"] = uni["symbol"].astype(str).str.upper().str.strip()
    for col in _CARRY_COLUMNS:
        if col not in uni.columns:
            uni[col] = pd.NA

    snapshots = sorted(uni["snapshot_date"].dropna().unique())
    if not snapshots:
        raise MembershipBuildError("No snapshot dates in universe_monthly")

    end_ts: Optional[pd.Timestamp] = None
    if end_date:
        end_ts = pd.Timestamp(end_date, tz="UTC").normalize()

    frames = []
    for i, snap in enumerate(snapshots):
        snap_ts = pd.Timestamp(snap)
        if i + 1 < len(snapshots):
            # Hold until the day before the next snapshot (no look-ahead, no overlap).
            window_end = pd.Timestamp(snapshots[i + 1]) - pd.Timedelta(days=1)
        else:
            # Final snapshot: hold to explicit end_date, else end of its own month.
            month_end = (snap_ts + pd.offsets.MonthEnd(1)).normalize()
            window_end = end_ts if (end_ts is not None and end_ts >= snap_ts) else month_end
        if window_end < snap_ts:
            window_end = snap_ts
        days = pd.date_range(start=snap_ts, end=window_end, freq="D", tz="UTC")

        members = uni[uni["snapshot_date"] == snap_ts].copy()
        if members.empty:
            continue
        members = members.drop_duplicates(subset=["cmc_id"], keep="first")

        # Cross-join this snapshot's members with every day in its hold window.
        block = members.loc[:, _CARRY_COLUMNS].copy()
        block["snapshot_date"] = snap_ts
        block["snapshot_month"] = snap_ts.strftime("%Y-%m")
        block["_key"] = 1
        day_df = pd.DataFrame({"date_ts": days, "_key": 1})
        merged = day_df.merge(block, on="_key", how="outer").drop(columns="_key")
        frames.append(merged)

    if not frames:
        raise MembershipBuildError("No membership rows produced")

    out = pd.concat(frames, ignore_index=True)
    out["is_member"] = True
    out["date_ts"] = _to_utc(out["date_ts"]).dt.normalize()
    out = out.reindex(columns=OUTPUT_COLUMNS)
    out = out.sort_values(["date_ts", "market_cap_rank", "symbol"], na_position="last").reset_index(drop=True)

    # Invariant: exactly one (date_ts, cmc_id) per row (no overlap across windows).
    dup = out.duplicated(subset=["date_ts", "cmc_id"]).sum()
    if dup:
        raise MembershipBuildError(f"Daily membership has {dup} duplicate (date_ts, cmc_id) rows")
    return out


def write_membership_daily(
    universe_monthly_path: Path | str,
    out_path: Path | str,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build and persist the daily membership mask + a small manifest. Returns manifest."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = build_membership_daily(universe_monthly_path, end_date=end_date)
    df.to_parquet(out_path, index=False)

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_universe": str(universe_monthly_path),
        "output": str(out_path),
        "rows": int(len(df)),
        "unique_cmc_id": int(df["cmc_id"].nunique()),
        "unique_symbol": int(df["symbol"].nunique()),
        "date_min": df["date_ts"].min().date().isoformat(),
        "date_max": df["date_ts"].max().date().isoformat(),
        "snapshot_count": int(df["snapshot_date"].nunique()),
        "end_date_arg": end_date,
        "no_lookahead": True,
        "note": "Daily membership forward-held from each month-start; date d derives only from snapshot_date <= d.",
    }
    manifest_path = out_path.with_name("membership_daily_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand monthly universe membership into a daily PIT mask")
    parser.add_argument("--universe", default="data/raw/universe/universe_monthly.parquet")
    parser.add_argument("--out", default="data/raw/universe/universe_membership_daily.parquet")
    parser.add_argument("--end", default=None, help="Inclusive end date YYYY-MM-DD for the final snapshot hold window")
    args = parser.parse_args()

    uni = Path(args.universe)
    if not uni.is_absolute():
        uni = PROJECT_ROOT / uni
    out = Path(args.out)
    if not out.is_absolute():
        out = PROJECT_ROOT / out

    manifest = write_membership_daily(uni, out, end_date=args.end)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
