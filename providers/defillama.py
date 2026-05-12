from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from providers.http_client import CachedHttpClient, ProviderUnavailableError, params_hash


METRIC_NAME_MAP = {
    "chain_tvl_usd": "chain_tvl_usd",
    "protocol_tvl_usd": "protocol_tvl_usd",
    "fees_usd": "fees_usd",
    "revenue_usd": "revenue_usd",
    "dex_volume_usd": "dex_volume_usd",
    "stablecoin_mcap_usd": "stablecoin_mcap_usd",
    "pool_tvl_usd": "pool_tvl_usd",
    "pool_apy": "pool_apy",
}

NON_NEGATIVE_METRICS = {
    "chain_tvl_usd",
    "protocol_tvl_usd",
    "fees_usd",
    "revenue_usd",
    "dex_volume_usd",
    "stablecoin_mcap_usd",
    "pool_tvl_usd",
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

CONTROLLED_CHAIN_ALIASES = {
    "BTC": {"bitcoin"},
    "ETH": {"ethereum"},
    "SOL": {"solana"},
    "BNB": {"bsc", "binance", "binance smart chain"},
    "TRX": {"tron"},
    "AVAX": {"avalanche"},
    "TON": {"ton"},
    "NEAR": {"near"},
    "SUI": {"sui"},
    "MNT": {"mantle"},
    "ADA": {"cardano"},
    "CRO": {"cronos"},
    "ARB": {"arbitrum"},
    "OP": {"optimism"},
    "APT": {"aptos"},
    "SEI": {"sei"},
    "INJ": {"injective"},
    "ATOM": {"cosmoshub", "cosmos"},
    "RUNE": {"thorchain"},
    "KAS": {"kaspa"},
}

CURATED_PROTOCOL_ALIASES = {
    "UNI": ["uniswap"],
    "AAVE": ["aave"],
    "CRV": ["curve-dex", "curve-finance"],
    "PENDLE": ["pendle"],
    "CAKE": ["pancakeswap"],
    "GNO": ["gnosis"],
    "RAY": ["raydium"],
    "SUSHI": ["sushiswap"],
}

CURATED_PROTOCOL_SYMBOLS = set(CURATED_PROTOCOL_ALIASES.keys())


@dataclass
class DeFiLlamaMapping:
    chain_slug: Optional[str]
    protocol_slug: Optional[str]


@dataclass
class DeFiLlamaFetchResult:
    mapping: DeFiLlamaMapping
    observations: pd.DataFrame
    fetched_metrics: List[str]
    failure_reason: str = ""


class DeFiLlamaProvider:
    """Cache-first DeFiLlama historical fetches with exact mapping rules."""

    def __init__(
        self,
        http_client: CachedHttpClient,
        config: Dict[str, Any],
        fixture_dir: Optional[Path | str] = None,
    ) -> None:
        self.http = http_client
        self.cfg = config
        self.base_url = str(config.get("base_url", "https://api.llama.fi")).rstrip("/")
        self.live_api_enabled = bool(config.get("live_api_enabled", True))
        self.force_refresh = bool(config.get("force_refresh", False))
        self.use_fixtures = bool(config.get("use_fixtures", False))
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.provider_key = "defillama"
        self._protocols: Optional[List[Dict[str, Any]]] = None
        self._chains: Optional[List[Dict[str, Any]]] = None
        self._stablecoins: Optional[List[Dict[str, Any]]] = None
        self._pools: Optional[List[Dict[str, Any]]] = None
        self._mapping_cache: Dict[str, DeFiLlamaMapping] = {}
        self._no_mapping_cache: set[str] = set()

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
            raise FileNotFoundError(f"Missing DeFiLlama fixture: {name}")
        with open(path, "r") as f:
            return json.load(f)

    def load_protocols(self) -> List[Dict[str, Any]]:
        if self._protocols is not None:
            return self._protocols
        if self.use_fixtures:
            payload = self._load_fixture_json("defillama_protocols.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/protocols",
                {},
                "protocols",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        self._protocols = list(payload if isinstance(payload, list) else payload.get("protocols", []))
        return self._protocols

    def load_chains(self) -> List[Dict[str, Any]]:
        if self._chains is not None:
            return self._chains
        if self.use_fixtures:
            payload = self._load_fixture_json("defillama_chains.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/v2/chains",
                {},
                "chains",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        self._chains = list(payload if isinstance(payload, list) else payload.get("chains", []))
        return self._chains

    def load_stablecoins(self) -> List[Dict[str, Any]]:
        if self._stablecoins is not None:
            return self._stablecoins
        if self.use_fixtures:
            payload = self._load_fixture_json("defillama_stablecoins.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/stablecoins",
                {},
                "stablecoins",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        self._stablecoins = list(payload.get("peggedAssets", [])) if isinstance(payload, dict) else []
        return self._stablecoins

    def load_pools(self) -> List[Dict[str, Any]]:
        if self._pools is not None:
            return self._pools
        if self.use_fixtures:
            payload = self._load_fixture_json("defillama_pools.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/pools",
                {},
                "pools",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        self._pools = list(payload.get("data", [])) if isinstance(payload, dict) else list(payload)
        return self._pools

    def resolve_mapping(self, symbol: str, coin_id: str = "", name: str = "") -> DeFiLlamaMapping:
        cache_key = f"{symbol.upper()}|{coin_id.lower()}|{name.lower()}"
        if cache_key in self._mapping_cache:
            return self._mapping_cache[cache_key]
        if cache_key in self._no_mapping_cache:
            return DeFiLlamaMapping(None, None)

        chain_slug = self._resolve_chain(symbol, coin_id, name)
        protocol_slug = self._resolve_protocol(symbol, coin_id, name)
        mapping = DeFiLlamaMapping(chain_slug=chain_slug, protocol_slug=protocol_slug)
        if not chain_slug and not protocol_slug:
            self._no_mapping_cache.add(cache_key)
        self._mapping_cache[cache_key] = mapping
        return mapping

    def _resolve_chain(self, symbol: str, coin_id: str, name: str) -> Optional[str]:
        symbol_upper = str(symbol).upper()
        coin_id_lower = str(coin_id).lower()
        name_lower = str(name).lower()
        alias_set = {symbol_upper.lower(), coin_id_lower, name_lower}
        alias_set.update(CONTROLLED_CHAIN_ALIASES.get(symbol_upper, set()))
        for row in self.load_chains():
            chain_name = str(row.get("name", "")).strip()
            token_symbol = str(row.get("tokenSymbol", "")).strip()
            gecko_id = str(row.get("gecko_id", "")).strip().lower()
            if not chain_name:
                continue
            if token_symbol.upper() == symbol_upper:
                return chain_name
            if gecko_id and gecko_id == coin_id_lower:
                return chain_name
            if chain_name.lower() in alias_set:
                return chain_name
        return None

    def _resolve_protocol(self, symbol: str, coin_id: str, name: str) -> Optional[str]:
        symbol_upper = str(symbol).upper()
        if symbol_upper not in CURATED_PROTOCOL_SYMBOLS:
            return None
        coin_id_lower = str(coin_id).lower()
        name_lower = str(name).lower()
        exact_slug: Dict[str, str] = {}
        exact_name: Dict[str, str] = {}
        exact_symbol: Dict[str, str] = {}
        exact_gecko: Dict[str, str] = {}
        for row in self.load_protocols():
            slug = str(row.get("slug", "")).strip()
            if not slug:
                continue
            proto_name = str(row.get("name", "")).strip().lower()
            proto_symbol = str(row.get("symbol", "")).strip().upper()
            gecko_id = str(row.get("gecko_id", "")).strip().lower()
            exact_slug[slug.lower()] = slug
            if proto_name:
                exact_name[proto_name] = slug
            if proto_symbol:
                exact_symbol[proto_symbol] = slug
            if gecko_id:
                exact_gecko[gecko_id] = slug
        for alias in CURATED_PROTOCOL_ALIASES.get(symbol_upper, []):
            if alias.lower() in exact_slug:
                return exact_slug[alias.lower()]
        if coin_id_lower in exact_gecko:
            return exact_gecko[coin_id_lower]
        if name_lower in exact_name:
            return exact_name[name_lower]
        if symbol_upper in exact_symbol:
            return exact_symbol[symbol_upper]
        return None

    def fetch_symbol_metrics(
        self,
        *,
        symbol: str,
        coin_id: str,
        name: str,
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> DeFiLlamaFetchResult:
        ok, reason = self.run_availability()
        if not ok:
            return DeFiLlamaFetchResult(DeFiLlamaMapping(None, None), pd.DataFrame(), [], reason)
        mapping = self.resolve_mapping(symbol, coin_id, name)
        observations: List[pd.DataFrame] = []
        fetched_metrics: List[str] = []
        failure_reasons: List[str] = []

        if "chain_tvl_usd" in requested_metrics and mapping.chain_slug:
            df = self._safe_fetch(
                lambda: self._fetch_chain_tvl(symbol=symbol, chain_slug=mapping.chain_slug, start_dt=start_dt, end_dt=end_dt)
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("chain_tvl_usd")
            else:
                failure_reasons.append("no_defillama_chain_tvl_data")
        elif "chain_tvl_usd" in requested_metrics:
            failure_reasons.append("no_defillama_chain_mapping")

        if "protocol_tvl_usd" in requested_metrics and mapping.protocol_slug:
            df = self._safe_fetch(
                lambda: self._fetch_protocol_tvl(symbol=symbol, protocol_slug=mapping.protocol_slug, start_dt=start_dt, end_dt=end_dt)
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("protocol_tvl_usd")
            else:
                failure_reasons.append("no_defillama_protocol_tvl_data")
        elif "protocol_tvl_usd" in requested_metrics:
            failure_reasons.append("no_defillama_protocol_mapping")

        if "fees_usd" in requested_metrics and mapping.protocol_slug:
            df = self._safe_fetch(
                lambda: self._fetch_protocol_summary(
                    symbol=symbol,
                    protocol_slug=mapping.protocol_slug,
                    metric_name="fees_usd",
                    endpoint="fees",
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("fees_usd")
            else:
                failure_reasons.append("no_defillama_fees_data")

        if "revenue_usd" in requested_metrics and mapping.protocol_slug:
            df = self._safe_fetch(
                lambda: self._fetch_protocol_summary(
                    symbol=symbol,
                    protocol_slug=mapping.protocol_slug,
                    metric_name="revenue_usd",
                    endpoint="fees",
                    start_dt=start_dt,
                    end_dt=end_dt,
                    summary_field="dailyRevenue",
                )
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("revenue_usd")
            else:
                failure_reasons.append("no_defillama_revenue_data")

        if "dex_volume_usd" in requested_metrics and mapping.protocol_slug:
            df = self._safe_fetch(
                lambda: self._fetch_protocol_summary(
                    symbol=symbol,
                    protocol_slug=mapping.protocol_slug,
                    metric_name="dex_volume_usd",
                    endpoint="dexs",
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("dex_volume_usd")
            else:
                failure_reasons.append("no_defillama_dex_volume_data")

        if "stablecoin_mcap_usd" in requested_metrics and mapping.chain_slug:
            df = self._safe_fetch(
                lambda: self._fetch_stablecoin_chain_chart(
                    symbol=symbol,
                    chain_slug=mapping.chain_slug,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            )
            if not df.empty:
                observations.append(df)
                fetched_metrics.append("stablecoin_mcap_usd")
            else:
                failure_reasons.append("no_defillama_stablecoin_data")

        if any(metric in requested_metrics for metric in ["pool_tvl_usd", "pool_apy"]):
            try:
                pool_df, pool_metrics = self._fetch_pool_metrics(
                    symbol=symbol,
                    protocol_slug=mapping.protocol_slug,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    requested_metrics=requested_metrics,
                )
            except (FileNotFoundError, ProviderUnavailableError):
                pool_df, pool_metrics = pd.DataFrame(), []
            if not pool_df.empty:
                observations.append(pool_df)
                fetched_metrics.extend(pool_metrics)
            elif "pool_tvl_usd" in requested_metrics or "pool_apy" in requested_metrics:
                failure_reasons.append("no_defillama_pool_mapping")

        if not observations:
            reason_text = ",".join(sorted(set(failure_reasons))) if failure_reasons else "no_defillama_mapping_or_data"
            return DeFiLlamaFetchResult(mapping, pd.DataFrame(), [], reason_text)
        merged = pd.concat(observations, ignore_index=True)
        merged = merged.drop_duplicates(["symbol", "date_ts", "metric_name", "source"]).sort_values(
            ["symbol", "date_ts", "metric_name"]
        ).reset_index(drop=True)
        return DeFiLlamaFetchResult(mapping, merged, fetched_metrics)

    def _safe_fetch(self, fetcher) -> pd.DataFrame:
        try:
            return fetcher()
        except (FileNotFoundError, ProviderUnavailableError):
            return pd.DataFrame()

    def _fetch_chain_tvl(self, *, symbol: str, chain_slug: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"defillama_historicalChainTvl_{chain_slug}.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/v2/historicalChainTvl/{chain_slug}",
                {},
                f"historical_chain_tvl_{chain_slug}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        return self._series_rows_to_df(
            symbol=symbol,
            provider_entity_id=chain_slug,
            provider_metric_name="chain_tvl_usd",
            metric_name="chain_tvl_usd",
            rows=rows,
            value_keys=["tvl", "totalLiquidityUSD", "liquidity"],
            start_dt=start_dt,
            end_dt=end_dt,
            data_type="chain_metric",
        )

    def _fetch_protocol_tvl(self, *, symbol: str, protocol_slug: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"defillama_protocol_{protocol_slug}.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/protocol/{protocol_slug}",
                {},
                f"protocol_{protocol_slug}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        rows = payload.get("tvl", []) if isinstance(payload, dict) else []
        return self._series_rows_to_df(
            symbol=symbol,
            provider_entity_id=protocol_slug,
            provider_metric_name="protocol_tvl_usd",
            metric_name="protocol_tvl_usd",
            rows=rows,
            value_keys=["totalLiquidityUSD", "tvl"],
            start_dt=start_dt,
            end_dt=end_dt,
            data_type="protocol_metric",
        )

    def _fetch_protocol_summary(
        self,
        *,
        symbol: str,
        protocol_slug: str,
        metric_name: str,
        endpoint: str,
        start_dt: datetime,
        end_dt: datetime,
        summary_field: str = "totalDataChart",
    ) -> pd.DataFrame:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"defillama_summary_{endpoint}_{protocol_slug}.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/summary/{endpoint}/{protocol_slug}",
                {},
                f"summary_{endpoint}_{protocol_slug}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        rows = payload.get(summary_field, []) if isinstance(payload, dict) else []
        value_keys = ["totalDataChart", "dailyFees", "dailyVolume", "dailyRevenue", "value"]
        return self._summary_rows_to_df(
            symbol=symbol,
            provider_entity_id=protocol_slug,
            provider_metric_name=metric_name,
            metric_name=metric_name,
            rows=rows,
            value_keys=value_keys,
            start_dt=start_dt,
            end_dt=end_dt,
            data_type="protocol_metric",
        )

    def _fetch_stablecoin_chain_chart(
        self,
        *,
        symbol: str,
        chain_slug: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        if self.use_fixtures:
            payload = self._load_fixture_json(f"defillama_stablecoincharts_{chain_slug}.json")
        else:
            payload = self.http.get_json(
                self.provider_key,
                f"{self.base_url}/stablecoincharts/{chain_slug}",
                {},
                f"stablecoincharts_{chain_slug}",
                force_refresh=self.force_refresh,
                live_api_enabled=self.live_api_enabled,
            )
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        return self._series_rows_to_df(
            symbol=symbol,
            provider_entity_id=chain_slug,
            provider_metric_name="stablecoin_mcap_usd",
            metric_name="stablecoin_mcap_usd",
            rows=rows,
            value_keys=["totalCirculatingUSD", "totalCirculating", "mcap", "totalBridgedToUSD"],
            start_dt=start_dt,
            end_dt=end_dt,
            data_type="chain_stablecoins",
        )

    def _fetch_pool_metrics(
        self,
        *,
        symbol: str,
        protocol_slug: Optional[str],
        requested_metrics: List[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[pd.DataFrame, List[str]]:
        if not protocol_slug:
            return pd.DataFrame(), []
        pool_match = None
        for row in self.load_pools():
            project = str(row.get("project", "")).strip().lower()
            token_symbol = str(row.get("symbol", "")).strip().upper()
            if project == protocol_slug.lower() or token_symbol == symbol.upper():
                pool_match = row
                break
        if not pool_match:
            return pd.DataFrame(), []
        observations: List[Dict[str, Any]] = []
        date_ts = _utc_timestamp(end_dt).normalize()
        if "pool_tvl_usd" in requested_metrics:
            value = pd.to_numeric(pd.Series([pool_match.get("tvlUsd")]), errors="coerce").iloc[0]
            if not pd.isna(value) and float(value) >= 0:
                observations.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": "pool_tvl_usd",
                        "metric_value": float(value),
                        "source": self.provider_key,
                        "provider_asset_id": str(pool_match.get("pool", protocol_slug)),
                        "provider_metric_name": "tvlUsd",
                        "provider_entity_id": str(pool_match.get("pool", protocol_slug)),
                        "data_type": "pool_metric",
                    }
                )
        if "pool_apy" in requested_metrics:
            value = pd.to_numeric(pd.Series([pool_match.get("apy")]), errors="coerce").iloc[0]
            if not pd.isna(value):
                observations.append(
                    {
                        "date_ts": date_ts,
                        "symbol": symbol,
                        "metric_name": "pool_apy",
                        "metric_value": float(value),
                        "source": self.provider_key,
                        "provider_asset_id": str(pool_match.get("pool", protocol_slug)),
                        "provider_metric_name": "apy",
                        "provider_entity_id": str(pool_match.get("pool", protocol_slug)),
                        "data_type": "pool_metric",
                    }
                )
        if not observations:
            return pd.DataFrame(), []
        return pd.DataFrame(observations), sorted(set(pd.DataFrame(observations)["metric_name"]))

    def _series_rows_to_df(
        self,
        *,
        symbol: str,
        provider_entity_id: str,
        provider_metric_name: str,
        metric_name: str,
        rows: List[Dict[str, Any]],
        value_keys: List[str],
        start_dt: datetime,
        end_dt: datetime,
        data_type: str,
    ) -> pd.DataFrame:
        observations: List[Dict[str, Any]] = []
        for row in rows:
            date_ts = pd.to_datetime(row.get("date"), unit="s", utc=True, errors="coerce")
            if pd.isna(date_ts):
                date_ts = pd.to_datetime(row.get("date"), utc=True, errors="coerce")
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            value = None
            for key in value_keys:
                if key in row:
                    value = row.get(key)
                    break
            numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.isna(numeric_value):
                continue
            if metric_name in NON_NEGATIVE_METRICS and float(numeric_value) < 0:
                continue
            observations.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "metric_name": METRIC_NAME_MAP.get(metric_name, metric_name),
                    "metric_value": float(numeric_value),
                    "source": self.provider_key,
                    "provider_asset_id": provider_entity_id,
                    "provider_metric_name": provider_metric_name,
                    "provider_entity_id": provider_entity_id,
                    "data_type": data_type,
                }
            )
        return pd.DataFrame(observations)

    def _summary_rows_to_df(
        self,
        *,
        symbol: str,
        provider_entity_id: str,
        provider_metric_name: str,
        metric_name: str,
        rows: List[Any],
        value_keys: List[str],
        start_dt: datetime,
        end_dt: datetime,
        data_type: str,
    ) -> pd.DataFrame:
        observations: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, list) and len(row) >= 2:
                date_ts = pd.to_datetime(row[0], unit="s", utc=True, errors="coerce")
                value = row[1]
            elif isinstance(row, dict):
                date_ts = pd.to_datetime(row.get("date"), unit="s", utc=True, errors="coerce")
                if pd.isna(date_ts):
                    date_ts = pd.to_datetime(row.get("date"), utc=True, errors="coerce")
                value = None
                for key in value_keys:
                    if key in row:
                        candidate = row.get(key)
                        if isinstance(candidate, list) and len(candidate) >= 2:
                            value = candidate[1]
                        else:
                            value = candidate
                        break
            else:
                continue
            if pd.isna(date_ts):
                continue
            date_ts = date_ts.normalize()
            if date_ts < _utc_timestamp(start_dt).normalize() or date_ts > _utc_timestamp(end_dt).normalize():
                continue
            numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.isna(numeric_value):
                continue
            if metric_name in NON_NEGATIVE_METRICS and float(numeric_value) < 0:
                continue
            observations.append(
                {
                    "date_ts": date_ts,
                    "symbol": symbol,
                    "metric_name": METRIC_NAME_MAP.get(metric_name, metric_name),
                    "metric_value": float(numeric_value),
                    "source": self.provider_key,
                    "provider_asset_id": provider_entity_id,
                    "provider_metric_name": provider_metric_name,
                    "provider_entity_id": provider_entity_id,
                    "data_type": data_type,
                }
            )
        return pd.DataFrame(observations)
