"""
agents/universe_sources.py — pluggable universe data-source builders
====================================================================

The single :class:`agents.universe_agent.UniverseAgent` orchestrates universe
construction, but the *source of point-in-time rankings* is pluggable. Each
source here turns some real dataset/API into a list of per-snapshot **candidate
frames** in the canonical candidate schema that the agent's shared processing
core (classification → gates → exclusion → top-N → hash → coverage) consumes.

Sources
-------
* ``cmc_web_pit``         — survivorship-FREE PIT rankings from CoinMarketCap's
                            public keyless data-API snapshot dataset
                            (built by ``scripts/build_cmc_web_history.py``).
                            Carries cmc_id, dateAdded (PIT maturity), real
                            category tags, and numMarketPairs (PIT tradability).
* ``cmc_listings_download``— PIT rankings from a downloaded CMC Pro
                            ``listings/historical`` parquet
                            (``scripts/build_cmc_history.py``).
* ``local_dataset``       — PIT rankings from any free historical rankings
                            dataset (CSV/Parquet/JSON), as-of cross-section.

This module is deliberately kept separate from ``universe_agent.py`` so the
CMC-specific ingestion code lives on its own, while the pipeline only ever
imports/instantiates ``UniverseAgent``.

No synthetic data is ever produced: every candidate row traces to a real row in
a real dataset; empty/missing inputs raise rather than fabricate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

# Canonical candidate columns the agent's processing core expects.
CANDIDATE_COLUMNS = [
    "symbol",
    "name",
    "slug",
    "provider",
    "provider_asset_id",
    "coin_id",
    "cmc_id",
    "market_cap_rank",
    "market_cap_usd",
    "volume_24h_usd",
    "price_usd",
    "is_active_at_snapshot",
    "raw_category_tags",
    "first_seen_utc",
    "num_market_pairs",
    "source",
]

# Real CoinGecko/CMC deny-category slug substrings -> the agent's exclusion flags.
# Folded in from the former UniverseAgentFree so every source gets accurate
# tag-based classification (kept in addition to denylist + keyword matching).
CATEGORY_FLAG_RULES: List[Tuple[str, str]] = [
    ("stablecoin", "is_stablecoin"),
    ("usd-stablecoin", "is_stablecoin"),
    ("fiat-stablecoin", "is_stablecoin"),
    ("asset-backed-stablecoin", "is_stablecoin"),
    ("wrapped", "is_wrapped"),
    ("binance-peg", "is_wrapped"),
    ("bridged", "is_bridged"),
    ("liquid-staking", "is_lst"),
    ("liquid-restak", "is_lst"),
    ("restaking", "is_lst"),
    ("restaked", "is_lst"),
    ("eth-staking", "is_lst"),
    ("liquid-staking-derivatives", "is_lst"),
    ("real-world", "is_synthetic_pegged"),
    ("rwa", "is_synthetic_pegged"),
    ("tokenized", "is_synthetic_pegged"),
    ("asset-backed", "is_synthetic_pegged"),
    ("commodity-backed", "is_synthetic_pegged"),
    ("peg-token", "is_synthetic_pegged"),
]

_COLUMN_ALIASES: Dict[str, List[str]] = {
    "date": ["date", "date_ts", "snapshot_date", "timestamp", "time", "day"],
    "symbol": ["symbol", "ticker", "asset", "coin", "code"],
    "name": ["name", "coin_name", "asset_name", "currency"],
    "market_cap": ["market_cap", "market_cap_usd", "marketcap", "mcap", "market_capitalization"],
    "rank": ["rank", "market_cap_rank", "cmc_rank", "ranking"],
    "volume_24h": ["volume_24h", "volume_24h_usd", "total_volume", "volume", "vol_24h"],
    "price": ["price", "price_usd", "close", "current_price"],
}


class UniverseSourceError(RuntimeError):
    pass


@dataclass
class SourcePlan:
    """A resolved universe source: how to process it and its per-snapshot candidates."""

    mode_name: str
    provider_name: str
    processing: str  # "market" | "cmc" | "pit"
    survivor_only: bool
    uses_cmc_id: bool
    snapshots: List[Tuple[pd.Timestamp, pd.DataFrame]]
    limitation: str = ""
    historical_market_cap_available: bool = True
    requested_start: Optional[str] = None
    requested_end: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


def _to_utc(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# --------------------------------------------------------------- cmc_web_pit
def load_cmc_web_dataset(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise UniverseSourceError(
            f"CMC web snapshot dataset not found: {path}\n"
            "Build it first: python3 scripts/build_cmc_web_history.py "
            "--start <YYYY-MM-01> --end <YYYY-MM-01> --top 300 --freq monthly"
        )
    df = pd.read_parquet(path)
    if df.empty:
        raise UniverseSourceError(f"CMC web snapshot dataset is empty: {path}")
    required = {"snapshot_date", "cmc_id", "symbol", "market_cap_usd"}
    missing = required - set(df.columns)
    if missing:
        raise UniverseSourceError(f"CMC web dataset missing columns: {sorted(missing)}")
    df = df.copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True)
    return df


def _month_targets(dataset_dates: List[pd.Timestamp], start: Optional[str], end: Optional[str]) -> List[pd.Timestamp]:
    ds_min, ds_max = min(dataset_dates), max(dataset_dates)
    start_ts = _to_utc(start).replace(day=1) if start else ds_min.replace(day=1)
    end_ts = _to_utc(end).replace(day=1) if end else ds_max.replace(day=1)
    months = pd.date_range(start=start_ts, end=end_ts, freq="MS", tz="UTC")
    return [m for m in months if ds_min <= m <= ds_max + pd.Timedelta(days=1)]


def _cmc_web_candidates(snap: pd.DataFrame, snapshot_date: pd.Timestamp, candidate_n: int) -> pd.DataFrame:
    sub = snap.sort_values("market_cap_usd", ascending=False).head(candidate_n).copy()
    out = pd.DataFrame()
    out["symbol"] = sub["symbol"].astype(str).str.upper().str.strip().values
    out["name"] = sub["name"].astype(str).values if "name" in sub else out["symbol"]
    out["slug"] = sub["slug"].astype(str).values if "slug" in sub else out["symbol"].str.lower()
    out["provider"] = "coinmarketcap"
    out["cmc_id"] = pd.to_numeric(sub["cmc_id"], errors="coerce").astype("Int64").values
    out["provider_asset_id"] = out["cmc_id"].astype("string").fillna("").values
    out["coin_id"] = out["slug"].values
    out["market_cap_rank"] = (
        pd.to_numeric(sub["rank"], errors="coerce").values if "rank" in sub else range(1, len(sub) + 1)
    )
    out["market_cap_usd"] = pd.to_numeric(sub["market_cap_usd"], errors="coerce").values
    out["volume_24h_usd"] = (
        pd.to_numeric(sub["volume_24h_usd"], errors="coerce").fillna(0.0).values if "volume_24h_usd" in sub else 0.0
    )
    out["price_usd"] = (
        pd.to_numeric(sub["price_usd"], errors="coerce").fillna(0.0).values if "price_usd" in sub else 0.0
    )
    out["is_active_at_snapshot"] = True
    tags = sub["raw_category_tags"] if "raw_category_tags" in sub else None
    if tags is not None:
        out["raw_category_tags"] = [list(t) if t is not None and hasattr(t, "__iter__") and not isinstance(t, str) else ([] if t is None else [str(t)]) for t in tags.values]
    else:
        out["raw_category_tags"] = [[] for _ in range(len(out))]
    if "date_added" in sub:
        out["first_seen_utc"] = pd.to_datetime(sub["date_added"], utc=True, errors="coerce").astype("string").fillna("").values
    else:
        out["first_seen_utc"] = ""
    out["num_market_pairs"] = (
        pd.to_numeric(sub["num_market_pairs"], errors="coerce").fillna(0).astype(int).values
        if "num_market_pairs" in sub
        else 0
    )
    out["source"] = "coinmarketcap_web_historical"
    return out.reindex(columns=CANDIDATE_COLUMNS)


def build_cmc_web_pit(
    dataset_path: Path,
    candidate_n: int,
    start: Optional[str],
    end: Optional[str],
    asof_staleness_days: int = 40,
) -> SourcePlan:
    df = load_cmc_web_dataset(dataset_path)
    snap_dates = sorted(df["snapshot_date"].dropna().unique().tolist())
    snap_ts = [pd.Timestamp(d) for d in snap_dates]
    targets = _month_targets(snap_ts, start, end)
    if not targets:
        raise UniverseSourceError("No month-start snapshots fall within the CMC web dataset coverage.")

    snapshots: List[Tuple[pd.Timestamp, pd.DataFrame]] = []
    window = pd.Timedelta(days=asof_staleness_days)
    for target in targets:
        # exact month-start row preferred; else most-recent snapshot within staleness window.
        exact = df[df["snapshot_date"] == target]
        if exact.empty:
            asof = df[(df["snapshot_date"] <= target) & (df["snapshot_date"] >= target - window)]
            if asof.empty:
                continue
            chosen = asof["snapshot_date"].max()
            exact = df[df["snapshot_date"] == chosen]
        cand = _cmc_web_candidates(exact, target, candidate_n)
        if not cand.empty:
            snapshots.append((target, cand))

    if not snapshots:
        raise UniverseSourceError("CMC web dataset produced no usable monthly snapshots.")
    return SourcePlan(
        mode_name="historical_cmc_web_monthly",
        provider_name="coinmarketcap_web_historical",
        processing="pit",
        survivor_only=False,
        uses_cmc_id=True,
        snapshots=snapshots,
        historical_market_cap_available=True,
        requested_start=targets[0].date().isoformat(),
        requested_end=targets[-1].date().isoformat(),
        limitation=(
            "Point-in-time membership is the real CMC top-N as of each month (incl. since-delisted coins, "
            "survivorship-bias-free). Maturity is verified point-in-time from dateAdded; exchange tradability "
            "uses numMarketPairs as a point-in-time proxy; on-chain coverage is verified against the CoinMetrics "
            "catalog's earliest-availability date as of the snapshot."
        ),
    )


# --------------------------------------------------- cmc_listings_download
def build_cmc_listings_download(
    listings_path: Path,
    start: Optional[str],
    end: Optional[str],
) -> SourcePlan:
    if not listings_path.exists():
        raise UniverseSourceError(
            f"Downloaded CMC listings not found: {listings_path}\n"
            "Run: python3 scripts/build_cmc_history.py --start <YYYY-MM-01> --end <YYYY-MM-01> --top 100"
        )
    listings = pd.read_parquet(listings_path)
    if listings.empty:
        raise UniverseSourceError("Downloaded CMC listings parquet is empty.")
    required = {"cmc_id", "symbol", "name", "market_cap_usd", "market_cap_rank"}
    missing = required - set(listings.columns)
    if missing:
        raise UniverseSourceError(f"CMC listings missing columns: {sorted(missing)}")
    if "snapshot_date" not in listings.columns:
        raise UniverseSourceError("CMC listings missing 'snapshot_date' column.")
    listings = listings.copy()
    listings["snapshot_date"] = pd.to_datetime(listings["snapshot_date"], utc=True)
    if "raw_category_tags" in listings.columns:
        listings["raw_category_tags"] = listings["raw_category_tags"].apply(_as_tag_list)
    else:
        listings["raw_category_tags"] = [[] for _ in range(len(listings))]
    if start:
        listings = listings[listings["snapshot_date"] >= _to_utc(start)]
    if end:
        listings = listings[listings["snapshot_date"] <= _to_utc(end)]
    snap_dates = sorted(listings["snapshot_date"].dropna().unique())
    if not snap_dates:
        raise UniverseSourceError("No CMC snapshot dates in the requested range.")
    snapshots = []
    for snap in snap_dates:
        snap_ts = pd.Timestamp(snap)
        cand = listings[listings["snapshot_date"] == snap].drop(columns=["snapshot_date"]).copy()
        if not cand.empty:
            snapshots.append((snap_ts, cand))
    if not snapshots:
        raise UniverseSourceError("No eligible CMC download snapshots produced.")
    return SourcePlan(
        mode_name="historical_cmc_monthly",
        provider_name="coinmarketcap",
        processing="cmc",
        survivor_only=False,
        uses_cmc_id=True,
        snapshots=snapshots,
        historical_market_cap_available=True,
        requested_start=pd.Timestamp(snap_dates[0]).date().isoformat(),
        requested_end=pd.Timestamp(snap_dates[-1]).date().isoformat(),
        limitation=(
            "Point-in-time membership is real CMC cmc_rank per month (incl. inactive/delisted). "
            "Maturity, exchange tradability, and on-chain coverage are not re-verified historically."
        ),
    )


def _as_tag_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(t) for t in value]
    if hasattr(value, "tolist"):
        return [str(t) for t in value.tolist()]
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    return [str(value)]


# ------------------------------------------------------------- local_dataset
def load_local_dataset(dataset_path: Path, column_map: Optional[Dict[str, str]]) -> pd.DataFrame:
    if not dataset_path.exists():
        raise UniverseSourceError(
            f"Historical dataset not found: {dataset_path}\n"
            "Provide a free historical rankings dataset (CSV/Parquet/JSON) with columns "
            "date, symbol, market_cap [, name, rank, volume_24h, price]."
        )
    suffix = dataset_path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        raw = pd.read_parquet(dataset_path)
    elif suffix == ".json":
        raw = pd.read_json(dataset_path)
    else:
        raw = pd.read_csv(dataset_path)
    return _normalize_local_dataset(raw, dataset_path, column_map or {})


def _normalize_local_dataset(raw: pd.DataFrame, dataset_path: Path, column_map: Dict[str, str]) -> pd.DataFrame:
    overrides = {str(k): str(v) for k, v in column_map.items()}
    lower_to_actual = {c.lower(): c for c in raw.columns}
    resolved: Dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        if canonical in overrides and overrides[canonical] in raw.columns:
            resolved[canonical] = overrides[canonical]
            continue
        for alias in aliases:
            if alias in lower_to_actual:
                resolved[canonical] = lower_to_actual[alias]
                break
    missing = [c for c in ("date", "symbol", "market_cap") if c not in resolved]
    if missing:
        raise UniverseSourceError(
            f"Dataset {dataset_path.name} missing required column(s): {missing}. "
            f"Found: {list(raw.columns)}. Map via universe.column_map."
        )
    df = pd.DataFrame()
    df["date"] = pd.to_datetime(raw[resolved["date"]], utc=True, errors="coerce")
    df["symbol"] = raw[resolved["symbol"]].astype(str).str.upper().str.strip()
    df["name"] = raw[resolved["name"]].astype(str) if "name" in resolved else df["symbol"]
    df["market_cap_usd"] = pd.to_numeric(raw[resolved["market_cap"]], errors="coerce")
    df["volume_24h_usd"] = pd.to_numeric(raw[resolved["volume_24h"]], errors="coerce") if "volume_24h" in resolved else 0.0
    df["price_usd"] = pd.to_numeric(raw[resolved["price"]], errors="coerce") if "price" in resolved else 0.0
    df["market_cap_rank"] = pd.to_numeric(raw[resolved["rank"]], errors="coerce") if "rank" in resolved else pd.NA
    cat_col = next((lower_to_actual[a] for a in ("categories", "category", "tags") if a in lower_to_actual), None)
    df["categories"] = raw[cat_col].astype(str).fillna("") if cat_col else ""
    df = df.dropna(subset=["date", "symbol"])
    df = df[(df["symbol"] != "") & (df["market_cap_usd"].fillna(0) > 0)]
    df["date"] = df["date"].dt.tz_convert("UTC").dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        raise UniverseSourceError(f"Dataset {dataset_path.name} has no usable rows after cleaning.")
    return df


def _local_candidates_asof(dataset: pd.DataFrame, snapshot_date: pd.Timestamp, asof_days: int, candidate_n: int) -> pd.DataFrame:
    window_start = snapshot_date - pd.Timedelta(days=asof_days)
    sub = dataset[(dataset["date"] <= snapshot_date) & (dataset["date"] >= window_start)]
    if sub.empty:
        return _empty_candidates()
    latest = sub.sort_values("date").groupby("symbol", as_index=False).tail(1)
    latest = latest.sort_values("market_cap_usd", ascending=False).head(candidate_n).copy()
    if latest["market_cap_rank"].isna().all():
        latest["market_cap_rank"] = range(1, len(latest) + 1)
    latest["market_cap_rank"] = pd.to_numeric(latest["market_cap_rank"], errors="coerce")
    out = pd.DataFrame()
    out["symbol"] = latest["symbol"].values
    out["name"] = latest["name"].values
    out["slug"] = latest["symbol"].str.lower().values
    out["provider"] = "historical_free_dataset"
    out["provider_asset_id"] = latest["symbol"].values
    out["coin_id"] = latest["symbol"].str.lower().values
    out["cmc_id"] = pd.NA
    out["market_cap_rank"] = latest["market_cap_rank"].fillna(999999).astype(int).values
    out["market_cap_usd"] = latest["market_cap_usd"].values
    out["volume_24h_usd"] = latest["volume_24h_usd"].fillna(0.0).values
    out["price_usd"] = latest["price_usd"].fillna(0.0).values
    out["is_active_at_snapshot"] = True
    out["raw_category_tags"] = [[t for t in str(c).split(";") if t] for c in latest["categories"].values]
    out["first_seen_utc"] = ""
    out["num_market_pairs"] = 0
    out["source"] = "historical_free_dataset"
    return out.reindex(columns=CANDIDATE_COLUMNS)


def build_local_dataset(
    dataset_path: Path,
    column_map: Optional[Dict[str, str]],
    candidate_n: int,
    start: Optional[str],
    end: Optional[str],
    lookback_days: int,
    asof_staleness_days: int,
) -> SourcePlan:
    dataset = load_local_dataset(dataset_path, column_map)
    ds_min, ds_max = dataset["date"].min(), dataset["date"].max()
    end_ts = (_to_utc(end) if end else ds_max).replace(day=1)
    start_ts = (_to_utc(start) if start else max(ds_min, ds_max - pd.Timedelta(days=lookback_days))).replace(day=1)
    months = pd.date_range(start=start_ts, end=end_ts, freq="MS", tz="UTC")
    targets = [d for d in months if ds_min <= d <= ds_max]
    if not targets:
        raise UniverseSourceError("No monthly snapshot dates fall within the dataset's coverage.")
    snapshots = []
    for target in targets:
        cand = _local_candidates_asof(dataset, target, asof_staleness_days, candidate_n)
        if not cand.empty:
            snapshots.append((target, cand))
    if not snapshots:
        raise UniverseSourceError("Local dataset produced no usable monthly snapshots.")
    return SourcePlan(
        mode_name="historical_free_monthly",
        provider_name="historical_free_dataset",
        processing="market",
        survivor_only=False,
        uses_cmc_id=False,
        snapshots=snapshots,
        historical_market_cap_available=True,
        requested_start=targets[0].date().isoformat(),
        requested_end=targets[-1].date().isoformat(),
        limitation=(
            "Free historical dataset provides point-in-time market-cap membership. Maturity / exchange "
            "tradability / on-chain coverage gates apply only if enabled and verifiable for this source."
        ),
    )


# ------------------------------------------------------- coinmetrics PIT min_time
def load_coinmetrics_min_times(http, force_refresh: bool, live_api_enabled: bool) -> Dict[str, pd.Timestamp]:
    """Map SYMBOL -> earliest CoinMetrics community data date (for PIT on-chain coverage)."""
    out: Dict[str, pd.Timestamp] = {}
    try:
        payload = http.get_json(
            "coinmetrics",
            "https://community-api.coinmetrics.io/v4/catalog/assets",
            {},
            "catalog_assets",
            force_refresh=force_refresh,
            live_api_enabled=live_api_enabled,
        )
    except Exception:
        return out
    for row in payload.get("data", []):
        asset = str(row.get("asset") or "").upper()
        if not asset:
            continue
        earliest: Optional[pd.Timestamp] = None
        for metric in row.get("metrics", []) or []:
            for freq in metric.get("frequencies", []) or []:
                mt = freq.get("min_time")
                if not mt:
                    continue
                try:
                    ts = pd.Timestamp(mt)
                    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                except Exception:
                    continue
                if earliest is None or ts < earliest:
                    earliest = ts
        if earliest is not None:
            out[asset] = earliest
    return out
