from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd
import yaml

from agents.base import AgentBase
from configs.config import get_config_hash
from providers.coincap import CoinCapProvider
from providers.coingecko import CoinGeckoProvider
from providers.coinmarketcap import CoinMarketCapProvider
from providers.coinpaprika import CoinPaprikaProvider
from providers.cryptocompare import CryptoCompareProvider
from providers.exchange_tradability import ExchangeTradabilityProvider
from providers.http_client import CachedHttpClient


CORE_COLUMNS = [
    "snapshot_date",
    "snapshot_year",
    "snapshot_month",
    "snapshot_id",
    "provider",
    "provider_asset_id",
    "cmc_id",
    "coin_id",
    "symbol",
    "name",
    "slug",
    "market_cap_rank",
    "market_cap_usd",
    "volume_24h_usd",
    "price_usd",
    "is_active_at_snapshot",
    "is_stablecoin",
    "is_wrapped",
    "is_bridged",
    "is_lst",
    "is_synthetic_pegged",
    "is_mature_365d",
    "is_exchange_tradable",
    "exchange",
    "exchange_symbol",
    "has_onchain_coverage",
    "onchain_coverage_source",
    "is_eligible",
    "exclusion_reason",
    "source",
    "created_at_utc",
]

MEMBERSHIP_COLUMNS = [
    "snapshot_date",
    "snapshot_month",
    "cmc_id",
    "symbol",
    "name",
    "slug",
    "market_cap_rank",
    "market_cap_usd",
    "is_eligible",
    "exclusion_reason",
    "source",
]


class UniverseValidationError(RuntimeError):
    pass


def _to_utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


