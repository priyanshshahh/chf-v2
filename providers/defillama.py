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


# ============================================================================
# Standalone keyless data-breadth collector (CLI).
#
# Independent of the point-in-time ``DeFiLlamaProvider`` class above (which is
# consumed by agents/onchain_agent.py). This section adds a plain, batch-style
# collector — matching the house style of providers/funding_rates.py — that
# writes tidy long-format parquets under data/external/defillama/ for a future
# DeFi-yield sleeve and fundamental valuation:
#
#   tvl.parquet          protocol TVL time series  {protocol, date, tvl_usd, source}
#   yields.parquet       pool APY snapshot         {pool_id, project, symbol,
#                                                    chain, apy, tvl_usd, ts_utc, source}
#   chains.parquet       chain TVL time series     {chain, date, tvl_usd, source}
#   stablecoins.parquet  stablecoin circulating    {stablecoin, symbol,
#                                                    circulating_usd, ts_utc, source}
#
# DeFiLlama is fully keyless (api.llama.fi / yields.llama.fi / stablecoins.llama.fi).
# Idempotent upsert via src.cmc.storage.upsert. Per-endpoint failures log & skip.
#
# Runnable as:  .venv/bin/python -m providers.defillama
# ============================================================================

import argparse as _argparse
import time as _time

import requests as _requests
import yaml as _yaml

from configs.logging_config import get_logger as _get_logger
from src.cmc.storage import upsert as _upsert

_logger = _get_logger("providers.defillama.collector")

_CONFIG_PATH = "configs/data_sources.yaml"
_DEFAULT_BASE_URL = "https://api.llama.fi"
_DEFAULT_YIELDS_URL = "https://yields.llama.fi"
_DEFAULT_STABLE_URL = "https://stablecoins.llama.fi"
_DEFAULT_OUTPUT_DIR = "data/external/defillama"
_REQUEST_TIMEOUT = 30
_SLEEP = 1.0
_DEFAULT_PROTOCOLS = ["uniswap", "aave", "lido", "makerdao", "curve-dex"]
_DEFAULT_CHAINS = ["Ethereum", "Solana", "Bitcoin", "BSC", "Arbitrum"]
_DEFAULT_YIELDS_TOP_N = 200
_DEFAULT_STABLE_TOP_N = 50


