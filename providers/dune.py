from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass
class DuneFetchResult:
    observations: pd.DataFrame
    fetched_metrics: List[str]
    failure_reason: str = ""


class DuneProvider:
    """Optional Dune provider using configured query IDs."""

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
        self.provider_key = "dune"
        env_key = str(config.get("env_key", "DUNE_API_KEY"))
        self.api_key = os.getenv(env_key, "")
        self.query_ids = dict(config.get("query_ids", {}))

    def run_availability(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", False):
            return False, "provider_disabled_in_config"
        if self.use_fixtures:
            return True, ""
        if self.cfg.get("only_if_key_present", True) and not self.api_key:
            return False, "provider_unavailable_missing_api_key"
        if not self.query_ids:
            return False, "provider_unavailable_missing_query_ids"
        return True, ""

    def fetch_symbol_metrics(
        self,
        *,
        symbol: str,
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> DuneFetchResult:
        ok, reason = self.run_availability()
        if not ok:
            return DuneFetchResult(pd.DataFrame(), [], reason)
        query_id = self.query_ids.get(symbol.upper())
        if not query_id:
            return DuneFetchResult(pd.DataFrame(), [], "no_dune_query_mapping")
        if self.use_fixtures:
            payload = self._load_fixture_json(f"dune_{symbol.upper()}.json")
        else:
            headers = {"X-Dune-API-Key": self.api_key}
            payload = self.http.get_json(
                self.provider_key,
                f"https://api.dune.com/api/v1/query/{query_id}/results",
                {},
                f"{symbol.upper()}_{params_hash({'query_id': query_id})}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
                headers=headers,
            )
        rows = (((payload or {}).get("result") or {}).get("rows")) or []
        observations: List[Dict[str, Any]] = []
        for row in rows:
            date_ts = pd.to_datetime(row.get("date"), utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            numeric_value = pd.to_numeric(pd.Series([row.get("curated_protocol_metric")]), errors="coerce").iloc[0]
            if pd.isna(numeric_value):
                continue
            observations.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol.upper(),
                    "metric_name": "curated_protocol_metric",
                    "metric_value": float(numeric_value),
                    "source": self.provider_key,
                    "provider_asset_id": str(query_id),
                    "provider_metric_name": "curated_protocol_metric",
                    "provider_entity_id": str(query_id),
                    "data_type": "query_metric",
                }
            )
        if not observations:
            return DuneFetchResult(pd.DataFrame(), [], "no_dune_data")
        df = pd.DataFrame(observations)
        return DuneFetchResult(df, ["curated_protocol_metric"])

    def _fixture_path(self, name: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / name
        return path if path.exists() else None

    def _load_fixture_json(self, name: str) -> Any:
        path = self._fixture_path(name)
        if path is None:
            raise FileNotFoundError(f"Missing Dune fixture: {name}")
        with open(path, "r") as f:
            return json.load(f)
