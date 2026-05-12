from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


NON_NEGATIVE_METRICS = {
    "protocol_volume_usd",
    "protocol_tvl_usd",
    "protocol_fees_usd",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass
class TheGraphFetchResult:
    observations: pd.DataFrame
    fetched_metrics: List[str]
    failure_reason: str = ""


class TheGraphProvider:
    """Optional configured-subgraph provider using GraphQL POST requests."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        config: Dict[str, Any],
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.cfg = config
        self.use_fixtures = bool(config.get("use_fixtures", False))
        self.live_api_enabled = bool(config.get("live_api_enabled", True))
        self.force_refresh = bool(config.get("force_refresh", False))
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.provider_key = "thegraph"
        env_key = str(config.get("env_key", "GRAPH_API_KEY"))
        self.api_key = os.getenv(env_key, "")
        self.subgraphs = dict(config.get("configured_subgraphs", {}))

    def run_availability(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", False):
            return False, "provider_disabled_in_config"
        if self.use_fixtures:
            return True, ""
        if not self.subgraphs and not self.api_key:
            return False, "provider_unavailable_missing_api_key_or_subgraph_config"
        if not self.subgraphs:
            return False, "provider_unavailable_missing_api_key_or_subgraph_config"
        return True, ""

    def fetch_symbol_metrics(
        self,
        *,
        symbol: str,
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> TheGraphFetchResult:
        ok, reason = self.run_availability()
        if not ok:
            return TheGraphFetchResult(pd.DataFrame(), [], reason)
        spec = self.subgraphs.get(symbol.upper())
        if not spec:
            return TheGraphFetchResult(pd.DataFrame(), [], "no_thegraph_subgraph_mapping")
        endpoint = str(spec.get("endpoint", "")).format(api_key=self.api_key)
        query = str(spec.get("query", ""))
        variables = dict(spec.get("variables", {}))
        root_field = str(spec.get("root_field", "data"))
        if not endpoint or not query or not root_field:
            return TheGraphFetchResult(pd.DataFrame(), [], "invalid_thegraph_subgraph_config")
        payload = {"query": query, "variables": variables}
        if self.use_fixtures:
            data = self._load_fixture_json(f"thegraph_{symbol.upper()}.json")
        else:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
            data = self.http.post_json(
                self.provider_key,
                endpoint,
                payload,
                f"{symbol.upper()}_{params_hash(payload)}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
                headers=headers,
            )
        root = data.get("data", {}).get(root_field, [])
        rows = root if isinstance(root, list) else []
        metric_fields = dict(spec.get("metric_fields", {}))
        observations: List[Dict[str, Any]] = []
        fetched_metrics: set[str] = set()
        for row in rows:
            date_raw = row.get("date") or row.get("timestamp")
            date_ts = pd.to_datetime(date_raw, unit="s", utc=True, errors="coerce")
            if pd.isna(date_ts):
                date_ts = pd.to_datetime(date_raw, utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            for metric_name, field_name in metric_fields.items():
                if metric_name not in requested_metrics:
                    continue
                numeric_value = pd.to_numeric(pd.Series([row.get(field_name)]), errors="coerce").iloc[0]
                if pd.isna(numeric_value):
                    continue
                if metric_name in NON_NEGATIVE_METRICS and float(numeric_value) < 0:
                    continue
                observations.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol.upper(),
                        "metric_name": metric_name,
                        "metric_value": float(numeric_value),
                        "source": self.provider_key,
                        "provider_asset_id": spec.get("id", symbol.upper()),
                        "provider_metric_name": field_name,
                        "provider_entity_id": spec.get("entity_id", root_field),
                        "data_type": "protocol_graph",
                    }
                )
                fetched_metrics.add(metric_name)
        if not observations:
            return TheGraphFetchResult(pd.DataFrame(), [], "no_thegraph_data")
        df = pd.DataFrame(observations)
        df = df.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name"]
        )
        return TheGraphFetchResult(df.reset_index(drop=True), sorted(fetched_metrics))

    def _fixture_path(self, name: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / name
        return path if path.exists() else None

    def _load_fixture_json(self, name: str) -> Any:
        path = self._fixture_path(name)
        if path is None:
            raise FileNotFoundError(f"Missing The Graph fixture: {name}")
        with open(path, "r") as f:
            return json.load(f)