def load_collector_config(config_path: "str | Path" = _CONFIG_PATH) -> Dict[str, Any]:
    """Load the ``defillama`` section of data_sources.yaml (best effort)."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return (_yaml.safe_load(f) or {}).get("defillama", {}) or {}


def _get_json(session: "_requests.Session", url: str, params: Optional[dict] = None) -> Any:
    """Single network chokepoint for the collector; tests monkeypatch this."""
    resp = session.get(url, params=params or {}, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _to_daily_utc(value: Any) -> Optional[pd.Timestamp]:
    """Coerce an epoch-seconds or date-like value to a UTC-normalized Timestamp."""
    ts = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
    if pd.isna(ts):
        ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.normalize()


def parse_protocol_tvl(payload: Any, protocol: str) -> List[Dict[str, Any]]:
    """Parse /protocol/{slug} -> rows {protocol, date, tvl_usd, source}."""
    rows: List[Dict[str, Any]] = []
    series = payload.get("tvl", []) if isinstance(payload, dict) else []
    for item in series:
        date = _to_daily_utc(item.get("date"))
        value = pd.to_numeric(pd.Series([item.get("totalLiquidityUSD", item.get("tvl"))]), errors="coerce").iloc[0]
        if date is None or pd.isna(value) or float(value) < 0:
            continue
        rows.append({"protocol": protocol, "date": date, "tvl_usd": float(value), "source": "defillama"})
    return rows


def parse_chain_tvl(payload: Any, chain: str) -> List[Dict[str, Any]]:
    """Parse /v2/historicalChainTvl/{chain} -> rows {chain, date, tvl_usd, source}."""
    rows: List[Dict[str, Any]] = []
    series = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
    for item in series:
        date = _to_daily_utc(item.get("date"))
        value = pd.to_numeric(pd.Series([item.get("tvl", item.get("totalLiquidityUSD"))]), errors="coerce").iloc[0]
        if date is None or pd.isna(value) or float(value) < 0:
            continue
        rows.append({"chain": chain, "date": date, "tvl_usd": float(value), "source": "defillama"})
    return rows


def parse_pools(payload: Any, top_n: int, ts_utc: pd.Timestamp) -> List[Dict[str, Any]]:
    """Parse /pools -> top-N (by TVL) rows of pool APY + TVL, stamped ``ts_utc``."""
    data = payload.get("data", []) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
    rows: List[Dict[str, Any]] = []
    for item in data:
        tvl = pd.to_numeric(pd.Series([item.get("tvlUsd")]), errors="coerce").iloc[0]
        pool_id = item.get("pool")
        if pool_id in (None, "") or pd.isna(tvl):
            continue
        apy = pd.to_numeric(pd.Series([item.get("apy")]), errors="coerce").iloc[0]
        rows.append(
            {
                "pool_id": str(pool_id),
                "project": str(item.get("project", "")),
                "symbol": str(item.get("symbol", "")),
                "chain": str(item.get("chain", "")),
                "apy": None if pd.isna(apy) else float(apy),
                "tvl_usd": float(tvl),
                "ts_utc": ts_utc,
                "source": "defillama",
            }
        )
    rows.sort(key=lambda r: r["tvl_usd"], reverse=True)
    return rows[:top_n]


def parse_stablecoins(payload: Any, top_n: int, ts_utc: pd.Timestamp) -> List[Dict[str, Any]]:
    """Parse /stablecoins -> top-N circulating rows, stamped ``ts_utc``."""
    assets = payload.get("peggedAssets", []) if isinstance(payload, dict) else []
    rows: List[Dict[str, Any]] = []
    for item in assets:
        circ = item.get("circulating", {})
        value = circ.get("peggedUSD") if isinstance(circ, dict) else None
        value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        name = item.get("name")
        if name in (None, "") or pd.isna(value) or float(value) < 0:
            continue
        rows.append(
            {
                "stablecoin": str(name),
                "symbol": str(item.get("symbol", "")),
                "circulating_usd": float(value),
                "ts_utc": ts_utc,
                "source": "defillama",
            }
        )
    rows.sort(key=lambda r: r["circulating_usd"], reverse=True)
    return rows[:top_n]


def collect_tvl(output_dir, cfg, session, write=True) -> pd.DataFrame:
    """Collect protocol TVL time series -> <output_dir>/tvl.parquet."""
    base_url = str(cfg.get("base_url", _DEFAULT_BASE_URL)).rstrip("/")
    protocols = cfg.get("protocols", _DEFAULT_PROTOCOLS)
    rows: List[Dict[str, Any]] = []
    for slug in protocols:
        try:
            payload = _get_json(session, f"{base_url}/protocol/{slug}")
            r = parse_protocol_tvl(payload, slug)
            rows.extend(r)
            _logger.info("defillama protocol tvl %s: %d rows", slug, len(r))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("defillama protocol tvl %s failed: %s", slug, exc)
        _time.sleep(_SLEEP)
    df = pd.DataFrame(rows, columns=["protocol", "date", "tvl_usd", "source"])
    if not df.empty and write:
        total = _upsert(df, ["protocol", "date"], Path(output_dir) / "tvl.parquet")
        _logger.info("defillama tvl: wrote %d new, %d total", len(df), total)
    return df


def collect_chains(output_dir, cfg, session, write=True) -> pd.DataFrame:
    """Collect chain TVL time series -> <output_dir>/chains.parquet."""
    base_url = str(cfg.get("base_url", _DEFAULT_BASE_URL)).rstrip("/")
    chains = cfg.get("chains", _DEFAULT_CHAINS)
    rows: List[Dict[str, Any]] = []
    for chain in chains:
        try:
            payload = _get_json(session, f"{base_url}/v2/historicalChainTvl/{chain}")
            r = parse_chain_tvl(payload, chain)
            rows.extend(r)
            _logger.info("defillama chain tvl %s: %d rows", chain, len(r))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("defillama chain tvl %s failed: %s", chain, exc)
        _time.sleep(_SLEEP)
    df = pd.DataFrame(rows, columns=["chain", "date", "tvl_usd", "source"])
    if not df.empty and write:
        total = _upsert(df, ["chain", "date"], Path(output_dir) / "chains.parquet")
        _logger.info("defillama chains: wrote %d new, %d total", len(df), total)
    return df


def collect_yields(output_dir, cfg, session, write=True) -> pd.DataFrame:
    """Collect pool APY/TVL snapshot -> <output_dir>/yields.parquet."""
    yields_url = str(cfg.get("yields_url", _DEFAULT_YIELDS_URL)).rstrip("/")
    top_n = int(cfg.get("yields_top_n", _DEFAULT_YIELDS_TOP_N))
    ts_utc = pd.Timestamp.utcnow().normalize()
    rows: List[Dict[str, Any]] = []
    try:
        payload = _get_json(session, f"{yields_url}/pools")
        rows = parse_pools(payload, top_n, ts_utc)
        _logger.info("defillama pools: %d rows (top %d)", len(rows), top_n)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("defillama pools failed: %s", exc)
    df = pd.DataFrame(
        rows, columns=["pool_id", "project", "symbol", "chain", "apy", "tvl_usd", "ts_utc", "source"]
    )
    if not df.empty and write:
        total = _upsert(df, ["pool_id", "ts_utc"], Path(output_dir) / "yields.parquet")
        _logger.info("defillama yields: wrote %d new, %d total", len(df), total)
    return df


def collect_stablecoins(output_dir, cfg, session, write=True) -> pd.DataFrame:
    """Collect stablecoin circulating snapshot -> <output_dir>/stablecoins.parquet."""
    stable_url = str(cfg.get("stablecoins_url", _DEFAULT_STABLE_URL)).rstrip("/")
    top_n = int(cfg.get("stablecoins_top_n", _DEFAULT_STABLE_TOP_N))
    ts_utc = pd.Timestamp.utcnow().normalize()
    rows: List[Dict[str, Any]] = []
    try:
        payload = _get_json(session, f"{stable_url}/stablecoins", {"includePrices": "false"})
        rows = parse_stablecoins(payload, top_n, ts_utc)
        _logger.info("defillama stablecoins: %d rows (top %d)", len(rows), top_n)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("defillama stablecoins failed: %s", exc)
    df = pd.DataFrame(
        rows, columns=["stablecoin", "symbol", "circulating_usd", "ts_utc", "source"]
    )
    if not df.empty and write:
        total = _upsert(df, ["stablecoin", "ts_utc"], Path(output_dir) / "stablecoins.parquet")
        _logger.info("defillama stablecoins: wrote %d new, %d total", len(df), total)
    return df


def collect_defillama(
    output_dir: "str | Path" = _DEFAULT_OUTPUT_DIR,
    config: Optional[Dict[str, Any]] = None,
    session: "Optional[_requests.Session]" = None,
    write: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Run all four DeFiLlama collectors; return {name: frame}."""
    cfg = config if config is not None else load_collector_config()
    output_dir = cfg.get("output_dir", _DEFAULT_OUTPUT_DIR) if config is None else output_dir
    if session is None:
        session = _requests.Session()
        session.headers["User-Agent"] = "chf-defillama-collector/1.0"
    return {
        "tvl": collect_tvl(output_dir, cfg, session, write),
        "chains": collect_chains(output_dir, cfg, session, write),
        "yields": collect_yields(output_dir, cfg, session, write),
        "stablecoins": collect_stablecoins(output_dir, cfg, session, write),
    }


def main() -> None:
    parser = _argparse.ArgumentParser(description="Collect DeFiLlama TVL/yields/stablecoins (keyless)")
    parser.add_argument("--output-dir", default=None, help="override output dir")
    parser.add_argument("--config", default=_CONFIG_PATH)
    args = parser.parse_args()
    cfg = load_collector_config(args.config)
    output_dir = args.output_dir or cfg.get("output_dir", _DEFAULT_OUTPUT_DIR)
    collect_defillama(output_dir=output_dir, config=cfg)


if __name__ == "__main__":
    main()
