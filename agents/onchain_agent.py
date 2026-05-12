from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from agents.base import AgentBase
from providers.blockchair import BlockchairProvider
from providers.coinmetrics import CoinMetricsProvider
from providers.defillama import DeFiLlamaProvider
from providers.dune import DuneProvider
from providers.etherscan import EtherscanProvider
from providers.http_client import CachedHttpClient, ProviderUnavailableError, RateLimitError
from providers.thegraph import TheGraphProvider


OBSERVATION_COLUMNS = [
    "date_ts",
    "symbol",
    "metric_name",
    "metric_value",
    "source",
    "provider_asset_id",
    "provider_metric_name",
    "provider_entity_id",
    "data_type",
    "snapshot_id",
    "fetched_at_utc",
    "is_forward_filled",
    "is_incomplete_dropped",
]

WIDE_COLUMNS = [
    "date_ts",
    "symbol",
    "adr_active_count",
    "tx_count",
    "realized_cap_usd",
    "mvrv_current",
    "nvt_adjusted",
    "fee_total_usd",
    "transfer_value_adjusted_usd",
    "current_supply",
    "market_cap_usd",
    "issuance_total_usd",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "revenue_usd",
    "dex_volume_usd",
    "stablecoin_mcap_usd",
    "pool_tvl_usd",
    "pool_apy",
    "gas_used",
    "transaction_count_proxy",
    "token_transfer_count_proxy",
    "protocol_volume_usd",
    "snapshot_id",
    "fetched_at_utc",
]

NON_NEGATIVE_METRICS = {
    "adr_active_count",
    "tx_count",
    "realized_cap_usd",
    "fee_total_usd",
    "transfer_value_adjusted_usd",
    "current_supply",
    "market_cap_usd",
    "issuance_total_usd",
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "revenue_usd",
    "dex_volume_usd",
    "stablecoin_mcap_usd",
    "pool_tvl_usd",
    "gas_used",
    "transaction_count_proxy",
    "token_transfer_count_proxy",
    "protocol_volume_usd",
}

PROVIDER_KEYS = ["coinmetrics", "defillama", "etherscan", "thegraph", "blockchair", "dune"]


@dataclass
class OnChainAssetRequest:
    symbol: str
    name: str
    coin_id: str
    provider_asset_id: str
    market_cap_rank: int
    universe_snapshot_id: str


@dataclass
class ProviderObservationResult:
    observations: pd.DataFrame
    fetched_metrics: List[str]
    available: bool
    failure_reason: str = ""


class OnChainAgentError(RuntimeError):
    pass