class UniverseAgent(AgentBase):
    """Research-grade cache-first universe construction agent."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        snapshot_date: Optional[str] = None,
    ):
        super().__init__(config)
        self.snapshot_date_override = snapshot_date
        self.ucfg = self.cfg.get("universe", {})
        self.output_dir = self._resolve_output_dir()
        self.cache_dir = self._resolve_cache_dir()
        self.fixture_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "universe"
        self.exclusions_cfg: Dict[str, Any] = {}
        self.http = CachedHttpClient(
            cache_dir=self.cache_dir,
            request_timeout_seconds=float(self.ucfg.get("request_timeout_seconds", 30)),
            min_seconds_between_requests=float(self.ucfg.get("min_seconds_between_requests", 2.0)),
            max_retries=int(self.ucfg.get("max_retries", 5)),
            backoff_base_seconds=float(self.ucfg.get("backoff_base_seconds", 3)),
            backoff_jitter_seconds=float(self.ucfg.get("backoff_jitter_seconds", 1.5)),
        )
        self.providers_used: List[str] = []
        self.warnings: List[str] = []
        self.limitations: List[str] = []
        self.provider_name = ""
        self.universe_mode = "unknown"
        self.survivor_only_universe = True
        self.requested_start_date: Optional[str] = None
        self.requested_end_date: Optional[str] = None
        self.actual_start_date: Optional[str] = None
        self.actual_end_date: Optional[str] = None
        self.historical_snapshots_requested = 0
        self.historical_snapshots_created = 0
        self.historical_snapshot_limitation = ""
        self.unique_assets_total = 0
        self.average_monthly_eligible_count = 0.0
        self.min_monthly_eligible_count = 0
        self.max_monthly_eligible_count = 0
        self.cmc_provider: Optional[CoinMarketCapProvider] = None

    def _resolve_output_dir(self) -> Path:
        raw = self.ucfg.get("output_dir") or str(self.get_path("raw") / "universe")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def _resolve_cache_dir(self) -> Path:
        raw = self.ucfg.get("cache_dir", "data/cache")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if str(self.output_dir).lower().find("demo") >= 0 and self.ucfg.get("fail_on_demo_data", True):
            raise UniverseValidationError("Universe output path contains 'demo'")

        exclusions_path = Path(self.cfg["_project_root"]) / "configs" / "universe_exclusions.yaml"
        if not exclusions_path.exists():
            exclusions_path = Path(__file__).resolve().parent.parent / "configs" / "universe_exclusions.yaml"
        if not exclusions_path.exists():
            raise FileNotFoundError(f"Missing universe exclusions config: {exclusions_path}")
        with open(exclusions_path, "r") as f:
            self.exclusions_cfg = yaml.safe_load(f) or {}

        if self.ucfg.get("research_mode", False) and not self.ucfg.get("cache_enabled", True):
            raise UniverseValidationError("research_mode requires cache_enabled=true")
        if self.ucfg.get("use_cmc_historical_listings", False):
            self.cmc_provider = CoinMarketCapProvider(
                cache_dir=self.cache_dir,
                request_timeout_seconds=float(self.ucfg.get("request_timeout_seconds", 30)),
                min_seconds_between_requests=float(self.ucfg.get("min_seconds_between_requests", 2.0)),
                max_retries=int(self.ucfg.get("max_retries", 5)),
                backoff_base_seconds=float(self.ucfg.get("backoff_base_seconds", 3)),
                backoff_jitter_seconds=float(self.ucfg.get("backoff_jitter_seconds", 1.5)),
                live_api_enabled=bool(self.ucfg.get("live_api_enabled", True)),
                force_refresh=bool(self.ucfg.get("force_refresh", False)),
            )

    def run(self) -> Dict[str, Any]:
        if self.ucfg.get("use_cmc_historical_listings", False):
            return self._run_cmc_historical_mode()
        snapshot_dates = self._build_snapshot_dates()
        if not snapshot_dates:
            raise UniverseValidationError("No universe snapshot dates produced")

        all_universe: List[pd.DataFrame] = []
        all_exclusions: List[pd.DataFrame] = []
        coverage_rows: List[Dict[str, Any]] = []
        snapshot_hashes: Dict[str, str] = {}

        for snapshot_date in snapshot_dates:
            candidates, source_used = self._fetch_candidates(snapshot_date)
            if candidates.empty:
                raise UniverseValidationError(f"No candidate assets for {snapshot_date.date()}")
            processed, exclusions, coverage, snapshot_hash = self._process_snapshot(
                candidates, snapshot_date, source_used
            )
            all_universe.append(processed[processed["is_eligible"]].copy())
            all_exclusions.append(exclusions)
            coverage_rows.append(coverage)
            snapshot_hashes[snapshot_date.strftime("%Y-%m-%d")] = snapshot_hash

        universe_df = pd.concat(all_universe, ignore_index=True) if all_universe else pd.DataFrame()
        exclusions_df = pd.concat(all_exclusions, ignore_index=True) if all_exclusions else pd.DataFrame()
        coverage_df = pd.DataFrame(coverage_rows)
        self.metrics["eligible_count"] = int(len(universe_df))
        self.metrics["excluded_count"] = int(len(exclusions_df))
        self.metrics["snapshot_count"] = int(len(snapshot_dates))
        return {
            "universe": universe_df,
            "exclusions": exclusions_df,
            "coverage": coverage_df,
            "snapshot_hashes": snapshot_hashes,
        }

    def _build_cmc_snapshot_dates(self) -> List[pd.Timestamp]:
        end_ts = pd.Timestamp.now(tz="UTC").normalize()
        if self.ucfg.get("end_date"):
            end_ts = _to_utc_timestamp(self.ucfg["end_date"]).normalize()
        start_ts = end_ts - pd.Timedelta(days=int(self.ucfg.get("lookback_days", 1095)))
        if self.ucfg.get("start_date"):
            start_ts = _to_utc_timestamp(self.ucfg["start_date"]).normalize()
        freq = self.ucfg.get("snapshot_frequency", "MS")
        dates = list(pd.date_range(start=start_ts, end=end_ts, freq=freq, tz="UTC"))
        if not dates:
            return []
        self.provider_name = "coinmarketcap"
        self.providers_used.append("coinmarketcap")
        self.universe_mode = "historical_cmc_monthly"
        self.survivor_only_universe = False
        self.requested_start_date = dates[0].date().isoformat()
        self.requested_end_date = dates[-1].date().isoformat()
        self.actual_start_date = self.requested_start_date
        self.actual_end_date = self.requested_end_date
        self.historical_snapshots_requested = len(dates)
        self.historical_snapshots_created = len(dates)
        self.historical_snapshot_limitation = ""
        self.limitations.append(
            "CMC historical listings provide point-in-time market-cap membership, but historical maturity, exchange tradability, and on-chain coverage checks are not fully verified historically."
        )
        return dates

    def _run_cmc_historical_mode(self) -> Dict[str, Any]:
        if self.cmc_provider is None:
            raise UniverseValidationError("CMC provider is not initialized")
        snapshot_dates = self._build_cmc_snapshot_dates()
        if not snapshot_dates:
            raise UniverseValidationError("No CMC historical snapshot dates produced")
        universe_rows: List[pd.DataFrame] = []
        exclusions_rows: List[pd.DataFrame] = []
        membership_rows: List[pd.DataFrame] = []
        coverage_rows: List[Dict[str, Any]] = []
        snapshot_hashes: Dict[str, str] = {}
        provider_fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cmc" / "listings_historical_sample.json"

        for snapshot_date in snapshot_dates:
            fixture = provider_fixture if self.ucfg.get("use_fixtures", False) and provider_fixture.exists() else None
            candidates = self.cmc_provider.fetch_historical_listings(
                snapshot_date=snapshot_date,
                start=1,
                limit=int(self.ucfg.get("candidate_n", 300)),
                convert=str(self.ucfg.get("convert", "USD")),
                fixture_path=fixture,
                live_api_enabled=bool(self.ucfg.get("live_api_enabled", True)) and not bool(fixture),
                force_refresh=bool(self.ucfg.get("force_refresh", False)),
            )
            if candidates.empty:
                if self.ucfg.get("fail_on_empty_month", True):
                    raise UniverseValidationError(f"No CMC candidates for {snapshot_date.date()}")
                continue
            processed, exclusions, coverage, snapshot_hash = self._process_cmc_snapshot(candidates, snapshot_date)
            if processed.empty and self.ucfg.get("fail_on_empty_month", True):
                raise UniverseValidationError(f"No eligible CMC universe rows for {snapshot_date.date()}")
            universe_rows.append(processed.copy())
            exclusions_rows.append(exclusions.copy())
            membership_rows.append(self._membership_rows_from_processed(processed, exclusions))
            coverage_rows.append(coverage)
            snapshot_hashes[snapshot_date.strftime("%Y-%m-%d")] = snapshot_hash

        universe_df = pd.concat(universe_rows, ignore_index=True) if universe_rows else pd.DataFrame(columns=CORE_COLUMNS)
        exclusions_df = pd.concat(exclusions_rows, ignore_index=True) if exclusions_rows else pd.DataFrame()
        membership_df = pd.concat(membership_rows, ignore_index=True) if membership_rows else pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
        coverage_df = pd.DataFrame(coverage_rows)
        monthly_counts = coverage_df["eligible_count"].astype(int) if not coverage_df.empty else pd.Series(dtype=int)
        self.unique_assets_total = int(universe_df["cmc_id"].nunique()) if "cmc_id" in universe_df.columns and not universe_df.empty else 0
        self.average_monthly_eligible_count = float(monthly_counts.mean()) if not monthly_counts.empty else 0.0
        self.min_monthly_eligible_count = int(monthly_counts.min()) if not monthly_counts.empty else 0
        self.max_monthly_eligible_count = int(monthly_counts.max()) if not monthly_counts.empty else 0
        self.metrics["eligible_count"] = int(len(universe_df))
        self.metrics["excluded_count"] = int(len(exclusions_df))
        self.metrics["snapshot_count"] = int(len(snapshot_dates))
        return {
            "universe": universe_df,
            "exclusions": exclusions_df,
            "coverage": coverage_df,
            "membership": membership_df,
            "snapshot_hashes": snapshot_hashes,
        }

    def _process_cmc_snapshot(
        self,
        candidates: pd.DataFrame,
        snapshot_date: pd.Timestamp,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any], str]:
        now = pd.Timestamp.now(tz="UTC")
        df = candidates.copy()
        if "cmc_id" in df.columns:
            df["cmc_id"] = pd.to_numeric(df["cmc_id"], errors="coerce").astype("Int64")
        df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
        df["provider"] = "coinmarketcap"
        df["source"] = "coinmarketcap"
        df["snapshot_date"] = snapshot_date
        df["snapshot_year"] = int(snapshot_date.year)
        df["snapshot_month"] = int(snapshot_date.month)
        df["created_at_utc"] = now
        df["exchange"] = ""
        df["exchange_symbol"] = ""
        df["has_onchain_coverage"] = pd.NA
        df["onchain_coverage_source"] = ""
        df["is_mature_365d"] = pd.NA
        df["is_exchange_tradable"] = pd.NA
        flags = df.apply(self._classification_flags, axis=1, result_type="expand")
        df = pd.concat([df, flags], axis=1)
        df["exclusion_reason"] = ""
        df["is_eligible"] = True
        df.loc[df["market_cap_usd"].fillna(0) <= 0, ["is_eligible", "exclusion_reason"]] = [False, "missing_market_cap"]
        for col, reason in [
            ("is_stablecoin", "stablecoin"),
            ("is_wrapped", "wrapped_asset"),
            ("is_bridged", "bridged_asset"),
            ("is_lst", "liquid_staking_token"),
            ("is_synthetic_pegged", "synthetic_or_pegged_asset"),
        ]:
            mask = df[col].fillna(False)
            df.loc[mask, "is_eligible"] = False
            df.loc[mask & (df["exclusion_reason"] == ""), "exclusion_reason"] = reason
        eligible = df[df["is_eligible"]].sort_values(["market_cap_rank", "symbol"], ascending=[True, True]).head(
            int(self.ucfg.get("final_universe_n", 100))
        ).copy()
        final_ids = set(eligible["cmc_id"].dropna().astype(int))
        outside_mask = df["is_eligible"] & ~df["cmc_id"].isin(final_ids)
        df.loc[outside_mask, "is_eligible"] = False
        df.loc[outside_mask & (df["exclusion_reason"] == ""), "exclusion_reason"] = "outside_final_top_n"
        snapshot_hash = self._snapshot_hash_cmc(eligible)
        df["snapshot_id"] = snapshot_hash
        eligible = df[df["cmc_id"].isin(final_ids)].copy()
        exclusions = df[~df["is_eligible"]].copy()
        min_eligible = int(self.ucfg.get("minimum_eligible_n", 1))
        if len(eligible) < min_eligible and self.ucfg.get("fail_on_low_eligible_count", True):
            raise UniverseValidationError(
                f"Eligible CMC universe too small for {snapshot_date.date()}: {len(eligible)} < {min_eligible}"
            )
        coverage = self._cmc_coverage_row(df, eligible, exclusions, snapshot_date)
        return eligible.reindex(columns=CORE_COLUMNS), exclusions, coverage, snapshot_hash

    def _snapshot_hash_cmc(self, df: pd.DataFrame) -> str:
        cols = ["snapshot_date", "cmc_id", "symbol", "market_cap_usd", "market_cap_rank"]
        payload = df[cols].sort_values(["snapshot_date", "cmc_id"]).to_json(orient="records", date_format="iso")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _membership_rows_from_processed(self, eligible: pd.DataFrame, exclusions: pd.DataFrame) -> pd.DataFrame:
        merged = pd.concat([eligible.copy(), exclusions.copy()], ignore_index=True)
        if merged.empty:
            return pd.DataFrame(columns=MEMBERSHIP_COLUMNS)
        rows = merged.copy()
        rows["snapshot_date"] = pd.to_datetime(rows["snapshot_date"], utc=True)
        rows["snapshot_month"] = rows["snapshot_date"].dt.strftime("%Y-%m")
        return rows.reindex(columns=MEMBERSHIP_COLUMNS)

    def _cmc_coverage_row(
        self,
        df: pd.DataFrame,
        eligible: pd.DataFrame,
        exclusions: pd.DataFrame,
        snapshot_date: pd.Timestamp,
    ) -> Dict[str, Any]:
        excluded_reasons = exclusions["exclusion_reason"].fillna("") if not exclusions.empty else pd.Series(dtype=str)
        return {
            "snapshot_date": snapshot_date,
            "source_used": "coinmarketcap",
            "candidate_count": int(len(df)),
            "eligible_count": int(len(eligible)),
            "excluded_count": int(len(exclusions)),
            "final_count": int(len(eligible)),
            "stablecoin_excluded_count": int((excluded_reasons == "stablecoin").sum()),
            "wrapped_excluded_count": int((excluded_reasons == "wrapped_asset").sum()),
            "bridged_excluded_count": int((excluded_reasons == "bridged_asset").sum()),
            "lst_excluded_count": int((excluded_reasons == "liquid_staking_token").sum()),
            "maturity_excluded_count": 0,
            "tradability_excluded_count": 0,
            "onchain_coverage_excluded_count": 0,
            "market_cap_coverage_pct": float((pd.to_numeric(df["market_cap_usd"], errors="coerce") > 0).mean()) if len(df) else 0.0,
            "historical_market_cap_available": True,
            "limitations": "; ".join(self.limitations),
            "passed_validation": True,
        }

    def persist(self, result: Dict[str, Any]) -> None:
        universe_df: pd.DataFrame = result["universe"]
        exclusions_df: pd.DataFrame = result["exclusions"]
        coverage_df: pd.DataFrame = result["coverage"]
        membership_df: pd.DataFrame = result.get("membership", pd.DataFrame(columns=MEMBERSHIP_COLUMNS))

        if universe_df.empty and self.ucfg.get("fail_on_empty_month", True):
            raise UniverseValidationError("No eligible universe rows to persist")

        universe_path = self.output_dir / "universe_monthly.parquet"
        membership_path = self.output_dir / "universe_membership.parquet"
        exclusions_path = self.output_dir / "exclusions_monthly.parquet"
        coverage_path = self.output_dir / "universe_coverage_report.parquet"
        manifest_path = self.output_dir / "universe_manifest.json"

        universe_df = universe_df.reindex(columns=CORE_COLUMNS)
        membership_df = membership_df.reindex(columns=MEMBERSHIP_COLUMNS)
        exclusions_extra = CORE_COLUMNS + ["exclusion_stage", "exclusion_rule", "raw_category_tags"]
        exclusions_df = exclusions_df.reindex(columns=exclusions_extra)

        universe_df.to_parquet(universe_path, index=False)
        membership_df.to_parquet(membership_path, index=False)
        exclusions_df.to_parquet(exclusions_path, index=False)
        coverage_df.to_parquet(coverage_path, index=False)

        manifest = {
            "run_id": self.run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "config_hash": get_config_hash(self.cfg),
            "start_date": self.ucfg.get("start_date"),
            "end_date": self.ucfg.get("end_date"),
            "universe_mode": self.universe_mode,
            "survivor_only_universe": bool(self.survivor_only_universe),
            "provider": self.provider_name or ("coinmarketcap" if self.ucfg.get("use_cmc_historical_listings", False) else ""),
            "requested_start_date": self.requested_start_date,
            "requested_end_date": self.requested_end_date,
            "actual_start_date": self.actual_start_date,
            "actual_end_date": self.actual_end_date,
            "historical_snapshots_requested": int(self.historical_snapshots_requested),
            "historical_snapshots_created": int(self.historical_snapshots_created),
            "latest_snapshot_created": bool(self.universe_mode == "latest_snapshot_only" or (not self.ucfg.get("use_cmc_historical_listings", False) and self.historical_snapshots_created == 1)),
            "historical_snapshot_limitation": self.historical_snapshot_limitation,
            "survivorship_bias_disclosed": True,
            "candidate_n": self.ucfg.get("candidate_n"),
            "final_universe_n": self.ucfg.get("final_universe_n"),
            "minimum_eligible_n": self.ucfg.get("minimum_eligible_n"),
            "providers_used": sorted(set(self.providers_used)),
            "cache_enabled": bool(self.ucfg.get("cache_enabled", True)),
            "force_refresh": bool(self.ucfg.get("force_refresh", False)),
            "cache_hit_count": int(self.http.cache_hit_count)
            + (sum(self.cmc_provider.cache_hit_count_by_provider.values()) if self.cmc_provider is not None else 0),
            "api_call_count_by_provider": {
                **dict(self.http.api_call_count_by_provider),
                **(self.cmc_provider.api_call_count_by_provider if self.cmc_provider is not None else {}),
            },
            "failed_api_call_count_by_provider": dict(self.http.failed_api_call_count_by_provider),
            "monthly_snapshot_count": int(coverage_df["snapshot_date"].nunique()) if not coverage_df.empty else 0,
            "total_eligible_rows": int(len(universe_df)),
            "total_excluded_rows": int(len(exclusions_df)),
            "unique_assets_total": int(self.unique_assets_total),
            "average_monthly_eligible_count": float(self.average_monthly_eligible_count),
            "min_monthly_eligible_count": int(self.min_monthly_eligible_count),
            "max_monthly_eligible_count": int(self.max_monthly_eligible_count),
            "output_files": {
                "universe": str(universe_path),
                "membership": str(membership_path),
                "exclusions": str(exclusions_path),
                "coverage": str(coverage_path),
                "manifest": str(manifest_path),
            },
            "snapshot_hashes": result.get("snapshot_hashes", {}),
            "warnings": self.warnings,
            "limitations": self.limitations,
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True, default=str)

        self.output_paths = {
            "universe": str(universe_path),
            "membership": str(membership_path),
            "exclusions": str(exclusions_path),
            "coverage": str(coverage_path),
            "manifest": str(manifest_path),
        }
        self._validate_outputs()

    def _build_snapshot_dates(self) -> List[pd.Timestamp]:
        if self.snapshot_date_override:
            snapshot = _to_utc_timestamp(self.snapshot_date_override).replace(day=1)
            latest_available_month = pd.Timestamp.now(tz="UTC").replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            requested_snapshot = snapshot
            if requested_snapshot < latest_available_month:
                if bool(self.ucfg.get("require_true_historical_rankings", False)):
                    raise UniverseValidationError("True historical free-provider market-cap rankings are not available for snapshot_date_override")
                snapshot = latest_available_month
                self.universe_mode = "latest_snapshot_only"
                self.historical_snapshot_limitation = (
                    "snapshot_date_override requested a past month, but free-provider mode cannot provide "
                    "true historical rankings. Built only the latest/current snapshot."
                )
                self.limitations.append(self.historical_snapshot_limitation)
            else:
                self.universe_mode = "explicit_snapshot"
            self.requested_start_date = requested_snapshot.date().isoformat()
            self.requested_end_date = requested_snapshot.date().isoformat()
            self.actual_start_date = snapshot.date().isoformat()
            self.actual_end_date = snapshot.date().isoformat()
            self.historical_snapshots_requested = 1
            self.historical_snapshots_created = 1
            return [snapshot]

        end = self.ucfg.get("end_date")
        if end:
            end_ts = _to_utc_timestamp(end)
        else:
            end_ts = pd.Timestamp.now(tz="UTC")
        current_month = end_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        latest_available_month = pd.Timestamp.now(tz="UTC").replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        start = self.ucfg.get("start_date")
        require_true = bool(self.ucfg.get("require_true_historical_rankings", False))
        start_ts = _to_utc_timestamp(start).replace(day=1) if start else current_month
        requested_end_ts = end_ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if requested_end_ts < start_ts:
            raise UniverseValidationError(
                f"universe.end_date {requested_end_ts.date()} is before start_date {start_ts.date()}"
            )

        requested_months = pd.date_range(start=start_ts, end=requested_end_ts, freq="MS", tz="UTC")
        self.historical_snapshots_requested = int(len(requested_months))
        self.requested_start_date = start_ts.date().isoformat()
        self.requested_end_date = requested_end_ts.date().isoformat()

        if start:
            historical_request = start_ts < latest_available_month or self.historical_snapshots_requested > 1
            if historical_request and require_true:
                raise UniverseValidationError(
                    "True historical free-provider market-cap rankings are not available; "
                    "set require_true_historical_rankings=false for current snapshot mode."
                )
            if historical_request:
                if not self.ucfg.get("allow_latest_snapshot_only", True):
                    raise UniverseValidationError(
                        "Historical monthly universe was requested, but this free-provider path "
                        "can only create the latest snapshot. Set allow_latest_snapshot_only=true "
                        "to allow explicit latest_snapshot_only mode."
                    )
                self.universe_mode = "latest_snapshot_only"
                self.historical_snapshot_limitation = (
                    "Free provider path uses current market rankings only; historical monthly "
                    "rankings were not fabricated. Only the latest/current snapshot was built."
                )
                self.limitations.append(self.historical_snapshot_limitation)
                self.actual_start_date = latest_available_month.date().isoformat()
                self.actual_end_date = latest_available_month.date().isoformat()
                self.historical_snapshots_created = 1
                return [latest_available_month]

        self.universe_mode = "monthly"
        self.actual_start_date = current_month.date().isoformat()
        self.actual_end_date = current_month.date().isoformat()
        self.historical_snapshots_created = 1
        self.historical_snapshot_limitation = ""
        return [current_month]

    def _fetch_candidates(self, snapshot_date: pd.Timestamp) -> Tuple[pd.DataFrame, str]:
        provider_priority = self.ucfg.get("provider_priority", ["coingecko"])
        provider_map = {
            "coingecko": CoinGeckoProvider(self.http),
            "coinpaprika": CoinPaprikaProvider(self.http),
            "coincap": CoinCapProvider(self.http),
            "cryptocompare": CryptoCompareProvider(self.http),
        }
        candidate_n = int(self.ucfg.get("candidate_n", self.ucfg.get("top_n", 100)))
        vs_currency = self.ucfg.get("vs_currency", "usd")
        force_refresh = bool(self.ucfg.get("force_refresh", False))
        live_api_enabled = bool(self.ucfg.get("live_api_enabled", True))

        for provider_name in provider_priority:
            provider = provider_map.get(provider_name)
            if provider is None:
                continue
            fixture = self.fixture_dir / f"{provider_name}_markets.json" if self.ucfg.get("use_fixtures", False) else None
            try:
                df = provider.fetch_candidates(
                    candidate_n=candidate_n,
                    snapshot_date=snapshot_date,
                    vs_currency=vs_currency,
                    force_refresh=force_refresh,
                    live_api_enabled=live_api_enabled,
                    fixture_path=fixture,
                )
                if not df.empty and self._valid_candidate_frame(df):
                    self.providers_used.append(provider_name)
                    return df, provider_name
                self.warnings.append(f"{provider_name} returned empty/invalid candidate data")
            except Exception as exc:
                self.warnings.append(f"{provider_name} failed: {exc}")
                continue

        if self.ucfg.get("fail_on_provider_exhaustion", True):
            raise UniverseValidationError(f"All universe providers failed: {self.warnings}")
        return pd.DataFrame(), ""

    def _valid_candidate_frame(self, df: pd.DataFrame) -> bool:
        required = {"symbol", "market_cap_usd", "provider_asset_id", "name"}
        return required.issubset(df.columns) and len(df) > 0

    def _process_snapshot(
        self,
        candidates: pd.DataFrame,
        snapshot_date: pd.Timestamp,
        source_used: str,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any], str]:
        now = pd.Timestamp.now(tz="UTC")
        df = candidates.copy()
        df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
        df = df[df["symbol"] != ""].copy()
        df = df.drop_duplicates(subset=["symbol"], keep="first")
        df["market_cap_usd"] = pd.to_numeric(df["market_cap_usd"], errors="coerce").fillna(0.0)
        df["volume_24h_usd"] = pd.to_numeric(df["volume_24h_usd"], errors="coerce").fillna(0.0)
        df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce").fillna(0.0)
        df = df.sort_values(["market_cap_usd", "market_cap_rank"], ascending=[False, True])

        flags = df.apply(self._classification_flags, axis=1, result_type="expand")
        df = pd.concat([df, flags], axis=1)
        df["is_mature_365d"] = df.apply(lambda r: self._check_maturity(r, snapshot_date), axis=1)

        tradability = df["symbol"].apply(self._check_tradability)
        df["is_exchange_tradable"] = [x[0] for x in tradability]
        df["exchange"] = [x[1] for x in tradability]
        df["exchange_symbol"] = [x[2] for x in tradability]

        onchain = df["symbol"].apply(self._check_onchain_coverage)
        df["has_onchain_coverage"] = [x[0] for x in onchain]
        df["onchain_coverage_source"] = [x[1] for x in onchain]

        df["snapshot_date"] = snapshot_date
        df["snapshot_year"] = int(snapshot_date.year)
        df["snapshot_month"] = int(snapshot_date.month)
        df["created_at_utc"] = now
        df["source"] = source_used

        reasons = df.apply(self._exclusion_reason, axis=1, result_type="expand")
        df["is_eligible"] = reasons["is_eligible"]
        df["exclusion_stage"] = reasons["exclusion_stage"]
        df["exclusion_rule"] = reasons["exclusion_rule"]
        df["exclusion_reason"] = reasons["exclusion_reason"]

        eligible = df[df["is_eligible"]].copy().sort_values(
            ["market_cap_usd", "market_cap_rank"], ascending=[False, True]
        )
        final_n = int(self.ucfg.get("final_universe_n", self.ucfg.get("top_n", 100)))
        eligible = eligible.head(final_n).copy()
        final_symbols = set(eligible["symbol"])
        df.loc[~df["symbol"].isin(final_symbols) & df["is_eligible"], "is_eligible"] = False
        df.loc[~df["symbol"].isin(final_symbols) & (df["exclusion_reason"] == ""), "exclusion_reason"] = "outside_final_top_n"
        df.loc[~df["symbol"].isin(final_symbols) & (df["exclusion_rule"] == ""), "exclusion_rule"] = "final_universe_n"
        df.loc[~df["symbol"].isin(final_symbols) & (df["exclusion_stage"] == ""), "exclusion_stage"] = "final_selection"

        snapshot_hash = self._snapshot_hash(df[df["symbol"].isin(final_symbols)])
        df["snapshot_id"] = snapshot_hash
        eligible = df[df["symbol"].isin(final_symbols)].copy()
        exclusions = df[~df["is_eligible"]].copy()

        min_eligible = int(self.ucfg.get("minimum_eligible_n", 1))
        if len(eligible) < min_eligible and self.ucfg.get("fail_on_low_eligible_count", True):
            raise UniverseValidationError(
                f"Eligible universe too small for {snapshot_date.date()}: {len(eligible)} < {min_eligible}"
            )

        coverage = self._coverage_row(df, eligible, exclusions, snapshot_date, source_used)
        return eligible, exclusions, coverage, snapshot_hash

    def _classification_flags(self, row: pd.Series) -> Dict[str, bool]:
        symbol = str(row.get("symbol", "")).upper()
        name = str(row.get("name", "")).lower()
        tags = row.get("raw_category_tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        tags_l = [str(t).lower().replace(" ", "-") for t in tags]
        text = f"{symbol.lower()} {name} {' '.join(tags_l)}"
        deny_symbols = {s.upper() for s in self.exclusions_cfg.get("denylist_symbols", [])}

        def has_any(keys: str) -> bool:
            return any(str(k).lower() in text for k in self.exclusions_cfg.get(keys, []))

        cat_deny = set(self.exclusions_cfg.get("category_denylist", []))
        is_cat_denied = any(t in cat_deny for t in tags_l)
        is_stable = has_any("stable_keywords") or ("stablecoins" in tags_l)
        is_wrapped = has_any("wrapped_keywords") or (is_cat_denied and "wrapped-tokens" in tags_l)
        is_bridged = has_any("bridged_keywords") or "bridged-tokens" in tags_l
        is_lst = has_any("lst_keywords") or "liquid-staking-tokens" in tags_l
        is_synth = has_any("synthetic_pegged_keywords")
        if symbol in deny_symbols and not any([is_stable, is_wrapped, is_bridged, is_lst, is_synth]):
            is_synth = True
        return {
            "is_stablecoin": is_stable,
            "is_wrapped": is_wrapped,
            "is_bridged": is_bridged,
            "is_lst": is_lst,
            "is_synthetic_pegged": is_synth,
        }

    def _check_maturity(self, row: pd.Series, snapshot_date: pd.Timestamp) -> bool:
        if not self.ucfg.get("require_365d_maturity", True):
            return True
        first_seen = row.get("first_seen_utc")
        if first_seen:
            try:
                start_ts = pd.Timestamp(first_seen)
                if start_ts.tzinfo is None:
                    start_ts = start_ts.tz_localize("UTC")
                else:
                    start_ts = start_ts.tz_convert("UTC")
                return (snapshot_date - start_ts).days >= 365
            except Exception:
                pass
        symbol = str(row.get("symbol", "")).upper()
        maturity_map = self._load_maturity_map()
        started_at = maturity_map.get(symbol)
        if not started_at:
            return False
        try:
            start_ts = pd.Timestamp(started_at)
            if start_ts.tzinfo is None:
                start_ts = start_ts.tz_localize("UTC")
            else:
                start_ts = start_ts.tz_convert("UTC")
        except Exception:
            return False
        return (snapshot_date - start_ts).days >= 365

    def _load_maturity_map(self) -> Dict[str, str]:
        if hasattr(self, "_maturity_map"):
            return self._maturity_map
        fixture = self.fixture_dir / "coinpaprika_coins.json" if self.ucfg.get("use_fixtures", False) else None
        provider = CoinPaprikaProvider(self.http)
        try:
            rows = provider.fetch_coin_registry(
                force_refresh=bool(self.ucfg.get("force_refresh", False)),
                live_api_enabled=bool(self.ucfg.get("live_api_enabled", True)),
                fixture_path=fixture,
            )
        except Exception as exc:
            self.warnings.append(f"maturity registry unavailable: {exc}")
            rows = []
        mapping: Dict[str, str] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            started_at = row.get("started_at") or row.get("first_data_at")
            if symbol and started_at:
                mapping[symbol] = started_at
        self._maturity_map = mapping
        return mapping

    def _check_tradability(self, symbol: str) -> Tuple[bool, str, str]:
        if not self.ucfg.get("require_exchange_tradability", True):
            return True, "", ""
        if not hasattr(self, "_tradability_provider"):
            self._seed_fixture_tradability_cache()
            self._tradability_provider = ExchangeTradabilityProvider(
                cache_dir=self.cache_dir,
                live_api_enabled=bool(self.ucfg.get("live_api_enabled", True)),
                force_refresh=bool(self.ucfg.get("force_refresh", False)),
                exchanges=self.ucfg.get("exchange_tradability_sources", ["coinbase", "kraken"]),
            )
        return self._tradability_provider.check_symbol(symbol)

    def _seed_fixture_tradability_cache(self) -> None:
        if not self.ucfg.get("use_fixtures", False):
            return
        src = self.fixture_dir / "tradability_markets.json"
        if not src.exists():
            return
        with open(src, "r") as f:
            payload = json.load(f)
        for exchange, markets in payload.items():
            out = self.cache_dir / "tradability" / f"{exchange}_markets.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            if not out.exists() or self.ucfg.get("force_refresh", False):
                with open(out, "w") as wf:
                    json.dump(markets, wf, indent=2, sort_keys=True)

    def _check_onchain_coverage(self, symbol: str) -> Tuple[bool, str]:
        if not self.ucfg.get("require_onchain_coverage", True):
            return True, ""
        coverage = self._load_onchain_coverage()
        hit = coverage.get(symbol.upper())
        if hit:
            return True, str(hit)
        return False, ""

    def _load_onchain_coverage(self) -> Dict[str, str]:
        if hasattr(self, "_onchain_coverage"):
            return self._onchain_coverage
        fixture = self.fixture_dir / "onchain_coverage.json"
        if self.ucfg.get("use_fixtures", False) and fixture.exists():
            with open(fixture, "r") as f:
                self._onchain_coverage = json.load(f)
            return self._onchain_coverage
        # Lightweight live/catalog coverage: CoinMetrics catalog once.
        coverage: Dict[str, str] = {}
        try:
            payload = self.http.get_json(
                "coinmetrics",
                "https://community-api.coinmetrics.io/v4/catalog/assets",
                {},
                "catalog_assets",
                force_refresh=bool(self.ucfg.get("force_refresh", False)),
                live_api_enabled=bool(self.ucfg.get("live_api_enabled", True)),
            )
            for row in payload.get("data", []):
                asset = str(row.get("asset") or "").upper()
                if asset:
                    coverage[asset] = "coinmetrics"
        except Exception as exc:
            self.warnings.append(f"coinmetrics coverage unavailable: {exc}")
        self._onchain_coverage = coverage
        return coverage

    def _exclusion_reason(self, row: pd.Series) -> Dict[str, Any]:
        checks = [
            ("classification", "stablecoin", "stablecoin", row.get("is_stablecoin")),
            ("classification", "wrapped", "wrapped_asset", row.get("is_wrapped")),
            ("classification", "bridged", "bridged_asset", row.get("is_bridged")),
            ("classification", "lst", "liquid_staking_token", row.get("is_lst")),
            ("classification", "synthetic_pegged", "synthetic_or_pegged_asset", row.get("is_synthetic_pegged")),
            ("maturity", "require_365d_maturity", "maturity_unverified", not row.get("is_mature_365d")),
            ("tradability", "require_exchange_tradability", "not_exchange_tradable", not row.get("is_exchange_tradable")),
            ("onchain_coverage", "require_onchain_coverage", "no_onchain_coverage", not row.get("has_onchain_coverage")),
            ("market_data", "positive_market_cap", "missing_market_cap", float(row.get("market_cap_usd") or 0) <= 0),
        ]
        for stage, rule, reason, failed in checks:
            if failed:
                return {
                    "is_eligible": False,
                    "exclusion_stage": stage,
                    "exclusion_rule": rule,
                    "exclusion_reason": reason,
                }
        return {"is_eligible": True, "exclusion_stage": "", "exclusion_rule": "", "exclusion_reason": ""}

    def _snapshot_hash(self, df: pd.DataFrame) -> str:
        cols = ["snapshot_date", "symbol", "provider_asset_id", "market_cap_usd", "market_cap_rank"]
        payload = df[cols].sort_values(["snapshot_date", "symbol"]).to_json(
            orient="records", date_format="iso"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _coverage_row(
        self,
        df: pd.DataFrame,
        eligible: pd.DataFrame,
        exclusions: pd.DataFrame,
        snapshot_date: pd.Timestamp,
        source_used: str,
    ) -> Dict[str, Any]:
        excluded_reasons = exclusions["exclusion_reason"].fillna("") if not exclusions.empty else pd.Series(dtype=str)
        return {
            "snapshot_date": snapshot_date,
            "source_used": source_used,
            "candidate_count": int(len(df)),
            "eligible_count": int(len(eligible)),
            "excluded_count": int(len(exclusions)),
            "final_count": int(len(eligible)),
            "stablecoin_excluded_count": int((excluded_reasons == "stablecoin").sum()),
            "wrapped_excluded_count": int((excluded_reasons == "wrapped_asset").sum()),
            "bridged_excluded_count": int((excluded_reasons == "bridged_asset").sum()),
            "lst_excluded_count": int((excluded_reasons == "liquid_staking_token").sum()),
            "maturity_excluded_count": int((excluded_reasons == "maturity_unverified").sum()),
            "tradability_excluded_count": int((excluded_reasons == "not_exchange_tradable").sum()),
            "onchain_coverage_excluded_count": int((excluded_reasons == "no_onchain_coverage").sum()),
            "market_cap_coverage_pct": float((df["market_cap_usd"] > 0).mean()) if len(df) else 0.0,
            "historical_market_cap_available": False,
            "limitations": "; ".join(self.limitations),
            "passed_validation": False,
        }

    def _validate_outputs(self) -> None:
        universe_path = self.output_dir / "universe_monthly.parquet"
        membership_path = self.output_dir / "universe_membership.parquet"
        exclusions_path = self.output_dir / "exclusions_monthly.parquet"
        coverage_path = self.output_dir / "universe_coverage_report.parquet"
        manifest_path = self.output_dir / "universe_manifest.json"
        required_paths = [universe_path, exclusions_path, coverage_path, manifest_path]
        if self.ucfg.get("use_cmc_historical_listings", False):
            required_paths.append(membership_path)
        for path in required_paths:
            if not path.exists():
                raise UniverseValidationError(f"Missing output file: {path}")
            if "demo" in str(path).lower() and self.ucfg.get("fail_on_demo_data", True):
                raise UniverseValidationError(f"Demo path rejected: {path}")

        con = duckdb.connect(database=":memory:")
        universe = con.execute(f"SELECT * FROM read_parquet('{universe_path}')").df()
        coverage = con.execute(f"SELECT * FROM read_parquet('{coverage_path}')").df()
        min_eligible = int(self.ucfg.get("minimum_eligible_n", 1))

        failures = []
        if universe.empty:
            failures.append("universe_monthly.parquet is empty")
        if not coverage.empty and (coverage["eligible_count"] < min_eligible).any():
            failures.append("eligible_count below minimum_eligible_n")
        for col in ["is_stablecoin", "is_wrapped", "is_bridged", "is_lst", "is_synthetic_pegged"]:
            if col in universe.columns and universe[col].fillna(False).any():
                failures.append(f"eligible universe contains {col}=true")
        if (
            self.ucfg.get("require_exchange_tradability", True)
            and not self.ucfg.get("use_cmc_historical_listings", False)
            and (universe["is_exchange_tradable"] == False).any()  # noqa: E712
        ):
            failures.append("eligible universe contains non-tradable rows")
        if (
            self.ucfg.get("require_onchain_coverage", True)
            and not self.ucfg.get("use_cmc_historical_listings", False)
            and (universe["has_onchain_coverage"] == False).any()  # noqa: E712
        ):
            failures.append("eligible universe contains rows without on-chain coverage")
        if (universe["market_cap_usd"] <= 0).any():
            failures.append("eligible universe contains non-positive market_cap_usd")
        if universe["snapshot_id"].isna().any():
            failures.append("snapshot_id is null")
        dup_cols = ["snapshot_date", "cmc_id"] if self.ucfg.get("use_cmc_historical_listings", False) and "cmc_id" in universe.columns else ["snapshot_date", "symbol"]
        if universe.duplicated(dup_cols).any():
            failures.append(f"duplicate {' + '.join(dup_cols)} rows")
        if self.ucfg.get("use_cmc_historical_listings", False):
            if "cmc_id" not in universe.columns or universe["cmc_id"].isna().any():
                failures.append("eligible universe contains null cmc_id")

        if failures:
            raise UniverseValidationError("; ".join(failures))

        coverage["passed_validation"] = True
        coverage.to_parquet(coverage_path, index=False)

    def load_latest_universe(self) -> pd.DataFrame:
        path = self.output_dir / "universe_monthly.parquet"
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(path)
        if df.empty:
            return df
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True)
        latest = df["snapshot_date"].max()
        return df[df["snapshot_date"] == latest].copy()

    def get_eligible_symbols(self) -> List[str]:
        df = self.load_latest_universe()
        return df["symbol"].tolist() if not df.empty else []
