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
    "gas_used",
    "transaction_count_proxy",
    "token_transfer_count_proxy",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass
class EtherscanFetchResult:
    observations: pd.DataFrame
    fetched_metrics: List[str]
    failure_reason: str = ""


class EtherscanProvider:
    """Optional Etherscan V2 provider for chain-level daily proxy metrics."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        config: Dict[str, Any],
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.cfg = config
        self.base_url = str(config.get("base_url", "https://api.etherscan.io/v2/api")).rstrip("/")
        self.use_fixtures = bool(config.get("use_fixtures", False))
        self.live_api_enabled = bool(config.get("live_api_enabled", True))
        self.force_refresh = bool(config.get("force_refresh", False))
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.provider_key = "etherscan"
        env_key = str(config.get("env_key", "ETHERSCAN_API_KEY"))
        self.api_key = os.getenv(env_key, "")

    def run_availability(self) -> tuple[bool, str]:
        if not self.cfg.get("enabled", False):
            return False, "provider_disabled_in_config"
        if self.use_fixtures:
            return True, ""
        if self.cfg.get("only_if_key_present", True) and not self.api_key:
            return False, "provider_unavailable_missing_api_key"
        return True, ""

    def fetch_symbol_metrics(
        self,
        *,
        symbol: str,
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> EtherscanFetchResult:
        ok, reason = self.run_availability()
        if not ok:
            return EtherscanFetchResult(pd.DataFrame(), [], reason)
        chain_cfg = (self.cfg.get("chains") or {}).get(symbol.upper())
        if not chain_cfg:
            return EtherscanFetchResult(pd.DataFrame(), [], "no_etherscan_chain_mapping")
        chainid = chain_cfg.get("chainid")
        chain_name = str(chain_cfg.get("chain_name", symbol))
        frames: List[pd.DataFrame] = []
        fetched_metrics: List[str] = []
        action_map = {
            "gas_used": "dailygasused",
            "transaction_count_proxy": "dailytx",
            "token_transfer_count_proxy": "dailytokenerc20txns",
        }
        for metric_name in requested_metrics:
            action = action_map.get(metric_name)
            if not action:
                continue
            df = self._fetch_daily_stat(
                symbol=symbol,
                chainid=chainid,
                chain_name=chain_name,
                metric_name=metric_name,
                action=action,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            if not df.empty:
                frames.append(df)
                fetched_metrics.append(metric_name)
        if not frames:
            return EtherscanFetchResult(pd.DataFrame(), [], "no_etherscan_data")
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name"]
        )
        return EtherscanFetchResult(merged.reset_index(drop=True), fetched_metrics)

    def _fixture_path(self, name: str) -> Optional[Path]:
        if self.fixture_dir is None:
            return None
        path = self.fixture_dir / name
        return path if path.exists() else None

    def _load_fixture_json(self, name: str) -> Any:
        path = self._fixture_path(name)
        if path is None:
            raise FileNotFoundError(f"Missing Etherscan fixture: {name}")
        with open(path, "r") as f:
            return json.load(f)

    def _fetch_daily_stat(
        self,
        *,
        symbol: str,
        chainid: Any,
        chain_name: str,
        metric_name: str,
        action: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"etherscan_{symbol.upper()}_{action}.json")
        else:
            params = {
                "chainid": chainid,
                "module": "stats",
                "action": action,
                "startdate": _utc_timestamp(start_dt).date().isoformat(),
                "enddate": _utc_timestamp(end_dt).date().isoformat(),
                "sort": "asc",
                "apikey": self.api_key,
            }
            payload = self.http.get_json(
                self.provider_key,
                self.base_url,
                params,
                f"{symbol.upper()}_{action}_{params_hash(params)}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        rows = payload.get("result", []) if isinstance(payload, dict) else []
        observations: List[Dict[str, Any]] = []
        for row in rows:
            date_ts = pd.to_datetime(row.get("UTCDate"), utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            value = None
            for candidate in ["gasUsed", "transactionCount", "tokenTransferCount", "value"]:
                if candidate in row:
                    value = row.get(candidate)
                    break
            numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.isna(numeric_value) or float(numeric_value) < 0:
                continue
            observations.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol.upper(),
                    "metric_name": metric_name,
                    "metric_value": float(numeric_value),
                    "source": self.provider_key,
                    "provider_asset_id": str(chainid),
                    "provider_metric_name": action,
                    "provider_entity_id": chain_name,
                    "data_type": "chain_proxy",
                }
            )
        return pd.DataFrame(observations)