class OnChainAgent(AgentBase):
    """Research-grade on-chain and DeFi fundamentals ingestion."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.ocfg = self.cfg.get("onchain") or self.cfg.get("on_chain", {})
        self.fixture_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "onchain"
        self.cache_dir = self._resolve_dir(self.ocfg.get("cache_dir", "data/cache/onchain"))
        self.output_dir = self._resolve_dir(self.ocfg.get("output_dir", "data/raw/onchain"))
        self.http = CachedHttpClient(
            cache_dir=self.cache_dir,
            request_timeout_seconds=float(self.ocfg.get("request_timeout_seconds", 30)),
            min_seconds_between_requests=float(self.ocfg.get("min_seconds_between_requests", 1.0)),
            max_retries=int(self.ocfg.get("max_retries", 4)),
            backoff_base_seconds=float(self.ocfg.get("backoff_base_seconds", 3)),
            backoff_jitter_seconds=float(self.ocfg.get("backoff_jitter_seconds", 1.5)),
        )
        self.asset_requests: List[OnChainAssetRequest] = []
        self.market_calendar: Dict[str, set[pd.Timestamp]] = {}
        self.coverage_rows: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.limitations: List[str] = []
        self.temporarily_unavailable_providers: Dict[str, str] = {}
        self.providers_unavailable: Dict[str, str] = {}
        self.providers_enabled: List[str] = []
        self.universe_snapshot_date: Optional[pd.Timestamp] = None
        self.market_snapshot_id: Optional[str] = None
        self.requested_start: Optional[pd.Timestamp] = None
        self.requested_end: Optional[pd.Timestamp] = None
        self.providers = self._build_providers()

    def _build_providers(self) -> Dict[str, Any]:
        live = bool(self.ocfg.get("live_api_enabled", True))
        fixtures = bool(self.ocfg.get("use_fixtures", False))
        refresh = bool(self.ocfg.get("force_refresh", False))
        providers: Dict[str, Any] = {}
        cm_cfg = dict(self.ocfg.get("coinmetrics", {}))
        cm_cfg.setdefault("live_api_enabled", live)
        cm_cfg.setdefault("use_fixtures", fixtures)
        cm_cfg.setdefault("force_refresh", refresh)
        providers["coinmetrics"] = CoinMetricsProvider(self.http, cm_cfg, fixture_dir=self.fixture_dir)

        dl_cfg = dict(self.ocfg.get("defillama", {}))
        dl_cfg.setdefault("live_api_enabled", live)
        dl_cfg.setdefault("use_fixtures", fixtures)
        dl_cfg.setdefault("force_refresh", refresh)
        providers["defillama"] = DeFiLlamaProvider(self.http, dl_cfg, fixture_dir=self.fixture_dir)

        es_cfg = dict(self.ocfg.get("etherscan", {}))
        es_cfg.setdefault("live_api_enabled", live)
        es_cfg.setdefault("use_fixtures", fixtures)
        es_cfg.setdefault("force_refresh", refresh)
        providers["etherscan"] = EtherscanProvider(self.http, es_cfg, fixture_dir=self.fixture_dir)

        tg_cfg = dict(self.ocfg.get("thegraph", {}))
        tg_cfg.setdefault("live_api_enabled", live)
        tg_cfg.setdefault("use_fixtures", fixtures)
        tg_cfg.setdefault("force_refresh", refresh)
        providers["thegraph"] = TheGraphProvider(self.http, tg_cfg, fixture_dir=self.fixture_dir)

        bc_cfg = dict(self.ocfg.get("blockchair", {}))
        bc_cfg.setdefault("live_api_enabled", live)
        bc_cfg.setdefault("use_fixtures", fixtures)
        bc_cfg.setdefault("force_refresh", refresh)
        providers["blockchair"] = BlockchairProvider(self.http, bc_cfg, fixture_dir=self.fixture_dir)

        dune_cfg = dict(self.ocfg.get("dune", {}))
        dune_cfg.setdefault("live_api_enabled", live)
        dune_cfg.setdefault("use_fixtures", fixtures)
        dune_cfg.setdefault("force_refresh", refresh)
        providers["dune"] = DuneProvider(self.http, dune_cfg, fixture_dir=self.fixture_dir)
        return providers

    def _resolve_dir(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def _now_utc(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.asset_requests, self.market_snapshot_id = self._load_asset_requests()
        if not self.asset_requests:
            raise OnChainAgentError("No assets remained after universe/market intersection")
        requested_end = pd.to_datetime(self.ocfg["end_date"], utc=True).normalize() if self.ocfg.get("end_date") else self._requested_end_from_market()
        requested_start = (
            pd.to_datetime(self.ocfg["start_date"], utc=True).normalize()
            if self.ocfg.get("start_date")
            else requested_end - pd.Timedelta(days=int(self.ocfg.get("backfill_days", 365)) - 1)
        )
        self.requested_start = requested_start.normalize()
        self.requested_end = requested_end.normalize()
        self._init_provider_run_status()
        snapshot_ref = self.universe_snapshot_date.date().isoformat() if self.universe_snapshot_date is not None else "unknown"
        self.generate_snapshot_id(f"onchain:{snapshot_ref}:{self.requested_end.date().isoformat()}")
        self.logger.info(
            "OnChainAgent prepared | assets=%s | providers_enabled=%s | %s -> %s",
            len(self.asset_requests),
            self.providers_enabled,
            self.requested_start.date().isoformat(),
            self.requested_end.date().isoformat(),
        )

    def _init_provider_run_status(self) -> None:
        self.providers_enabled = []
        self.providers_unavailable = {}
        for provider_key in self.ocfg.get("provider_priority", PROVIDER_KEYS):
            provider = self.providers.get(provider_key)
            if provider is None:
                self.providers_unavailable[provider_key] = "provider_not_implemented"
                continue
            if hasattr(provider, "run_availability"):
                ok, reason = provider.run_availability()
            else:
                ok, reason = True, ""
            if ok:
                self.providers_enabled.append(provider_key)
            else:
                self.providers_unavailable[provider_key] = reason or "provider_unavailable"

    def _load_asset_requests(self) -> Tuple[List[OnChainAssetRequest], str]:
        universe_path = self.get_path("raw") / "universe" / "universe_monthly.parquet"
        market_cov_path = self.get_path("raw") / "market" / "market_coverage_report.parquet"
        market_manifest_path = self.get_path("raw") / "market" / "market_manifest.json"
        market_ohlcv_path = self.get_path("raw") / "market" / "market_ohlcv.parquet"
        if not universe_path.exists():
            raise FileNotFoundError("Missing universe_monthly.parquet. Run UniverseAgent first.")
        if not market_cov_path.exists():
            raise FileNotFoundError("Missing market_coverage_report.parquet. Run MarketDataAgent first.")
        if not market_manifest_path.exists():
            raise FileNotFoundError("Missing market_manifest.json. Run MarketDataAgent first.")
        if not market_ohlcv_path.exists():
            raise FileNotFoundError("Missing market_ohlcv.parquet. Run MarketDataAgent first.")

        universe = pd.read_parquet(universe_path)
        universe["snapshot_date"] = pd.to_datetime(universe["snapshot_date"], utc=True)
        universe_manifest_path = self.get_path("raw") / "universe" / "universe_manifest.json"
        if universe_manifest_path.exists():
            with open(universe_manifest_path, "r") as f:
                universe_manifest = json.load(f)
            if universe_manifest.get("survivor_only_universe") is False or universe_manifest.get("universe_mode") == "historical_cmc_monthly":
                self.limitations.append(
                    "Historical universe mode detected; OnChainAgent fetches unique/latest eligible assets and point-in-time membership filtering is downstream."
                )
        latest_snapshot = universe["snapshot_date"].max()
        latest = universe[(universe["snapshot_date"] == latest_snapshot) & (universe["is_eligible"])].copy()
        latest = latest.sort_values(["market_cap_rank", "symbol"], na_position="last")
        self.universe_snapshot_date = latest_snapshot

        coverage = pd.read_parquet(market_cov_path)
        required_cov_cols = {"symbol", "passed_qa", "is_full_ohlcv"}
        if not required_cov_cols.issubset(coverage.columns):
            raise OnChainAgentError(
                f"Market coverage missing required columns: {sorted(required_cov_cols - set(coverage.columns))}"
            )
        market_ok = coverage[(coverage["passed_qa"] == True) & (coverage["is_full_ohlcv"] == True)].copy()  # noqa: E712
        market_symbols = set(market_ok["symbol"].astype(str).str.upper())
        latest["symbol"] = latest["symbol"].astype(str).str.upper()
        latest = latest[latest["symbol"].isin(market_symbols)].copy()
        if latest.empty:
            raise OnChainAgentError("No assets remain after intersecting universe with valid market coverage")

        with open(market_manifest_path, "r") as f:
            market_manifest = json.load(f)
        market_snapshot_id = str(market_manifest.get("snapshot_id", ""))

        market = pd.read_parquet(market_ohlcv_path, columns=["symbol", "date_ts"])
        market["symbol"] = market["symbol"].astype(str).str.upper()
        market["date_ts"] = pd.to_datetime(market["date_ts"], utc=True).dt.normalize()
        self.market_calendar = {
            symbol: set(grp["date_ts"].tolist())
            for symbol, grp in market.groupby("symbol")
        }

        max_assets = self.ocfg.get("max_assets")
        if max_assets:
            latest = latest.head(int(max_assets)).copy()
        requests: List[OnChainAssetRequest] = []
        for _, row in latest.iterrows():
            requests.append(
                OnChainAssetRequest(
                    symbol=str(row["symbol"]).upper(),
                    name=str(row.get("name", row["symbol"])),
                    coin_id=str(row["coin_id"]),
                    provider_asset_id=str(row.get("provider_asset_id", row["coin_id"])),
                    market_cap_rank=int(row.get("market_cap_rank", 10**9)),
                    universe_snapshot_id=str(row.get("snapshot_id", "")),
                )
            )
        return requests, market_snapshot_id

    def _requested_end_from_market(self) -> pd.Timestamp:
        market_path = self.get_path("raw") / "market" / "market_ohlcv.parquet"
        market = pd.read_parquet(market_path, columns=["date_ts"])
        market["date_ts"] = pd.to_datetime(market["date_ts"], utc=True).dt.normalize()
        market_end = market["date_ts"].max()
        yesterday = self._now_utc().normalize() - pd.Timedelta(days=1)
        return min(market_end, yesterday)

    def run(self) -> Dict[str, Any]:
        if self.requested_start is None or self.requested_end is None:
            raise OnChainAgentError("OnChainAgent.prepare() did not set requested date window")
        all_observations: List[pd.DataFrame] = []
        requested_assets = len(self.asset_requests)
        for idx, request in enumerate(self.asset_requests, start=1):
            print(f"[onchain] Fetching {request.symbol} {idx}/{requested_assets}", flush=True)
            obs_df, coverage_row = self._fetch_asset(request)
            self.coverage_rows.append(coverage_row)
            if not obs_df.empty:
                all_observations.append(obs_df)

        observations = (
            pd.concat(all_observations, ignore_index=True)
            if all_observations else pd.DataFrame(columns=OBSERVATION_COLUMNS)
        )
        observations = self._finalize_observations(observations)
        wide = self._build_wide(observations)
        coverage = pd.DataFrame(self.coverage_rows)
        metrics_by_source = observations.groupby("source").size().to_dict() if not observations.empty else {}
        self.metrics["requested_assets"] = requested_assets
        persisted_symbols = set(observations["symbol"].astype(str)) if not observations.empty else set()
        self.metrics["assets_with_any_onchain"] = len(persisted_symbols)
        for provider_key in PROVIDER_KEYS:
            provider_symbols = set(
                observations.loc[observations["source"].astype(str) == provider_key, "symbol"].astype(str)
            ) if not observations.empty else set()
            self.metrics[f"assets_with_{provider_key}"] = len(provider_symbols)
        self.metrics["total_observations"] = int(len(observations))
        self.metrics["total_wide_rows"] = int(len(wide))
        self.metrics["defillama_observations"] = int((observations["source"] == "defillama").sum()) if not observations.empty else 0
        self.metrics["source_counts"] = json.dumps(metrics_by_source, sort_keys=True)
        fatal_errors = self._fatal_errors(coverage, observations)
        return {
            "observations": observations,
            "wide": wide,
            "coverage": coverage,
            "fatal_errors": fatal_errors,
        }

    def _fetch_asset(self, request: OnChainAssetRequest) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        provider_attempts: List[str] = []
        provider_failure_reasons: Dict[str, str] = {}
        frames: List[pd.DataFrame] = []
        fetched_metrics: List[str] = []
        provider_hits = {key: False for key in PROVIDER_KEYS}
        asset_started = time.monotonic()

        for provider_key in self.ocfg.get("provider_priority", PROVIDER_KEYS):
            if time.monotonic() - asset_started > float(self.ocfg.get("per_asset_timeout_seconds", 180)):
                provider_failure_reasons["asset"] = "asset_timeout"
                break
            provider_attempts.append(provider_key)
            if provider_key in self.providers_unavailable:
                provider_failure_reasons[provider_key] = self.providers_unavailable[provider_key]
                continue
            if provider_key in self.temporarily_unavailable_providers:
                provider_failure_reasons[provider_key] = self.temporarily_unavailable_providers[provider_key]
                continue
            try:
                result = self._run_provider(provider_key, request)
                if result.failure_reason:
                    provider_failure_reasons[provider_key] = result.failure_reason
                if not result.observations.empty:
                    frames.append(result.observations)
                    fetched_metrics.extend(result.fetched_metrics)
                    provider_hits[provider_key] = True
            except (RateLimitError, ProviderUnavailableError) as exc:
                reason = str(exc)
                provider_failure_reasons[provider_key] = reason
                self.temporarily_unavailable_providers[provider_key] = reason
            except Exception as exc:
                provider_failure_reasons[provider_key] = str(exc)

        attempted_observations = (
            pd.concat(frames, ignore_index=True)
            if frames else pd.DataFrame(columns=OBSERVATION_COLUMNS)
        )
        attempted_observations = self._normalize_asset_observations(request.symbol, attempted_observations)
        passed_qa = False
        failure_reason = ""
        persisted_observations = attempted_observations.copy()
        if attempted_observations.empty:
            failure_reason = self._summarize_failure(provider_failure_reasons) or "no_onchain_data"
        else:
            unique_days = attempted_observations["date_ts"].nunique()
            passed_qa = unique_days >= int(self.ocfg.get("min_history_days", 365))
            if not passed_qa:
                failure_reason = "below_min_history_days"
                persisted_observations = pd.DataFrame(columns=OBSERVATION_COLUMNS)

        persisted_sources = (
            set(persisted_observations["source"].astype(str))
            if not persisted_observations.empty else set()
        )

        coverage_row = {
            "symbol": request.symbol,
            "coin_id": request.coin_id,
            "market_cap_rank": request.market_cap_rank,
            "requested": True,
            "fetched_any": not persisted_observations.empty,
            "coinmetrics_available": "coinmetrics" in persisted_sources,
            "defillama_available": "defillama" in persisted_sources,
            "etherscan_available": "etherscan" in persisted_sources,
            "thegraph_available": "thegraph" in persisted_sources,
            "blockchair_available": "blockchair" in persisted_sources,
            "dune_available": "dune" in persisted_sources,
            "source_used": ",".join(sorted(persisted_sources)) if persisted_sources else "",
            "metrics_requested": self._metrics_requested_for_run(),
            "metrics_fetched": sorted(set(persisted_observations["metric_name"].astype(str))) if not persisted_observations.empty else [],
            "distinct_metrics_fetched": int(persisted_observations["metric_name"].nunique()) if not persisted_observations.empty else 0,
            "max_days_single_metric": int(persisted_observations.groupby("metric_name")["date_ts"].nunique().max()) if not persisted_observations.empty else 0,
            "primary_metric_days": {
                metric: int(persisted_observations.loc[persisted_observations["metric_name"] == metric, "date_ts"].nunique())
                for metric in self.ocfg.get("primary_metrics", ["adr_active_count", "tx_count", "chain_tvl_usd", "protocol_tvl_usd"])
            } if not persisted_observations.empty else {},
            "attempted_row_count_long": int(len(attempted_observations)),
            "persisted_row_count_long": int(len(persisted_observations)),
            "row_count_long": int(len(persisted_observations)),
            "row_count_wide": int(persisted_observations[["date_ts", "symbol"]].drop_duplicates().shape[0]) if not persisted_observations.empty else 0,
            "start_date": persisted_observations["date_ts"].min().isoformat() if not persisted_observations.empty else None,
            "end_date": persisted_observations["date_ts"].max().isoformat() if not persisted_observations.empty else None,
            "requested_start_date": self.requested_start.isoformat() if self.requested_start is not None else None,
            "requested_end_date": self.requested_end.isoformat() if self.requested_end is not None else None,
            "missing_days_by_metric": self._missing_days_by_metric(request.symbol, persisted_observations),
            "provider_attempts": provider_attempts,
            "provider_failure_reasons": provider_failure_reasons,
            "passed_qa": passed_qa,
            "failure_reason": failure_reason,
        }
        print(
            f"[onchain] QA {request.symbol}: observations={coverage_row['row_count_long']} "
            f"passed={str(passed_qa).lower()} reason={failure_reason}",
            flush=True,
        )
        return persisted_observations, coverage_row

    def _run_provider(self, provider_key: str, request: OnChainAssetRequest) -> ProviderObservationResult:
        provider = self.providers[provider_key]
        start_dt = self.requested_start.to_pydatetime() if self.requested_start is not None else None
        end_dt = self.requested_end.to_pydatetime() if self.requested_end is not None else None
        if provider_key == "coinmetrics":
            result = provider.get_asset_metrics_cached(
                symbol=request.symbol,
                coin_id=request.coin_id,
                name=request.name,
                asset_id=None,
                metrics=list(self.ocfg.get("coinmetrics", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(
                observations=result.observations,
                fetched_metrics=result.available_metrics,
                available=not result.observations.empty,
                failure_reason=result.failure_reason,
            )
        if provider_key == "defillama":
            result = provider.fetch_symbol_metrics(
                symbol=request.symbol,
                coin_id=request.coin_id,
                name=request.name,
                requested_metrics=list(self.ocfg.get("defillama", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(
                observations=result.observations,
                fetched_metrics=result.fetched_metrics,
                available=not result.observations.empty,
                failure_reason=result.failure_reason,
            )
        if provider_key == "etherscan":
            result = provider.fetch_symbol_metrics(
                symbol=request.symbol,
                requested_metrics=list(self.ocfg.get("etherscan", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(result.observations, result.fetched_metrics, not result.observations.empty, result.failure_reason)
        if provider_key == "thegraph":
            result = provider.fetch_symbol_metrics(
                symbol=request.symbol,
                requested_metrics=list(self.ocfg.get("thegraph", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(result.observations, result.fetched_metrics, not result.observations.empty, result.failure_reason)
        if provider_key == "blockchair":
            result = provider.fetch_symbol_metrics(
                symbol=request.symbol,
                requested_metrics=list(self.ocfg.get("blockchair", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(result.observations, result.fetched_metrics, not result.observations.empty, result.failure_reason)
        if provider_key == "dune":
            result = provider.fetch_symbol_metrics(
                symbol=request.symbol,
                requested_metrics=list(self.ocfg.get("dune", {}).get("metrics", [])),
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return ProviderObservationResult(result.observations, result.fetched_metrics, not result.observations.empty, result.failure_reason)
        return ProviderObservationResult(pd.DataFrame(), [], False, "provider_not_implemented")

    def _metrics_requested_for_run(self) -> List[str]:
        metrics: List[str] = []
        for provider_key in self.ocfg.get("provider_priority", PROVIDER_KEYS):
            metrics.extend(list((self.ocfg.get(provider_key, {}) or {}).get("metrics", [])))
        return metrics

    def _normalize_asset_observations(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=OBSERVATION_COLUMNS)
        normalized = df.copy()
        normalized["date_ts"] = pd.to_datetime(normalized["date_ts"], utc=True, errors="coerce").dt.normalize()
        normalized["metric_value"] = pd.to_numeric(normalized["metric_value"], errors="coerce")
        normalized["metric_value"] = normalized["metric_value"].replace([np.inf, -np.inf], pd.NA)
        normalized = normalized.dropna(subset=["date_ts", "metric_name", "metric_value"]).copy()
        normalized = normalized[normalized["symbol"].astype(str).str.upper() == symbol.upper()].copy()
        normalized = normalized[normalized["date_ts"] <= self.requested_end].copy()  # type: ignore[arg-type]
        normalized = normalized[normalized["date_ts"] >= self.requested_start].copy()  # type: ignore[arg-type]
        normalized = normalized[normalized["date_ts"] < self._now_utc().normalize()].copy()
        normalized["metric_name"] = normalized["metric_name"].astype(str)
        normalized = normalized[
            ~(
                normalized["metric_name"].isin(NON_NEGATIVE_METRICS)
                & (normalized["metric_value"] < 0)
            )
        ].copy()
        market_days = self.market_calendar.get(symbol.upper(), set())
        if market_days:
            normalized = normalized[normalized["date_ts"].isin(market_days)].copy()
        normalized["snapshot_id"] = self.snapshot_id
        normalized["fetched_at_utc"] = self._now_utc().isoformat()
        normalized["is_forward_filled"] = False
        normalized["is_incomplete_dropped"] = False
        normalized = normalized.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name", "source"]
        )
        for col in OBSERVATION_COLUMNS:
            if col not in normalized.columns:
                normalized[col] = pd.NA
        return normalized[OBSERVATION_COLUMNS].reset_index(drop=True)

    def _missing_days_by_metric(self, symbol: str, observations: pd.DataFrame) -> Dict[str, int]:
        if observations.empty:
            return {}
        market_days = self.market_calendar.get(symbol.upper(), set())
        if not market_days:
            return {}
        if self.requested_start is not None and self.requested_end is not None:
            market_days = {
                day for day in market_days
                if self.requested_start <= pd.Timestamp(day) <= self.requested_end
            }
        missing: Dict[str, int] = {}
        for metric_name, grp in observations.groupby("metric_name"):
            seen = set(pd.to_datetime(grp["date_ts"], utc=True).tolist())
            missing[str(metric_name)] = max(len(market_days - seen), 0)
        return missing

    def _build_wide(self, observations: pd.DataFrame) -> pd.DataFrame:
        if observations.empty:
            return pd.DataFrame(columns=WIDE_COLUMNS)
        wide = observations.pivot_table(
            index=["date_ts", "symbol"],
            columns="metric_name",
            values="metric_value",
            aggfunc="last",
        ).reset_index()
        wide.columns.name = None
        wide["snapshot_id"] = self.snapshot_id
        wide["fetched_at_utc"] = self._now_utc().isoformat()
        for col in WIDE_COLUMNS:
            if col not in wide.columns:
                wide[col] = pd.NA
        return wide[WIDE_COLUMNS].sort_values(["symbol", "date_ts"]).reset_index(drop=True)

    def _finalize_observations(self, observations: pd.DataFrame) -> pd.DataFrame:
        if observations.empty:
            return pd.DataFrame(columns=OBSERVATION_COLUMNS)
        observations = observations.copy()
        observations["metric_value"] = pd.to_numeric(observations["metric_value"], errors="coerce")
        observations = observations.dropna(subset=["metric_value", "date_ts", "metric_name", "symbol", "source"])
        bad_negative = observations["metric_name"].isin(NON_NEGATIVE_METRICS) & (observations["metric_value"] < 0)
        observations = observations.loc[~bad_negative].copy()
        observations = observations.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name", "source"]
        )
        return observations[OBSERVATION_COLUMNS].reset_index(drop=True)

    def _fatal_errors(self, coverage: pd.DataFrame, observations: pd.DataFrame) -> List[str]:
        errors: List[str] = []
        if coverage.empty:
            errors.append("coverage_report_empty")
        if observations.empty and self.ocfg.get("fail_on_empty_output", True):
            errors.append("onchain_observations_empty")

        assets_with_any = int(observations["symbol"].nunique()) if not observations.empty else 0
        total_observations = int(len(observations))
        assets_with_defillama = int(
            observations.loc[observations["source"].astype(str) == "defillama", "symbol"].nunique()
        ) if not observations.empty else 0
        defillama_observations = int((observations["source"] == "defillama").sum()) if not observations.empty else 0

        min_assets = int(self.ocfg.get("minimum_assets_with_any_onchain", 1))
        min_obs = int(self.ocfg.get("minimum_total_metric_observations", 1))
        min_defi_assets = int(self.ocfg.get("minimum_assets_with_defillama", 0))
        min_defi_obs = int(self.ocfg.get("minimum_defillama_observations", 0))
        min_distinct_metrics = int(self.ocfg.get("min_distinct_metrics_required", 1))
        primary_metrics = list(self.ocfg.get("primary_metrics", ["adr_active_count", "tx_count", "chain_tvl_usd", "protocol_tvl_usd"]))
        min_primary_days = int(self.ocfg.get("min_primary_metric_days_required", 0))

        if assets_with_any < min_assets and self.ocfg.get("fail_on_low_coverage", True):
            errors.append(f"assets_with_any_onchain_below_minimum:{assets_with_any}<{min_assets}")
        if total_observations < min_obs and self.ocfg.get("fail_on_low_coverage", True):
            errors.append(f"total_observations_below_minimum:{total_observations}<{min_obs}")
        if min_defi_assets > 0 and assets_with_defillama < min_defi_assets and self.ocfg.get("fail_on_low_coverage", True):
            errors.append(f"assets_with_defillama_below_minimum:{assets_with_defillama}<{min_defi_assets}")
        if min_defi_obs > 0 and defillama_observations < min_defi_obs and self.ocfg.get("fail_on_low_coverage", True):
            errors.append(f"defillama_observations_below_minimum:{defillama_observations}<{min_defi_obs}")
        if not observations.empty and min_distinct_metrics > 0:
            weak_assets = observations.groupby("symbol")["metric_name"].nunique().loc[lambda s: s < min_distinct_metrics]
            if not weak_assets.empty and self.ocfg.get("fail_on_low_coverage", True):
                errors.append(f"assets_below_min_distinct_metrics:{len(weak_assets)}")
        if not observations.empty and min_primary_days > 0 and primary_metrics:
            primary = observations[observations["metric_name"].isin(primary_metrics)]
            weak_primary = primary.groupby("symbol")["date_ts"].nunique().loc[lambda s: s < min_primary_days]
            if not weak_primary.empty and self.ocfg.get("fail_on_low_coverage", True):
                errors.append(f"assets_below_min_primary_metric_days:{len(weak_primary)}")
        if coverage is not None and not coverage.empty:
            if not coverage["provider_attempts"].apply(lambda x: bool(x)).any():
                errors.append("no_provider_attempts_recorded")
        return errors

    def _summarize_failure(self, reasons: Dict[str, str]) -> str:
        ordered: List[str] = []
        for key in PROVIDER_KEYS + ["asset"]:
            reason = reasons.get(key)
            if (
                reason
                and reason not in ordered
                and not reason.startswith("provider_disabled_in_config")
            ):
                ordered.append(reason)
        return "; ".join(ordered)

    def persist(self, result: Dict[str, Any]) -> None:
        observations = result["observations"].copy()
        wide = result["wide"].copy()
        coverage = result["coverage"].copy()
        fatal_errors = list(result.get("fatal_errors", []))

        self.output_dir.mkdir(parents=True, exist_ok=True)
        observations_path = self.output_dir / "onchain_observations.parquet"
        wide_path = self.output_dir / "onchain_wide.parquet"
        coverage_path = self.output_dir / "onchain_coverage_report.parquet"
        manifest_path = self.output_dir / "onchain_manifest.json"
        quality_path = self.output_dir / "data_quality_onchain.md"
        partition_root = self.output_dir / "partitioned"

        coverage = self._serialize_coverage_columns(coverage)
        if fatal_errors:
            self._write_quality_report(quality_path, coverage, observations, fatal_errors)
            self.output_paths.update({"data_quality_report": str(quality_path)})
            raise OnChainAgentError("; ".join(fatal_errors))
        observations.to_parquet(observations_path, index=False)
        wide.to_parquet(wide_path, index=False)
        coverage.to_parquet(coverage_path, index=False)
        self._write_partitioned(observations, partition_root)
        self._write_quality_report(quality_path, coverage, observations, fatal_errors)

        providers_used = sorted(set(observations["source"].astype(str))) if not observations.empty else []
        manifest = {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": self._now_utc().isoformat(),
            "universe_snapshot_date": self.universe_snapshot_date.isoformat() if self.universe_snapshot_date is not None else None,
            "market_snapshot_id": self.market_snapshot_id,
            "requested_assets": int(self.metrics.get("requested_assets", 0)),
            "assets_with_any_onchain": int(self.metrics.get("assets_with_any_onchain", 0)),
            "assets_with_coinmetrics": int(self.metrics.get("assets_with_coinmetrics", 0)),
            "assets_with_defillama": int(self.metrics.get("assets_with_defillama", 0)),
            "assets_with_etherscan": int(self.metrics.get("assets_with_etherscan", 0)),
            "assets_with_thegraph": int(self.metrics.get("assets_with_thegraph", 0)),
            "assets_with_blockchair": int(self.metrics.get("assets_with_blockchair", 0)),
            "assets_with_dune": int(self.metrics.get("assets_with_dune", 0)),
            "total_observations": int(self.metrics.get("total_observations", 0)),
            "total_wide_rows": int(self.metrics.get("total_wide_rows", 0)),
            "defillama_observations": int(self.metrics.get("defillama_observations", 0)),
            "backfill_days": int(self.ocfg.get("backfill_days", 365)),
            "min_history_days": int(self.ocfg.get("min_history_days", 365)),
            "providers_configured": list(self.ocfg.get("provider_priority", PROVIDER_KEYS)),
            "providers_enabled": self.providers_enabled,
            "providers_attempted_live": [key for key in self.providers_enabled if not self.ocfg.get(key, {}).get("use_fixtures", False)],
            "providers_used": providers_used,
            "providers_unavailable": {**self.providers_unavailable, **self.temporarily_unavailable_providers},
            "api_call_count_by_provider": dict(self.http.api_call_count_by_provider),
            "cache_hit_count_by_provider": dict(self.http.cache_hit_count_by_provider),
            "output_files": {
                "observations": str(observations_path),
                "wide": str(wide_path),
                "coverage_report": str(coverage_path),
                "manifest": str(manifest_path),
                "data_quality_report": str(quality_path),
                "partitioned": str(partition_root),
            },
            "warnings": self.warnings,
            "limitations": self.limitations + [
                "On-chain observations are not forward-filled; sparse metrics remain sparse and are lagged downstream by FeatureAgent."
            ],
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        self.output_paths.update(manifest["output_files"])

    def _serialize_coverage_columns(self, coverage: pd.DataFrame) -> pd.DataFrame:
        if coverage.empty:
            return coverage
        serialized = coverage.copy()
        json_cols = [
            "metrics_requested",
            "metrics_fetched",
            "missing_days_by_metric",
            "provider_attempts",
            "provider_failure_reasons",
        ]
        for col in json_cols:
            if col in serialized.columns:
                serialized[col] = serialized[col].apply(
                    lambda value: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                )
        return serialized

    def _write_partitioned(self, observations: pd.DataFrame, partition_root: Path) -> None:
        if partition_root.exists():
            for child in partition_root.glob("year=*"):
                if child.is_dir():
                    for nested in child.rglob("*.parquet"):
                        nested.unlink()
        if observations.empty:
            return
        df = observations.copy()
        df["year"] = pd.to_datetime(df["date_ts"], utc=True).dt.year
        df["month"] = pd.to_datetime(df["date_ts"], utc=True).dt.month
        for (year, month), grp in df.groupby(["year", "month"]):
            part_dir = partition_root / f"year={int(year)}" / f"month={int(month):02d}"
            part_dir.mkdir(parents=True, exist_ok=True)
            grp.drop(columns=["year", "month"]).to_parquet(part_dir / f"part-{self.run_id}.parquet", index=False)

    def _write_quality_report(
        self,
        path: Path,
        coverage: pd.DataFrame,
        observations: pd.DataFrame,
        fatal_errors: List[str],
    ) -> None:
        lines = [
            "# On-Chain Data Quality Report",
            "",
            f"- Requested assets: {int(self.metrics.get('requested_assets', 0))}",
            f"- Assets with any on-chain data: {int(self.metrics.get('assets_with_any_onchain', 0))}",
            f"- Assets with CoinMetrics: {int(self.metrics.get('assets_with_coinmetrics', 0))}",
            f"- Assets with DeFiLlama: {int(self.metrics.get('assets_with_defillama', 0))}",
            f"- Assets with Etherscan: {int(self.metrics.get('assets_with_etherscan', 0))}",
            f"- Assets with The Graph: {int(self.metrics.get('assets_with_thegraph', 0))}",
            f"- Assets with Blockchair: {int(self.metrics.get('assets_with_blockchair', 0))}",
            f"- Assets with Dune: {int(self.metrics.get('assets_with_dune', 0))}",
            f"- Total observations: {int(self.metrics.get('total_observations', 0))}",
            "",
            "## Coverage Failures",
        ]
        if coverage.empty:
            lines.append("- coverage_report_empty")
        else:
            failed = coverage[coverage["passed_qa"] != True]  # noqa: E712
            if failed.empty:
                lines.append("- none")
            else:
                for _, row in failed.iterrows():
                    lines.append(f"- {row['symbol']}: {row.get('failure_reason', '')}")
        if fatal_errors:
            lines.extend(["", "## Fatal Errors"])
            for error in fatal_errors:
                lines.append(f"- {error}")
        path.write_text("\n".join(lines))
