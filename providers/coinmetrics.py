from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

import pandas as pd

from providers.http_client import (
    CachedHttpClient,
    ProviderUnavailableError,
    RateLimitError,
    params_hash,
)


METRIC_NAME_MAP = {
    "AdrActCnt": "adr_active_count",
    "TxCnt": "tx_count",
    "CapRealUSD": "realized_cap_usd",
    "CapMVRVCur": "mvrv_current",
    "NVTAdj": "nvt_adjusted",
    "FeeTotUSD": "fee_total_usd",
    "TxTfrValAdjUSD": "transfer_value_adjusted_usd",
    "SplyCur": "current_supply",
    "CapMrktCurUSD": "market_cap_usd",
    "IssTotUSD": "issuance_total_usd",
    "HashRate": "hash_rate",
    "RevUSD": "revenue_usd",
}

NON_NEGATIVE_METRICS = {
    "AdrActCnt",
    "TxCnt",
    "CapRealUSD",
    "FeeTotUSD",
    "TxTfrValAdjUSD",
    "SplyCur",
    "CapMrktCurUSD",
    "IssTotUSD",
    "HashRate",
    "RevUSD",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass
class CoinMetricsFetchResult:
    asset_id: Optional[str]
    observations: pd.DataFrame
    available_metrics: List[str]
    failure_reason: str = ""


class CoinMetricsProvider:
    """Cache-first access to CoinMetrics Community asset metrics."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        config: Dict[str, Any],
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.cfg = config
        self.base_url = str(config.get("base_url", "https://community-api.coinmetrics.io/v4")).rstrip("/")
        self.live_api_enabled = bool(config.get("live_api_enabled", True))
        self.force_refresh = bool(config.get("force_refresh", False))
        self.use_fixtures = bool(config.get("use_fixtures", False))
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.provider_key = "coinmetrics"
        self._catalog_assets: Optional[List[Dict[str, Any]]] = None
        self._asset_metric_catalog: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._forbidden_asset_metrics: set[tuple[str, str]] = set()

    def run_availability(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", False):
            return False, "provider_disabled_in_config"
        return True, ""

    @property
    def api_call_count_by_provider(self) -> Dict[str, int]:
        return dict(self.http.api_call_count_by_provider)

    @property
    def cache_hit_count_by_provider(self) -> Dict[str, int]:
        return dict(self.http.cache_hit_count_by_provider)

    def _fixture_path(self, name: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / name
        return path if path.exists() else None

    def _load_fixture_json(self, name: str) -> Any:
        path = self._fixture_path(name)
        if path is None:
            raise FileNotFoundError(f"Missing CoinMetrics fixture: {name}")
        return pd.read_json(path).to_dict(orient="records") if path.suffix == ".jsonl" else __import__("json").load(open(path, "r"))

    def load_catalog_assets(self) -> List[Dict[str, Any]]:
        if self._catalog_assets is not None:
            return self._catalog_assets
        if self.use_fixtures:
            payload = self._load_fixture_json("coinmetrics_catalog_assets.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/catalog/assets",
                {},
                "catalog_assets",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        self._catalog_assets = list(payload.get("data", []))
        return self._catalog_assets

    def resolve_asset_id(self, symbol: str, coin_id: str = "", name: str = "") -> Optional[str]:
        symbol = str(symbol).strip().lower()
        coin_id = str(coin_id).strip().lower()
        name = str(name).strip().lower()
        assets = self.load_catalog_assets()
        asset_ids = {str(row.get("asset", "")).lower() for row in assets if row.get("asset")}
        if symbol in asset_ids:
            return symbol
        if coin_id in asset_ids:
            return coin_id
        exact_name_map = {
            str(row.get("name", "")).strip().lower(): str(row.get("asset", "")).lower()
            for row in assets
            if row.get("name") and row.get("asset")
        }
        if name in exact_name_map:
            return exact_name_map[name]
        return None

    def load_metric_availability(self, asset_ids: Iterable[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
        asset_ids = [str(asset).lower() for asset in asset_ids if asset]
        missing = [asset for asset in asset_ids if asset not in self._asset_metric_catalog]
        if not missing:
            return {asset: self._asset_metric_catalog.get(asset, {}) for asset in asset_ids}
        assets_param = ",".join(sorted(set(missing)))
        if self.use_fixtures:
            for asset in missing:
                payload = self._load_fixture_json(f"coinmetrics_catalog_asset_metrics_{asset}.json")
                self._asset_metric_catalog[asset] = self._parse_metric_catalog_rows(payload.get("data", []))
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/catalog-v2/asset-metrics",
                {"assets": assets_param},
                f"catalog_asset_metrics_{params_hash({'assets': assets_param})}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
            rows = payload.get("data", [])
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                asset = str(row.get("asset", "")).lower()
                if asset:
                    grouped.setdefault(asset, []).append(row)
            for asset in missing:
                self._asset_metric_catalog[asset] = self._parse_metric_catalog_rows(grouped.get(asset, []))
        return {asset: self._asset_metric_catalog.get(asset, {}) for asset in asset_ids}

    def _parse_metric_catalog_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        parsed: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if isinstance(row.get("metrics"), list):
                for metric_row in row.get("metrics", []):
                    metric = str(metric_row.get("metric", "")).strip()
                    if not metric:
                        continue
                    parsed[metric] = self._parse_metric_row(metric_row)
                continue
            metric = str(row.get("metric", "")).strip()
            if metric:
                parsed[metric] = self._parse_metric_row(row)
        return parsed

    @staticmethod
    def _parse_metric_row(row: Dict[str, Any]) -> Dict[str, Any]:
        frequencies = row.get("frequencies", [])
        flat_frequencies: List[str] = []
        preferred_min_time = row.get("min_time")
        preferred_max_time = row.get("max_time")
        for freq in frequencies:
            if isinstance(freq, dict):
                frequency = str(freq.get("frequency", ""))
                if frequency:
                    flat_frequencies.append(frequency)
                if frequency == "1d":
                    if freq.get("min_time"):
                        preferred_min_time = str(freq.get("min_time"))
                    if freq.get("max_time"):
                        preferred_max_time = str(freq.get("max_time"))
            else:
                flat_frequencies.append(str(freq))
        return {
            "min_time": preferred_min_time,
            "max_time": preferred_max_time,
            "frequencies": flat_frequencies or frequencies,
        }

    def get_supported_metrics(
        self,
        asset_id: str,
        requested_metrics: List[str],
        start_date: date,
        end_date: date,
    ) -> List[str]:
        availability = self.load_metric_availability([asset_id]).get(asset_id, {})
        supported: List[str] = []
        for metric in requested_metrics:
            if (asset_id, metric) in self._forbidden_asset_metrics:
                continue
            info = availability.get(metric)
            if not info:
                continue
            frequencies = set(info.get("frequencies", []))
            if frequencies and "1d" not in frequencies:
                continue
            min_time = info.get("min_time")
            max_time = info.get("max_time")
            if min_time:
                min_day = _utc_timestamp(min_time).date()
                if min_day > end_date:
                    continue
            if max_time:
                max_day = _utc_timestamp(max_time).date()
                if max_day < start_date:
                    continue
            supported.append(metric)
        return supported

    def get_asset_metrics_cached(
        self,
        *,
        symbol: str,
        coin_id: str,
        name: str,
        asset_id: Optional[str],
        metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> CoinMetricsFetchResult:
        asset_id = asset_id or self.resolve_asset_id(symbol, coin_id, name)
        if not asset_id:
            return CoinMetricsFetchResult(None, pd.DataFrame(), [], "no_coinmetrics_mapping")
        supported_metrics = self.get_supported_metrics(asset_id, metrics, start_dt.date(), end_dt.date())
        if not supported_metrics:
            return CoinMetricsFetchResult(asset_id, pd.DataFrame(), [], "no_supported_coinmetrics_metrics")

        try:
            payload_rows, accepted_metrics = self._fetch_timeseries_rows(asset_id, supported_metrics, start_dt, end_dt)
        except ProviderUnavailableError as exc:
            text = str(exc).lower()
            if "403" in text or "401" in text:
                for metric in supported_metrics:
                    self._forbidden_asset_metrics.add((asset_id, metric))
                return CoinMetricsFetchResult(asset_id, pd.DataFrame(), [], "metric_forbidden")
            raise
        observations = self._normalize_rows(
            symbol=symbol,
            asset_id=asset_id,
            rows=payload_rows,
            requested_metrics=accepted_metrics,
        )
        return CoinMetricsFetchResult(asset_id, observations, accepted_metrics)

    def _fetch_timeseries_rows(
        self,
        asset_id: str,
        metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        try:
            return self._fetch_timeseries_rows_once(asset_id, metrics, start_dt, end_dt)
        except ProviderUnavailableError as exc:
            text = str(exc).lower()
            if "403" not in text and "401" not in text:
                raise
            rows: List[Dict[str, Any]] = []
            accepted: List[str] = []
            any_success = False
            for metric in metrics:
                if (asset_id, metric) in self._forbidden_asset_metrics:
                    continue
                try:
                    metric_rows, _ = self._fetch_timeseries_rows_once(asset_id, [metric], start_dt, end_dt)
                    rows.extend(metric_rows)
                    accepted.append(metric)
                    any_success = True
                except ProviderUnavailableError as metric_exc:
                    metric_text = str(metric_exc).lower()
                    if "403" in metric_text or "401" in metric_text:
                        self._forbidden_asset_metrics.add((asset_id, metric))
                        continue
                    raise
            if not any_success:
                raise ProviderUnavailableError(self.provider_key, str(exc)) from exc
            return rows, accepted

    def _fetch_timeseries_rows_once(
        self,
        asset_id: str,
        metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"coinmetrics_timeseries_{asset_id}.json")
            return list(payload.get("data", [])), metrics
        params = {
            "assets": asset_id,
            "metrics": ",".join(metrics),
            "frequency": "1d",
            "page_size": 10000,
            "start_time": _utc_timestamp(start_dt).date().isoformat(),
            "end_time": _utc_timestamp(end_dt).date().isoformat(),
        }
        payload = self.http.get_json(
            self.provider_key,
            f"{self.base_url}/timeseries/asset-metrics",
            params,
            f"timeseries_{asset_id}_{params_hash(params)}",
            force_refresh=self.force_refresh,
            live_api_enabled=self.live_api_enabled,
        )
        rows = list(payload.get("data", []))
        next_page_url = payload.get("next_page_url")
        page = 1
        while next_page_url:
            page += 1
            parsed = urlparse(next_page_url)
            next_params = {
                key: values[0]
                for key, values in parse_qs(parsed.query).items()
                if values
            }
            next_payload = self.http.get_json(
                self.provider_key,
                f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                next_params,
                f"timeseries_{asset_id}_page_{page}_{params_hash(next_params)}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
            rows.extend(next_payload.get("data", []))
            next_page_url = next_payload.get("next_page_url")
        return rows, metrics

    def _normalize_rows(
        self,
        *,
        symbol: str,
        asset_id: str,
        rows: List[Dict[str, Any]],
        requested_metrics: List[str],
    ) -> pd.DataFrame:
        observations: List[Dict[str, Any]] = []
        for row in rows:
            date_ts = pd.to_datetime(row.get("time"), utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            for metric in requested_metrics:
                if metric not in row:
                    continue
                value = pd.to_numeric(pd.Series([row.get(metric)]), errors="coerce").iloc[0]
                if pd.isna(value):
                    continue
                if metric in NON_NEGATIVE_METRICS and float(value) < 0:
                    continue
                observations.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": METRIC_NAME_MAP.get(metric, metric),
                        "metric_value": float(value),
                        "source": self.provider_key,
                        "provider_asset_id": asset_id,
                        "provider_metric_name": metric,
                        "provider_entity_id": asset_id,
                        "data_type": "asset_metric",
                    }
                )
        if not observations:
            return pd.DataFrame()
        df = pd.DataFrame(observations)
        return df.drop_duplicates(["symbol", "date_ts", "metric_name"]).sort_values(
            ["symbol", "date_ts", "metric_name"]
        ).reset_index(drop=True)
