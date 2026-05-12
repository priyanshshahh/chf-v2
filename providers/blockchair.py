from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, params_hash


NON_NEGATIVE_METRICS = {
    "chain_transaction_count",
    "chain_output_volume",
    "chain_fee_total",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass
class BlockchairFetchResult:
    observations: pd.DataFrame
    fetched_metrics: List[str]
    failure_reason: str = ""


class BlockchairProvider:
    """Optional Blockchair chart provider for configured chains."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        config: Dict[str, Any],
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.cfg = config
        self.base_url = str(config.get("base_url", "https://api.blockchair.com")).rstrip("/")
        self.use_fixtures = bool(config.get("use_fixtures", False))
        self.live_api_enabled = bool(config.get("live_api_enabled", True))
        self.force_refresh = bool(config.get("force_refresh", False))
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.provider_key = "blockchair"
        self.chains = dict(config.get("chains", {}))

    def run_availability(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", False):
            return False, "provider_disabled_in_config"
        return True, ""

    def fetch_symbol_metrics(
        self,
        *,
        symbol: str,
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> BlockchairFetchResult:
        ok, reason = self.run_availability()
        if not ok:
            return BlockchairFetchResult(pd.DataFrame(), [], reason)
        chain_slug = self.chains.get(symbol.upper())
        if not chain_slug:
            return BlockchairFetchResult(pd.DataFrame(), [], "no_blockchair_chain_mapping")
        if self.use_fixtures:
            payload = self._load_fixture_json(f"blockchair_{symbol.upper()}.json")
        else:
            # Blockchair's chart endpoints vary by chain and public access can be blocked.
            # We only run when explicitly enabled and use a conservative chart API path.
            params = {"q": "transaction-count,output-volume-usd,total-fees-usd"}
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/{chain_slug}/charts",
                params,
                f"{symbol.upper()}_{params_hash(params)}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        chart_rows = data if isinstance(data, list) else []
        observations: List[Dict[str, Any]] = []
        metric_map = {
            "chain_transaction_count": "transaction_count",
            "chain_output_volume": "output_volume",
            "chain_fee_total": "fee_total",
        }
        for row in chart_rows:
            date_ts = pd.to_datetime(row.get("date"), utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            for metric_name, field_name in metric_map.items():
                if metric_name not in requested_metrics:
                    continue
                numeric_value = pd.to_numeric(pd.Series([row.get(field_name)]), errors="coerce").iloc[0]
                if pd.isna(numeric_value) or float(numeric_value) < 0:
                    continue
                observations.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol.upper(),
                        "metric_name": metric_name,
                        "metric_value": float(numeric_value),
                        "source": self.provider_key,
                        "provider_asset_id": chain_slug,
                        "provider_metric_name": field_name,
                        "provider_entity_id": chain_slug,
                        "data_type": "chain_chart",
                    }
                )
        if not observations:
            return BlockchairFetchResult(pd.DataFrame(), [], "no_blockchair_data")
        df = pd.DataFrame(observations)
        df = df.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name"]
        )
        return BlockchairFetchResult(df.reset_index(drop=True), sorted(set(df["metric_name"])))

    def _fixture_path(self, name: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / name
        return path if path.exists() else None

    def _load_fixture_json(self, name: str) -> Any:
        path = self._fixture_path(name)
        if path is None:
            raise FileNotFoundError(f"Missing Blockchair fixture: {name}")
        with open(path, "r") as f:
            return json.load(f)
