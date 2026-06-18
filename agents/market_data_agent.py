from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from agents.base import AgentBase
from providers.ccxt_market import CCXTMarketProvider
from providers.coinmarketcap import CoinMarketCapProvider
from providers.http_client import CachedHttpClient, ProviderUnavailableError, RateLimitError
from providers.market_fallbacks import MarketFallbackProvider


CANONICAL_COLUMNS = [
    "date_ts",
    "symbol",
    "cmc_id",
    "exchange",
    "exchange_symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "market_cap",
    "source",
    "snapshot_id",
    "fetched_at_utc",
    "is_forward_filled",
    "is_incomplete_dropped",
    "data_type",
    "is_full_ohlcv",
    "quote_currency",
    "is_universe_member",
    "market_cap_rank",
    "dollar_volume_usd",
    "volume_basis",
    "is_synthetic_ohlc",
    "is_price_anomaly",
    "price_basis",
    "has_long_gap",
    "volume_scope",
    "is_stale_price",
]


def _largest_segment_after_long_gap(missing_arr, max_allowed_gap: int) -> int:
    """Return the index cutoff to keep all data AFTER the last gap longer than allowed.

    Given a boolean array where True = a missing day, find the end position of the LAST
    contiguous missing run longer than ``max_allowed_gap``; data from there onward has no
    long gaps. Used by ``long_gap_policy: segment_and_flag`` to keep the most-recent
    contiguous segment instead of discarding the whole asset.
    """
    n = len(missing_arr)
    cutoff = 0
    i = 0
    while i < n:
        if missing_arr[i]:
            j = i
            while j < n and missing_arr[j]:
                j += 1
            if (j - i) > max_allowed_gap:
                cutoff = j
            i = j
        else:
            i += 1
    return cutoff


# Per-source volume basis. Exchange (CCXT) candle volume is in BASE-asset units, so
# USD dollar-volume = volume * close. Aggregator/index sources already report volume
# in USD (quote), so dollar-volume = volume directly (multiplying by close would
# double-count price). Close-only sources carry no usable volume.
VOLUME_BASIS_BY_SOURCE = {
    "cryptocompare": "quote_usd",   # histoday volumeto = USD
    "coingecko": "quote_usd",       # total_volumes = USD
    "coinpaprika": "quote_usd",     # ohlcv volume = USD
    "coinmarketcap": "quote_usd",   # CMC ohlcv quote volume = USD
    "coincap": "none",              # close-only, no volume
}


def volume_basis_for_source(source_used: str) -> str:
    """Return the volume unit basis for a market source ('base', 'quote_usd', 'none')."""
    s = str(source_used or "").lower()
    if s.startswith("ccxt_"):
        return "base"
    return VOLUME_BASIS_BY_SOURCE.get(s, "base" if s else "none")


# Per-source PRICE basis (limitation E). Exchange (CCXT) candles are a specific venue's
# close; aggregator sources are a cross-venue composite index. These are different price
# *definitions*, so price_basis is tagged per row and a verifier warns when an asset's
# series mixes bases (venue-close spliced with index-close).
def price_basis_for_source(source_used: str) -> str:
    """Return the price definition basis ('venue_close', 'composite_index', 'unknown')."""
    s = str(source_used or "").lower()
    if not s:
        return "unknown"
    if s.startswith("ccxt_"):
        return "venue_close"
    return "composite_index"


# Per-source VOLUME SCOPE (limitation #7). Exchange (CCXT) volume is a single venue's
# traded volume; aggregator sources report a cross-venue GLOBAL volume. Same unit (after
# Phase 2's dollar_volume_usd) but a different *quantity*, so scope is tagged for downstream.
def volume_scope_for_source(source_used: str) -> str:
    """Return the volume scope ('single_venue', 'global', 'unknown')."""
    s = str(source_used or "").lower()
    if not s:
        return "unknown"
    if s.startswith("ccxt_"):
        return "single_venue"
    return "global"


@dataclass
class AssetRequest:
    symbol: str
    coin_id: str
    exchange: str
    exchange_symbol: str
    cmc_id: Optional[int] = None


class MarketDataAgentError(RuntimeError):
    pass


class MarketDataAgent(AgentBase):
    """Research-mode market ingestion with exchange-first, cache-first provider fallback."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        symbols: Optional[List[str]] = None,
    ):
        super().__init__(config)
        self.symbols = symbols or []
        self.mcfg = self.cfg.get("market_data", {})
        self.fixture_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "market"
        self.output_dir = self.get_path("raw") / "market"
        self.cache_dir = self._resolve_cache_dir()
        self.http = CachedHttpClient(
            cache_dir=self.cache_dir,
            request_timeout_seconds=float(self.mcfg.get("request_timeout_seconds", 30)),
            min_seconds_between_requests=float(self.mcfg.get("min_seconds_between_requests", 1.5)),
            max_retries=int(self.mcfg.get("max_retries", 5)),
            backoff_base_seconds=float(self.mcfg.get("backoff_base_seconds", 3)),
            backoff_jitter_seconds=float(self.mcfg.get("backoff_jitter_seconds", 1.5)),
        )
        self.fallback_provider = MarketFallbackProvider(self.http, fixture_dir=self.fixture_dir)
        self.asset_requests: List[AssetRequest] = []
        self.universe_snapshot_date: Optional[pd.Timestamp] = None
        self.coverage_rows: List[Dict[str, Any]] = []
        self.exchanges_used: set[str] = set()
        self.fallbacks_used: set[str] = set()
        self.failed_assets: List[str] = []
        self.api_call_count_by_provider: Dict[str, int] = {}
        self.cache_hit_count_by_provider: Dict[str, int] = {}
        self.provider_instances: Dict[str, CCXTMarketProvider] = {}
        # Permanent (geo-block / DNS) — won't recover within a run.
        self.temporarily_unavailable_providers: Dict[str, str] = {}
        # Phase 4: bounded per-provider cooldown for transient rate limits.
        # provider_key -> monotonic deadline; retried once the deadline passes.
        self.provider_cooldown_until: Dict[str, float] = {}
        self.cmc_provider: Optional[CoinMarketCapProvider] = None

    def _resolve_cache_dir(self) -> Path:
        raw = self.mcfg.get("cache_dir", "data/cache/market")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def _now_utc(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def _as_of_date(self) -> pd.Timestamp:
        """Phase 4: pinnable 'as-of' date for deterministic runs.

        Defaults to today (UTC midnight) but can be pinned via ``market_data.as_of_date``
        so the requested window and the 'incomplete current day' cutoff are reproducible
        across calendar days. Wall-clock is used only for non-semantic metadata
        (``fetched_at_utc``).
        """
        raw = self.mcfg.get("as_of_date")
        if raw:
            return pd.to_datetime(raw, utc=True).normalize()
        return self._now_utc().normalize()

    def _content_hash(self, market_df: pd.DataFrame) -> str:
        """Deterministic 16-hex content hash of the full price panel.

        Covers (symbol, date_ts, open, high, low, close, volume) — not close alone — so a
        change in any OHLC field or volume changes the fingerprint. Mirrors UniverseAgent's
        content hashing; identical across runs over the same data regardless of wall-clock.
        """
        if market_df is None or market_df.empty:
            return ""
        wanted = ["symbol", "date_ts", "open", "high", "low", "close", "volume"]
        cols = [c for c in wanted if c in market_df.columns]
        if "symbol" not in cols or "date_ts" not in cols:
            return ""
        sub = market_df[cols].copy()
        sub["date_ts"] = pd.to_datetime(sub["date_ts"], utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in sub.columns:
                sub[c] = pd.to_numeric(sub[c], errors="coerce")
        sub = sub.sort_values(["symbol", "date_ts"])
        payload = sub.to_json(orient="records", date_format="iso")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _write_partitioned_market(self, market_df: pd.DataFrame, out_dir: Path) -> Optional[Path]:
        """Write a Hive-partitioned (symbol=…/year=…) Parquet copy via DuckDB COPY.

        Additive to the flat ``market_ohlcv.parquet`` that downstream + verifiers read.
        Yearly partition depth only (avoids the small-file problem). Deterministic rewrite:
        the partition tree is cleared first. Defensive: any failure logs a warning and
        returns None rather than failing the whole persist.
        """
        if market_df is None or market_df.empty or "date_ts" not in market_df.columns:
            return None
        partitioned_dir = out_dir / "partitioned"
        try:
            import shutil

            import duckdb

            if partitioned_dir.exists():
                shutil.rmtree(partitioned_dir)
            partitioned_dir.mkdir(parents=True, exist_ok=True)
            frame = market_df.copy()
            frame["date_ts"] = pd.to_datetime(frame["date_ts"], utc=True)
            frame["year"] = frame["date_ts"].dt.year.astype(int)
            con = duckdb.connect(database=":memory:")
            try:
                con.register("market_df", frame)
                con.execute(
                    "COPY (SELECT * FROM market_df) "
                    f"TO '{partitioned_dir.as_posix()}' "
                    "(FORMAT PARQUET, PARTITION_BY (symbol, year), OVERWRITE_OR_IGNORE TRUE)"
                )
            finally:
                con.close()
            return partitioned_dir
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(f"partitioned market write failed (non-fatal): {exc}")
            return None

    def _flag_stale_prices(self, df: pd.DataFrame) -> pd.DataFrame:
        """Flag genuinely stale (frozen-feed) prices: a run of an identical REAL close longer
        than ``max_flat_close_days``. Forward-filled synthetic flats are excluded (already
        flagged via is_synthetic_ohlc/is_forward_filled). Non-destructive diagnostic.
        """
        df = df.copy()
        if "is_stale_price" not in df.columns:
            df["is_stale_price"] = False
        n = int(self.mcfg.get("max_flat_close_days", 10))
        if df.empty or "close" not in df.columns or n <= 0:
            return df
        close = pd.to_numeric(df["close"], errors="coerce")
        run_id = (close != close.shift(1)).cumsum()
        run_len = close.groupby(run_id).transform("size")
        stale = (run_len > n) & close.notna()
        if "is_forward_filled" in df.columns:
            stale = stale & ~df["is_forward_filled"].fillna(False).astype(bool)
        df["is_stale_price"] = stale.values
        n_stale = int(stale.sum())
        if n_stale:
            self.metrics["stale_price_rows_total"] = float(self.metrics.get("stale_price_rows_total", 0.0) + n_stale)
        return df

    def _flag_price_anomalies(self, df: pd.DataFrame) -> pd.DataFrame:
        """Phase 5: flag (and optionally treat) spike-and-revert price anomalies.

        A bar is an anomaly only when BOTH the move into it and the move out of it exceed
        ``max_abs_daily_log_return`` AND they have opposite sign — i.e. a round-trip spike
        that reverses the next day. This is the signature of a bad print / thin-venue glitch.
        Crucially, a *legitimate* large move that is sustained (does not revert) is NOT
        flagged, so real crypto rallies/crashes survive. Forward-filled synthetic bars are
        never flagged (already tagged, volume 0).

        ``anomaly_policy``: ``flag_only`` (default — non-destructive, downstream may mask),
        ``drop`` (remove the bar), or ``winsorize`` (neutralize to the prior close).
        """
        df = df.copy()
        df["is_price_anomaly"] = False
        if df.empty or "close" not in df.columns or len(df) < 3:
            return df
        threshold = float(self.mcfg.get("max_abs_daily_log_return", 1.6094379124341003))  # ln(5) ≈ +400%/day
        # Secondary, lower threshold for the round-trip-to-origin test (catches MODERATE bad
        # prints): a spike of this magnitude that returns to within tolerance of the pre-spike
        # price is a glitch regardless of whether each leg individually exceeds ln(5).
        secondary = float(self.mcfg.get("anomaly_secondary_log_return", 0.9162907318741551))  # ln(2.5)
        tol = float(self.mcfg.get("anomaly_roundtrip_tolerance", 0.15))  # 15% return-to-origin
        close = pd.to_numeric(df["close"], errors="coerce")
        prev = close.shift(1)
        nxt = close.shift(-1)
        with np.errstate(divide="ignore", invalid="ignore"):
            r_in = np.log(close / prev)
            r_out = np.log(nxt / close)
        opposite = (np.sign(r_in) * np.sign(r_out)) < 0
        # (a) both legs huge + opposite sign (original egregious-glitch rule).
        primary = (r_in.abs() > threshold) & (r_out.abs() > threshold) & opposite
        # (b) round-trip to origin: a big spike whose NEXT close returns near the PRIOR close.
        with np.errstate(divide="ignore", invalid="ignore"):
            return_to_origin = (nxt - prev).abs() / prev.abs()
        roundtrip = (r_in.abs() > secondary) & opposite & (return_to_origin <= tol)
        anomaly = (primary | roundtrip).fillna(False)
        # A sustained move (no revert) is never flagged: opposite=False or return_to_origin large.
        if "is_synthetic_ohlc" in df.columns:
            anomaly = anomaly & ~df["is_synthetic_ohlc"].fillna(False).astype(bool)
        df["is_price_anomaly"] = anomaly.values
        n_anom = int(anomaly.sum())
        if n_anom:
            self.metrics["price_anomalies_total"] = float(self.metrics.get("price_anomalies_total", 0.0) + n_anom)
        policy = str(self.mcfg.get("anomaly_policy", "flag_only")).lower()
        if n_anom and policy == "drop":
            df = df[~df["is_price_anomaly"]].copy()
        elif n_anom and policy == "winsorize":
            # Neutralize the spike to a flat bar at the prior close — and mark it SYNTHETIC so
            # range/ATR features exclude it (no unflagged fabricated bar).
            mask = df["is_price_anomaly"].values
            prior_vals = prev.values
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df.loc[mask, col] = prior_vals[mask]
            if "is_synthetic_ohlc" not in df.columns:
                df["is_synthetic_ohlc"] = False
            df.loc[mask, "is_synthetic_ohlc"] = True
        return df

    def prepare(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.mcfg.get("use_cmc_ohlcv", False):
            self.cmc_provider = CoinMarketCapProvider(
                cache_dir=self.cache_dir,
                request_timeout_seconds=float(self.mcfg.get("request_timeout_seconds", 30)),
                min_seconds_between_requests=float(self.mcfg.get("min_seconds_between_requests", 1.5)),
                max_retries=int(self.mcfg.get("max_retries", 5)),
                backoff_base_seconds=float(self.mcfg.get("backoff_base_seconds", 3)),
                backoff_jitter_seconds=float(self.mcfg.get("backoff_jitter_seconds", 1.5)),
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
            )
        if self.mcfg.get("fail_on_binance_usage", True):
            quote_cfg = str(self.mcfg.get("quote_currency", "")).upper()
            exchange_cfg = " ".join(str(item).lower() for item in self.mcfg.get("exchange_priority", []))
            if "binance" in exchange_cfg:
                raise MarketDataAgentError("Binance is forbidden in research mode")
            if quote_cfg == "USDT":
                raise MarketDataAgentError("USDT quote configuration is forbidden in research mode")
        if (
            str(self.mcfg.get("universe_membership_mode", "latest_snapshot")).lower() == "union_full_history"
            and self.mcfg.get("require_pit_membership", False)
            and not self._membership_daily_path().exists()
        ):
            raise MarketDataAgentError(
                f"require_pit_membership=true but daily membership mask is missing: {self._membership_daily_path()}. "
                "Run scripts/build_membership_daily.py first."
            )
        self._load_universe_requests()
        if not self.asset_requests:
            raise MarketDataAgentError("No eligible market data asset requests were loaded from universe")

    def _load_universe_requests(self) -> None:
        universe_path = self.get_path("raw") / "universe" / "universe_monthly.parquet"
        if not universe_path.exists():
            raise FileNotFoundError(
                "Missing universe_monthly.parquet. Run UniverseAgent and verify_universe_run.py first."
            )
        df = pd.read_parquet(universe_path)
        required = {"snapshot_date", "is_eligible", "symbol", "coin_id", "exchange", "exchange_symbol"}
        if not required.issubset(df.columns):
            raise ValueError(f"Universe output missing required columns: {sorted(required - set(df.columns))}")
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], utc=True)
        if self.mcfg.get("use_cmc_ohlcv", False):
            if "cmc_id" not in df.columns:
                raise ValueError("Universe output missing required column cmc_id for CMC market mode")
            eligible = df[df["is_eligible"]].copy()
            if eligible.empty:
                raise MarketDataAgentError("Historical CMC universe has no eligible assets")
            eligible = eligible.sort_values(["market_cap_rank", "symbol"], na_position="last")
            max_assets = self.mcfg.get("max_assets")
            grouped = (
                eligible.sort_values(["snapshot_date", "market_cap_rank", "symbol"])
                .groupby("cmc_id", as_index=False)
                .first()
                .sort_values(["market_cap_rank", "symbol"], na_position="last")
            )
            if max_assets:
                grouped = grouped.head(int(max_assets)).copy()
            for _, row in grouped.iterrows():
                self.asset_requests.append(
                    AssetRequest(
                        symbol=str(row["symbol"]).upper(),
                        coin_id=str(row.get("coin_id") or row.get("slug") or row["symbol"]).lower(),
                        exchange=str(row.get("exchange") or "").lower().strip(),
                        exchange_symbol=str(row.get("exchange_symbol") or "").strip(),
                        cmc_id=int(row["cmc_id"]) if pd.notna(row["cmc_id"]) else None,
                    )
                )
            self.universe_snapshot_date = eligible["snapshot_date"].max()
            return
        membership_mode = str(self.mcfg.get("universe_membership_mode", "latest_snapshot")).lower()
        if membership_mode == "union_full_history":
            self._load_union_requests(df)
            return
        latest_snapshot = df["snapshot_date"].max()
        latest_df = df[(df["snapshot_date"] == latest_snapshot) & (df["is_eligible"])].copy()
        if latest_df.empty:
            raise MarketDataAgentError("Latest universe snapshot has no eligible assets")
        latest_df = latest_df.sort_values(["market_cap_rank", "symbol"], na_position="last")
        max_assets = self.mcfg.get("max_assets")
        if max_assets:
            latest_df = latest_df.head(int(max_assets)).copy()
        if self.symbols:
            allowed = {s.upper() for s in self.symbols}
            latest_df = latest_df[latest_df["symbol"].astype(str).str.upper().isin(allowed)].copy()
        for _, row in latest_df.iterrows():
            exchange = str(row["exchange"]).lower().strip()
            exchange_symbol = str(row["exchange_symbol"]).strip()
            if self.mcfg.get("fail_on_binance_usage", True):
                if "binance" in exchange or "USDT" in exchange_symbol.upper():
                    raise MarketDataAgentError(
                        f"Universe contains forbidden market route for {row['symbol']}: {exchange} {exchange_symbol}"
                    )
            self.asset_requests.append(
                AssetRequest(
                    symbol=str(row["symbol"]).upper(),
                    coin_id=str(row["coin_id"]),
                    exchange=exchange,
                    exchange_symbol=exchange_symbol,
                    cmc_id=int(row["cmc_id"]) if "cmc_id" in row and pd.notna(row["cmc_id"]) else None,
                )
            )
        self.universe_snapshot_date = latest_snapshot

    def _load_union_requests(self, df: pd.DataFrame) -> None:
        """Build one request per stable cmc_id across ALL monthly snapshots (PIT union).

        This preserves the survivorship-free universe: every coin that was ever an
        eligible member — including since-delisted names (FTT, LUNA, CEL, …) — gets a
        fetch request, with routing/identity taken from its most recent eligible
        snapshot. Per-day membership is re-applied later via the daily mask
        (``_attach_membership_mask``) and by FeatureAgent, so the model only trades a
        coin on the days it was actually in the top-N.
        """
        eligible = df[df["is_eligible"]].copy()
        if eligible.empty:
            raise MarketDataAgentError("Union universe has no eligible assets")
        latest_per_coin = (
            eligible.sort_values(["snapshot_date", "market_cap_rank", "symbol"], na_position="last")
            .groupby("cmc_id", as_index=False)
            .last()
            .sort_values(["market_cap_rank", "symbol"], na_position="last")
        )
        max_assets = self.mcfg.get("max_assets")
        if max_assets:
            latest_per_coin = latest_per_coin.head(int(max_assets)).copy()
        if self.symbols:
            allowed = {s.upper() for s in self.symbols}
            latest_per_coin = latest_per_coin[latest_per_coin["symbol"].astype(str).str.upper().isin(allowed)].copy()
        for _, row in latest_per_coin.iterrows():
            exchange = str(row.get("exchange") or "").lower().strip()
            exchange_symbol = str(row.get("exchange_symbol") or "").strip()
            if self.mcfg.get("fail_on_binance_usage", True):
                if "binance" in exchange or "USDT" in exchange_symbol.upper():
                    raise MarketDataAgentError(
                        f"Universe contains forbidden market route for {row['symbol']}: {exchange} {exchange_symbol}"
                    )
            self.asset_requests.append(
                AssetRequest(
                    symbol=str(row["symbol"]).upper(),
                    coin_id=str(row.get("coin_id") or row.get("slug") or row["symbol"]).lower(),
                    exchange=exchange,
                    exchange_symbol=exchange_symbol,
                    cmc_id=int(row["cmc_id"]) if pd.notna(row.get("cmc_id")) else None,
                )
            )
        self.universe_snapshot_date = eligible["snapshot_date"].max()
        self.metrics["universe_union_assets"] = float(len(self.asset_requests))

    def _membership_daily_path(self) -> Path:
        raw = self.mcfg.get("membership_daily_path", "data/raw/universe/universe_membership_daily.parquet")
        path = Path(raw)
        if not path.is_absolute():
            path = Path(self.cfg["_project_root"]) / path
        return path

    def _attach_membership_mask(self, market_df: pd.DataFrame) -> pd.DataFrame:
        """Left-join the daily PIT membership mask onto the market panel.

        Adds ``is_universe_member`` (True only on days the coin was an eligible
        top-N member), and fills ``market_cap_rank`` / ``market_cap`` from the
        membership record. Non-member rows are RETAINED (downstream feature warmup
        needs pre-membership history) but flagged ``is_universe_member=False``.
        Gated by ``attach_membership_mask`` (default off → columns are NA, behavior
        unchanged).
        """
        if "is_universe_member" not in market_df.columns:
            market_df["is_universe_member"] = pd.NA
        if "market_cap_rank" not in market_df.columns:
            market_df["market_cap_rank"] = pd.NA
        if not bool(self.mcfg.get("attach_membership_mask", False)):
            return market_df
        if market_df.empty:
            return market_df
        mpath = self._membership_daily_path()
        if not mpath.exists():
            if self.mcfg.get("require_pit_membership", False):
                raise MarketDataAgentError(f"attach_membership_mask=true but mask missing: {mpath}")
            self.logger.warning(f"membership mask not found, skipping attach: {mpath}")
            return market_df
        mask = pd.read_parquet(mpath, columns=["date_ts", "cmc_id", "symbol", "market_cap_rank", "market_cap_usd"])
        mask["date_ts"] = pd.to_datetime(mask["date_ts"], utc=True).dt.normalize()
        market_df["date_ts"] = pd.to_datetime(market_df["date_ts"], utc=True).dt.normalize()
        # Phase 9 (#4, #12): vectorized, collision-safe join. The cmc_id join is the stable
        # primary; the symbol fallback is used ONLY for symbols that map to a single cmc_id in
        # the mask (ambiguous reused tickers are excluded — no cross-coin mis-attribution).
        n0 = len(market_df)
        market_df = market_df.reset_index(drop=True)
        market_df["_row"] = np.arange(n0)
        market_df["_sym_u"] = market_df["symbol"].astype(str).str.upper()
        cid_num = pd.to_numeric(market_df.get("cmc_id", pd.Series([pd.NA] * n0)), errors="coerce")
        market_df["_cid_num"] = cid_num

        # Primary: by (date_ts, cmc_id).
        mask_id = mask.dropna(subset=["cmc_id"]).copy()
        mask_id["_cid"] = pd.to_numeric(mask_id["cmc_id"], errors="coerce")
        mask_id = mask_id.dropna(subset=["_cid"]).drop_duplicates(["date_ts", "_cid"])
        mask_id = mask_id[["date_ts", "_cid", "market_cap_rank", "market_cap_usd"]].rename(
            columns={"market_cap_rank": "_rank_id", "market_cap_usd": "_mcap_id"}
        )
        mask_id["_m_id"] = True
        merged = market_df.merge(mask_id, left_on=["date_ts", "_cid_num"], right_on=["date_ts", "_cid"], how="left")

        # Fallback: by (date_ts, symbol), unambiguous tickers only.
        msym = mask.copy()
        msym["symbol"] = msym["symbol"].astype(str).str.upper()
        sym_card = msym.groupby("symbol")["cmc_id"].nunique(dropna=False)
        unambiguous = set(sym_card[sym_card <= 1].index)
        mask_sym = msym[msym["symbol"].isin(unambiguous)].drop_duplicates(["date_ts", "symbol"])
        mask_sym = mask_sym[["date_ts", "symbol", "market_cap_rank", "market_cap_usd"]].rename(
            columns={"symbol": "_sym", "market_cap_rank": "_rank_sym", "market_cap_usd": "_mcap_sym"}
        )
        mask_sym["_m_sym"] = True
        merged = merged.merge(mask_sym, left_on=["date_ts", "_sym_u"], right_on=["date_ts", "_sym"], how="left")

        if len(merged) != n0:  # defensive: keys must be unique → no row multiplication
            merged = merged.drop_duplicates("_row").sort_values("_row")
        merged = merged.sort_values("_row").reset_index(drop=True)

        m_id = (merged["_m_id"] == True).to_numpy(dtype=bool)   # noqa: E712
        m_sym = (merged["_m_sym"] == True).to_numpy(dtype=bool) & ~m_id   # noqa: E712
        member = m_id | m_sym
        cur_rank = market_df.get("market_cap_rank", pd.Series([pd.NA] * n0)).to_numpy()
        rank = np.where(m_id, merged["_rank_id"].to_numpy(), np.where(m_sym, merged["_rank_sym"].to_numpy(), cur_rank))
        cur_mcap = pd.to_numeric(market_df.get("market_cap", pd.Series([pd.NA] * n0)), errors="coerce").to_numpy()
        mcap_mask = np.where(m_id, merged["_mcap_id"].to_numpy(), np.where(m_sym, merged["_mcap_sym"].to_numpy(), np.nan))
        mcap = np.where(~np.isnan(cur_mcap), cur_mcap, mcap_mask)

        market_df["is_universe_member"] = member
        market_df["market_cap_rank"] = rank
        market_df["market_cap"] = mcap
        market_df = market_df.drop(columns=["_row", "_sym_u", "_cid_num"], errors="ignore")
        members = member
        member_rows = int(pd.Series(members).sum())
        self.metrics["membership_member_rows"] = float(member_rows)
        self.metrics["membership_total_rows"] = float(len(market_df))
        self._progress(
            f"[market] Membership mask attached: {member_rows}/{len(market_df)} rows are universe members"
        )
        return market_df

    def _load_symbols_from_universe(self) -> None:
        self._load_universe_requests()
        self.symbols = [request.symbol for request in self.asset_requests]

    def run(self) -> Dict[str, Any]:
        as_of = self._as_of_date()
        requested_end = pd.to_datetime(self.mcfg["end_date"], utc=True).normalize() if self.mcfg.get("end_date") else as_of
        lookback_days = int(self.mcfg.get("lookback_days", self.mcfg.get("backfill_days", 2000)))
        requested_start = pd.to_datetime(self.mcfg["start_date"], utc=True).normalize() if self.mcfg.get("start_date") else requested_end - pd.Timedelta(days=lookback_days)
        snapshot_ref = self.universe_snapshot_date.date().isoformat() if self.universe_snapshot_date is not None else "unknown"
        # Phase 4: snapshot_id incorporates the data window so identical config + window
        # is reproducible and a different window yields a distinct id.
        self.generate_snapshot_id(
            f"market:{snapshot_ref}:{requested_start.date().isoformat()}:{requested_end.date().isoformat()}"
        )

        all_frames: List[pd.DataFrame] = []
        total_assets = len(self.asset_requests)
        for idx, request in enumerate(self.asset_requests, start=1):
            self._progress(f"[market] Fetching {request.symbol} {idx}/{total_assets}")
            df, coverage = self._fetch_asset(request, requested_start, requested_end)
            self.coverage_rows.append(coverage)
            if df.empty:
                self.failed_assets.append(request.symbol)
                continue
            all_frames.append(df)

        requested_assets = len(self.asset_requests)
        fetched_assets = len(all_frames)
        min_history_days = int(self.mcfg.get("min_history_days", 365))
        full_ohlcv_assets = sum(
            1
            for row in self.coverage_rows
            if row.get("passed_qa") and row.get("is_full_ohlcv") and int(row.get("row_count", 0)) >= min_history_days
        )
        self.metrics["requested_assets"] = requested_assets
        self.metrics["fetched_assets"] = fetched_assets
        self.metrics["failed_assets"] = len(self.failed_assets)
        self.metrics["full_ohlcv_assets"] = full_ohlcv_assets

        market_df = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame(columns=CANONICAL_COLUMNS)
        # The survivorship-free union can legitimately carry two cmc_ids for one ticker
        # (redenominations / ticker reuse: BTT 3718→16086, LUNA classic→2.0, DYDX, KNC).
        # Both resolve to the same exchange series, so collapse to one (symbol, date_ts)
        # row to preserve the downstream unique-key contract and avoid double-counting.
        if not market_df.empty and {"symbol", "date_ts"}.issubset(market_df.columns):
            market_df = market_df.drop_duplicates(subset=["symbol", "date_ts"], keep="first").reset_index(drop=True)
        market_df = self._attach_membership_mask(market_df)
        coverage_df = pd.DataFrame(self.coverage_rows)
        fatal_errors = self._fatal_errors(
            requested_assets=requested_assets,
            fetched_assets=fetched_assets,
            full_ohlcv_assets=full_ohlcv_assets,
            coverage_df=coverage_df,
            market_df=market_df,
        )
        return {
            "market_ohlcv": market_df,
            "coverage_report": coverage_df,
            "requested_assets": requested_assets,
            "fetched_assets": fetched_assets,
            "fatal_errors": fatal_errors,
        }

    def _progress(self, message: str) -> None:
        print(message, flush=True)
        self.logger.info(message)

    def _provider_for_exchange(self, exchange_name: str) -> CCXTMarketProvider:
        if exchange_name not in self.provider_instances:
            self.provider_instances[exchange_name] = CCXTMarketProvider(
                exchange_name=exchange_name,
                cache_dir=self.cache_dir,
                timeframe=self.mcfg.get("timeframe", "1d"),
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)),
                use_fixtures=bool(self.mcfg.get("use_fixtures", False)),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
                request_timeout_seconds=float(self.mcfg.get("request_timeout_seconds", 30)),
                min_seconds_between_requests=float(self.mcfg.get("min_seconds_between_requests", 1.5)),
                max_retries=int(self.mcfg.get("max_retries", 5)),
                backoff_base_seconds=float(self.mcfg.get("backoff_base_seconds", 3)),
                backoff_jitter_seconds=float(self.mcfg.get("backoff_jitter_seconds", 1.5)),
                fixture_dir=self.fixture_dir,
            )
        return self.provider_instances[exchange_name]

    def _merge_provider_stats(self, provider: CCXTMarketProvider) -> None:
        for key, value in provider.api_call_count_by_provider.items():
            self.api_call_count_by_provider[key] = int(value)
        for key, value in provider.cache_hit_count_by_provider.items():
            self.cache_hit_count_by_provider[key] = int(value)

    def _ordered_exchange_candidates(self, request: AssetRequest) -> List[str]:
        configured = [str(item).lower() for item in self.mcfg.get("exchange_priority", [])]
        hint = str(request.exchange or "").lower().strip()
        # The universe's `exchange` field may carry a non-routable PIT placeholder
        # (e.g. "cmc_market_pairs" from the tradability gate). Only honor it as a fetch
        # route when it is a real, configured exchange — otherwise every asset wastes a
        # guaranteed-failing ccxt attempt on a non-existent exchange.
        ordered = ([hint] if hint in configured else []) + configured
        result: List[str] = []
        seen = set()
        for name in ordered:
            if not name or name in seen or name == "binance":
                continue
            seen.add(name)
            result.append(name)
        return result

    def _fetch_asset(
        self,
        request: AssetRequest,
        requested_start: pd.Timestamp,
        requested_end: pd.Timestamp,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        if self.mcfg.get("use_cmc_ohlcv", False):
            return self._fetch_asset_cmc(request, requested_start, requested_end)
        source_used = ""
        data_type = ""
        is_full_ohlcv = False
        fallback_used = False
        chosen_exchange = request.exchange
        request_exchange_symbol = request.exchange_symbol
        raw_df = pd.DataFrame()
        provider_attempts: List[str] = []
        provider_failure_reasons: Dict[str, str] = {}
        asset_started = time.monotonic()
        # A provider result shorter than the QA history floor (e.g. Coinbase's truncated
        # OHLCV for AVAX) must NOT short-circuit the waterfall — keep trying richer
        # providers/aggregators, and only settle for the longest short series if nothing
        # clears the floor. This keeps source selection deterministic (fixed priority).
        accept_min_rows = int(self.mcfg.get("min_history_days", 365))
        if str(self.mcfg.get("min_history_days_policy", "absolute")).lower() == "membership_aware":
            accept_min_rows = int(self.mcfg.get("min_history_days_floor", 90))
        best_short: Optional[Dict[str, Any]] = None

        for exchange_name in self._ordered_exchange_candidates(request):
            if time.monotonic() - asset_started > float(self.mcfg.get("per_asset_timeout_seconds", 180)):
                provider_failure_reasons["asset"] = "asset_timeout"
                break
            provider = self._provider_for_exchange(exchange_name)
            provider_key = provider.provider_key
            if provider_key in self.temporarily_unavailable_providers:
                provider_failure_reasons[provider_key] = self.temporarily_unavailable_providers[provider_key]
                continue
            cooldown_until = self.provider_cooldown_until.get(provider_key, 0.0)
            if time.monotonic() < cooldown_until:
                provider_failure_reasons[provider_key] = "rate_limit_cooldown_active"
                continue
            provider_attempts.append(provider_key)
            try:
                market_symbol = self._market_symbol_for_provider(provider, request, exchange_name)
                if not market_symbol:
                    raise MarketDataAgentError(f"market_symbol_unresolved:{exchange_name}")
                if self.mcfg.get("log_each_provider_attempt", True):
                    self._progress(f"[market] Attempt {provider_key} for {request.symbol} using {market_symbol}")
                raw_df = provider.fetch_ohlcv(
                    market_symbol,
                    requested_start.to_pydatetime(),
                    requested_end.to_pydatetime(),
                    1000,
                    int(self.mcfg.get("max_pages_per_asset", 20)),
                    int(self.mcfg.get("max_rows_per_asset", 3000)),
                )
                if raw_df.empty:
                    provider_failure_reasons[provider_key] = "empty_response"
                    if self.mcfg.get("log_each_provider_attempt", True):
                        self._progress(f"[market] Result {provider_key} {request.symbol}: rows=0 status=fail reason=empty_response")
                    continue
                if len(raw_df) < accept_min_rows:
                    # Data returned but too short to clear the QA history floor. Stash the
                    # longest such candidate and keep trying richer providers/aggregators.
                    provider_failure_reasons[provider_key] = f"short_history:{len(raw_df)}<{accept_min_rows}"
                    if best_short is None or len(raw_df) > int(best_short["rows"]):
                        best_short = {
                            "rows": len(raw_df),
                            "df": raw_df.copy(),
                            "provider_key": provider_key,
                            "exchange_name": exchange_name,
                            "market_symbol": market_symbol,
                        }
                    if self.mcfg.get("log_each_provider_attempt", True):
                        self._progress(f"[market] Result {provider_key} {request.symbol}: rows={len(raw_df)} status=short reason=short_history:{len(raw_df)}<{accept_min_rows}")
                    raw_df = pd.DataFrame()
                    continue
                source_used = provider_key
                data_type = "exchange_ohlcv"
                is_full_ohlcv = True
                chosen_exchange = exchange_name
                self.exchanges_used.add(exchange_name)
                request_exchange_symbol = market_symbol
                if self.mcfg.get("log_each_provider_attempt", True):
                    self._progress(f"[market] Result {provider_key} {request.symbol}: rows={len(raw_df)} status=success reason=")
                break
            except RateLimitError as exc:
                # Phase 4: transient — cool the provider down for a bounded window instead of
                # abandoning it for the whole run (keeps source selection run-order-independent).
                reason = str(exc)
                provider_failure_reasons[provider_key] = reason
                self.provider_cooldown_until[provider_key] = time.monotonic() + float(
                    self.mcfg.get("provider_cooldown_seconds", 60)
                )
                if self.mcfg.get("log_each_provider_attempt", True):
                    self._progress(f"[market] Result {provider_key} {request.symbol}: rows=0 status=fail reason={reason}")
            except ProviderUnavailableError as exc:
                # Geo-block / DNS — will not recover within the run; skip permanently.
                reason = str(exc)
                provider_failure_reasons[provider_key] = reason
                self.temporarily_unavailable_providers[provider_key] = reason
                if self.mcfg.get("log_each_provider_attempt", True):
                    self._progress(f"[market] Result {provider_key} {request.symbol}: rows=0 status=fail reason={reason}")
            except Exception as exc:
                provider_failure_reasons[provider_key] = str(exc)
                if self.mcfg.get("log_each_provider_attempt", True):
                    self._progress(f"[market] Result {provider_key} {request.symbol}: rows=0 status=fail reason={exc}")
            finally:
                self._merge_provider_stats(provider)
        if raw_df.empty:
            fallback_result = self.fallback_provider.fetch_daily_data(
                symbol=request.symbol,
                coin_id=request.coin_id,
                provider_priority=self.mcfg.get(
                    "fallback_provider_priority",
                    ["cryptocompare", "coingecko", "coincap", "coinpaprika"],
                ),
                requested_start_dt=requested_start.to_pydatetime(),
                requested_end_dt=(requested_end - pd.Timedelta(days=1)).to_pydatetime(),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)),
                use_fixtures=bool(self.mcfg.get("use_fixtures", False)),
            )
            for attempt in fallback_result.attempts:
                if attempt not in provider_attempts:
                    provider_attempts.append(attempt)
            provider_failure_reasons.update(fallback_result.failure_reasons)
            raw_df = fallback_result.df
            if fallback_result.provider_name:
                source_used = fallback_result.provider_name
                data_type = fallback_result.data_type
                is_full_ohlcv = fallback_result.is_full_ohlcv
                fallback_used = True
                self.fallbacks_used.add(fallback_result.provider_name)

        if raw_df.empty and best_short is not None:
            # No exchange or aggregator cleared the history floor; record the longest short
            # series we saw rather than dropping the asset, so QA/coverage report it honestly.
            raw_df = best_short["df"]
            source_used = best_short["provider_key"]
            data_type = "exchange_ohlcv"
            is_full_ohlcv = True
            chosen_exchange = best_short["exchange_name"]
            self.exchanges_used.add(best_short["exchange_name"])
            request_exchange_symbol = best_short["market_symbol"]

        if raw_df.empty:
            failure_reason = self._summarize_failure(provider_failure_reasons)
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._coverage_row(
                request=request,
                requested_start=requested_start,
                requested_end=requested_end,
                source_used=source_used,
                row_count=0,
                start_date="",
                end_date="",
                missing_days=0,
                forward_filled_days=0,
                incomplete_rows_dropped=0,
                failure_reason=failure_reason,
                passed_qa=False,
                fetched=False,
                provider_attempts=provider_attempts,
                provider_failure_reasons=provider_failure_reasons,
                data_type=data_type or "none",
                is_full_ohlcv=is_full_ohlcv,
                fallback_used=fallback_used,
            )

        normalized, qa = self._normalize_asset_frame(
            raw_df=raw_df,
            request=request,
            source_used=source_used,
            requested_start=requested_start,
            requested_end=requested_end,
            data_type=data_type,
            is_full_ohlcv=is_full_ohlcv,
            exchange_name=chosen_exchange,
            exchange_symbol=request_exchange_symbol,
        )
        self._progress(
            f"[market] QA {request.symbol}: normalized_rows={len(normalized)} passed={str(bool(qa['passed_qa'])).lower()} reason={qa['failure_reason']}"
        )
        if normalized.empty and is_full_ohlcv and not fallback_used:
            provider_failure_reasons[source_used or "exchange"] = qa["failure_reason"]
            fallback_result = self.fallback_provider.fetch_daily_data(
                symbol=request.symbol,
                coin_id=request.coin_id,
                provider_priority=self.mcfg.get(
                    "fallback_provider_priority",
                    ["cryptocompare", "coingecko", "coincap", "coinpaprika"],
                ),
                requested_start_dt=requested_start.to_pydatetime(),
                requested_end_dt=(requested_end - pd.Timedelta(days=1)).to_pydatetime(),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)),
                use_fixtures=bool(self.mcfg.get("use_fixtures", False)),
            )
            for attempt in fallback_result.attempts:
                if attempt not in provider_attempts:
                    provider_attempts.append(attempt)
            provider_failure_reasons.update(fallback_result.failure_reasons)
            if not fallback_result.df.empty:
                source_used = fallback_result.provider_name
                data_type = fallback_result.data_type
                is_full_ohlcv = fallback_result.is_full_ohlcv
                fallback_used = True
                self.fallbacks_used.add(fallback_result.provider_name)
                normalized, qa = self._normalize_asset_frame(
                    raw_df=fallback_result.df,
                    request=request,
                    source_used=source_used,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    data_type=data_type,
                    is_full_ohlcv=is_full_ohlcv,
                    exchange_name=chosen_exchange,
                    exchange_symbol=request_exchange_symbol,
                )
                self._progress(
                    f"[market] QA {request.symbol}: normalized_rows={len(normalized)} passed={str(bool(qa['passed_qa'])).lower()} reason={qa['failure_reason']}"
                )
        return normalized, self._coverage_row(
            request=request,
            requested_start=requested_start,
            requested_end=requested_end,
            source_used=source_used,
            row_count=len(normalized),
            start_date=normalized["date_ts"].min().date().isoformat() if not normalized.empty else "",
            end_date=normalized["date_ts"].max().date().isoformat() if not normalized.empty else "",
            missing_days=int(qa["missing_days"]),
            forward_filled_days=int(qa["forward_filled_days"]),
            incomplete_rows_dropped=int(qa["incomplete_rows_dropped"]),
            failure_reason="" if qa["passed_qa"] else qa["failure_reason"],
            passed_qa=bool(qa["passed_qa"]),
            fetched=not normalized.empty,
            provider_attempts=provider_attempts,
            provider_failure_reasons=provider_failure_reasons,
            data_type=data_type,
            is_full_ohlcv=is_full_ohlcv,
            fallback_used=fallback_used,
        )

    def _fetch_asset_cmc(
        self,
        request: AssetRequest,
        requested_start: pd.Timestamp,
        requested_end: pd.Timestamp,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        if request.cmc_id is None:
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._coverage_row(
                request=request,
                requested_start=requested_start,
                requested_end=requested_end,
                source_used="",
                row_count=0,
                start_date="",
                end_date="",
                missing_days=0,
                forward_filled_days=0,
                incomplete_rows_dropped=0,
                failure_reason="missing_cmc_id",
                passed_qa=False,
                fetched=False,
                provider_attempts=["coinmarketcap"],
                provider_failure_reasons={"coinmarketcap": "missing_cmc_id"},
                data_type="none",
                is_full_ohlcv=False,
                fallback_used=False,
            )
        provider_attempts = ["coinmarketcap"]
        provider_failure_reasons: Dict[str, str] = {}
        raw_df = pd.DataFrame()
        source_used = ""
        data_type = "exchange_ohlcv"
        is_full_ohlcv = True
        if self.cmc_provider is None:
            raise MarketDataAgentError("CMC provider is not initialized")
        try:
            fixture = (
                Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cmc" / "ohlcv_historical_sample.json"
                if self.mcfg.get("use_fixtures", False)
                else None
            )
            raw_df = self.cmc_provider.fetch_ohlcv_historical(
                cmc_id=request.cmc_id,
                symbol=request.symbol,
                time_start=requested_start,
                time_end=requested_end - pd.Timedelta(days=1),
                interval=self.mcfg.get("interval", "daily"),
                convert=self.mcfg.get("convert", "USD"),
                fixture_path=fixture if fixture and fixture.exists() else None,
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)) and not bool(fixture and fixture.exists()),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
            )
            if not raw_df.empty:
                source_used = "coinmarketcap"
        except Exception as exc:
            provider_failure_reasons["coinmarketcap"] = str(exc)
        self.api_call_count_by_provider.update(self.cmc_provider.api_call_count_by_provider)
        self.cache_hit_count_by_provider.update(self.cmc_provider.cache_hit_count_by_provider)

        fallback_used = False
        if raw_df.empty and self.mcfg.get("fallback_to_free_providers", True):
            fallback_result = self.fallback_provider.fetch_daily_data(
                symbol=request.symbol,
                coin_id=request.coin_id,
                provider_priority=self.mcfg.get(
                    "fallback_provider_priority",
                    ["cryptocompare", "coingecko", "coincap", "coinpaprika"],
                ),
                requested_start_dt=requested_start.to_pydatetime(),
                requested_end_dt=(requested_end - pd.Timedelta(days=1)).to_pydatetime(),
                force_refresh=bool(self.mcfg.get("force_refresh", False)),
                live_api_enabled=bool(self.mcfg.get("live_api_enabled", True)),
                use_fixtures=bool(self.mcfg.get("use_fixtures", False)),
            )
            provider_attempts.extend([a for a in fallback_result.attempts if a not in provider_attempts])
            provider_failure_reasons.update(fallback_result.failure_reasons)
            if not fallback_result.df.empty:
                raw_df = fallback_result.df
                source_used = fallback_result.provider_name
                data_type = fallback_result.data_type
                is_full_ohlcv = fallback_result.is_full_ohlcv
                fallback_used = True

        if raw_df.empty:
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._coverage_row(
                request=request,
                requested_start=requested_start,
                requested_end=requested_end,
                source_used=source_used,
                row_count=0,
                start_date="",
                end_date="",
                missing_days=0,
                forward_filled_days=0,
                incomplete_rows_dropped=0,
                failure_reason=self._summarize_failure(provider_failure_reasons),
                passed_qa=False,
                fetched=False,
                provider_attempts=provider_attempts,
                provider_failure_reasons=provider_failure_reasons,
                data_type=data_type or "none",
                is_full_ohlcv=is_full_ohlcv,
                fallback_used=fallback_used,
            )
        normalized, qa = self._normalize_asset_frame(
            raw_df=raw_df,
            request=request,
            source_used=source_used,
            requested_start=requested_start,
            requested_end=requested_end,
            data_type=data_type,
            is_full_ohlcv=is_full_ohlcv,
            exchange_name=request.exchange or "",
            exchange_symbol=request.exchange_symbol or "",
        )
        return normalized, self._coverage_row(
            request=request,
            requested_start=requested_start,
            requested_end=requested_end,
            source_used=source_used,
            row_count=len(normalized),
            start_date=normalized["date_ts"].min().date().isoformat() if not normalized.empty else "",
            end_date=normalized["date_ts"].max().date().isoformat() if not normalized.empty else "",
            missing_days=int(qa["missing_days"]),
            forward_filled_days=int(qa["forward_filled_days"]),
            incomplete_rows_dropped=int(qa["incomplete_rows_dropped"]),
            failure_reason="" if qa["passed_qa"] else qa["failure_reason"],
            passed_qa=bool(qa["passed_qa"]),
            fetched=not normalized.empty,
            provider_attempts=provider_attempts,
            provider_failure_reasons=provider_failure_reasons,
            data_type=data_type,
            is_full_ohlcv=is_full_ohlcv,
            fallback_used=fallback_used,
        )

    def _normalize_asset_frame(
        self,
        raw_df: pd.DataFrame,
        request: AssetRequest,
        source_used: str,
        requested_start: pd.Timestamp,
        requested_end: pd.Timestamp,
        data_type: str,
        is_full_ohlcv: bool,
        exchange_name: str,
        exchange_symbol: str,
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        df = raw_df.copy()
        if df.empty:
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._qa_payload("empty_dataframe")

        df["date_ts"] = pd.to_datetime(df["date_ts"], utc=True).dt.normalize()
        for col in ["close", "volume", "open", "high", "low"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                df[col] = df[col].replace([np.inf, -np.inf], pd.NA)
            else:
                df[col] = pd.NA
        if is_full_ohlcv:
            df = df.dropna(subset=["date_ts", "open", "high", "low", "close"])
            df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()
        else:
            df = df.dropna(subset=["date_ts", "close"])
            df = df[df["close"] > 0].copy()
            df["open"] = pd.NA
            df["high"] = pd.NA
            df["low"] = pd.NA
        df = df.sort_values("date_ts").drop_duplicates(subset=["date_ts"], keep="last").reset_index(drop=True)

        incomplete_rows_dropped = 0
        if self.mcfg.get("drop_incomplete_current_day", True):
            current_day = self._as_of_date()
            before = len(df)
            df = df[df["date_ts"] < current_day].copy()
            incomplete_rows_dropped = before - len(df)
        if df.empty:
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._qa_payload(
                "empty_after_incomplete_drop",
                incomplete_rows_dropped=incomplete_rows_dropped,
            )

        missing_days = 0
        forward_filled_days = 0
        long_gap_flagged = False
        if self.mcfg.get("forward_fill_missing_days", True):
            full_index = pd.date_range(start=df["date_ts"].min(), end=df["date_ts"].max(), freq="D", tz="UTC")
            df = df.set_index("date_ts").reindex(full_index)
            valid_close = pd.to_numeric(df["close"], errors="coerce").replace([np.inf, -np.inf], pd.NA)
            valid_close = valid_close.where(valid_close > 0)
            missing_mask = valid_close.isna()
            missing_days = int(missing_mask.sum())
            if missing_days:
                max_gap = int(missing_mask.astype(int).groupby((~missing_mask).cumsum()).sum().max())
                max_allowed_gap = int(self.mcfg.get("max_forward_fill_gap_days", 3))
                if max_gap > max_allowed_gap:
                    policy = str(self.mcfg.get("long_gap_policy", "reject_asset")).lower()
                    if policy != "segment_and_flag":
                        # Legacy default: a single long gap rejects the whole asset.
                        return pd.DataFrame(columns=CANONICAL_COLUMNS), self._qa_payload(
                            "missing_gap_exceeds_max_forward_fill_gap",
                            missing_days=missing_days,
                            forward_filled_days=0,
                            incomplete_rows_dropped=incomplete_rows_dropped,
                        )
                    # Phase 8 (F): keep the most-recent contiguous segment, flag the gap.
                    cutoff = _largest_segment_after_long_gap(missing_mask.to_numpy(), max_allowed_gap)
                    df = df.iloc[cutoff:].copy()
                    long_gap_flagged = True
                    valid_close = pd.to_numeric(df["close"], errors="coerce").replace([np.inf, -np.inf], pd.NA)
                    valid_close = valid_close.where(valid_close > 0)
                    missing_mask = valid_close.isna()
                    missing_days = int(missing_mask.sum())
            close_series = valid_close.ffill()
            fillable_mask = missing_mask & close_series.notna() & (close_series > 0)
            forward_filled_days = int(fillable_mask.sum())
            df["close"] = close_series
            if is_full_ohlcv:
                prior_close = close_series.shift(1).ffill()
                df["open"] = df["open"].where(~fillable_mask, prior_close)
                df["high"] = df["high"].where(~fillable_mask, close_series)
                df["low"] = df["low"].where(~fillable_mask, close_series)
            else:
                df["open"] = pd.NA
                df["high"] = pd.NA
                df["low"] = pd.NA
            if self.mcfg.get("set_filled_volume_to_zero", True):
                df.loc[fillable_mask, "volume"] = 0.0
            df["is_forward_filled"] = fillable_mask.values
            # Phase 3: flag forward-filled full-OHLCV bars as SYNTHETIC. Their open/high/low
            # are carried-forward fabrications (high=low=close), not real intraday range, so
            # downstream range/vol features (ATR, hl_range) must exclude them. Close is a
            # legitimate mark-to-market carry and is retained.
            df["is_synthetic_ohlc"] = (fillable_mask.values & bool(is_full_ohlcv))
            df["has_long_gap"] = bool(long_gap_flagged)
            df = df.reset_index().rename(columns={"index": "date_ts"})
            if is_full_ohlcv:
                df = df.dropna(subset=["open", "high", "low", "close"])
                df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()
            else:
                df = df.dropna(subset=["close"])
                df = df[df["close"] > 0].copy()
        else:
            df = df.reset_index(drop=True)
            df["is_forward_filled"] = False
            df["is_synthetic_ohlc"] = False
            df["has_long_gap"] = False

        # Phase 8 (G): membership-aware history floor. In membership_aware mode the floor
        # drops to min_history_days_floor so genuinely short-lived members (delisted coins)
        # are kept instead of excluded for "insufficient history".
        min_history_days = int(self.mcfg.get("min_history_days", 365))
        if str(self.mcfg.get("min_history_days_policy", "absolute")).lower() == "membership_aware":
            min_history_days = int(self.mcfg.get("min_history_days_floor", 90))
        if len(df) < min_history_days:
            return pd.DataFrame(columns=CANONICAL_COLUMNS), self._qa_payload(
                f"history_below_min_history_days:{len(df)}<{min_history_days}",
                missing_days=missing_days,
                forward_filled_days=forward_filled_days,
                incomplete_rows_dropped=incomplete_rows_dropped,
            )

        # Phase 5: flag spike-and-revert price anomalies (bad prints), apply policy.
        df = self._flag_price_anomalies(df)

        df["symbol"] = request.symbol
        df["cmc_id"] = request.cmc_id
        df["exchange"] = exchange_name
        df["exchange_symbol"] = exchange_symbol
        if "market_cap" not in df.columns:
            df["market_cap"] = pd.NA
        df["source"] = source_used
        df["snapshot_id"] = self.snapshot_id
        df["fetched_at_utc"] = self._now_utc().isoformat()
        df["is_incomplete_dropped"] = False
        df["data_type"] = data_type
        df["is_full_ohlcv"] = bool(is_full_ohlcv)
        df["quote_currency"] = self.mcfg.get("quote_currency", "USD")
        # Phase 2: canonical USD dollar-volume, unit-correct per source.
        basis = volume_basis_for_source(source_used)
        df["volume_basis"] = basis
        # Phase 8 (E): tag the price definition basis (venue close vs composite index).
        df["price_basis"] = price_basis_for_source(source_used)
        # Phase 9 (#7): tag volume scope (single venue vs cross-venue global).
        df["volume_scope"] = volume_scope_for_source(source_used)
        vol = pd.to_numeric(df["volume"], errors="coerce")
        close_num = pd.to_numeric(df["close"], errors="coerce")
        if basis == "base":
            df["dollar_volume_usd"] = vol * close_num
        elif basis == "quote_usd":
            df["dollar_volume_usd"] = vol
        else:
            df["dollar_volume_usd"] = pd.NA
        # Phase 9 (#9): flag genuinely stale (frozen-feed) prices — long runs of an identical
        # REAL close (forward-filled synthetic flats are excluded, already flagged).
        df = self._flag_stale_prices(df)
        for col in CANONICAL_COLUMNS:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[CANONICAL_COLUMNS]
        qa_failure = self._qa_failure(df)
        return df, self._qa_payload(
            qa_failure,
            passed_qa=qa_failure == "",
            missing_days=missing_days,
            forward_filled_days=forward_filled_days,
            incomplete_rows_dropped=incomplete_rows_dropped,
        )

    def _qa_payload(
        self,
        failure_reason: str,
        passed_qa: bool = False,
        missing_days: int = 0,
        forward_filled_days: int = 0,
        incomplete_rows_dropped: int = 0,
    ) -> Dict[str, Any]:
        return {
            "passed_qa": passed_qa,
            "failure_reason": failure_reason,
            "missing_days": missing_days,
            "forward_filled_days": forward_filled_days,
            "incomplete_rows_dropped": incomplete_rows_dropped,
        }

    def _qa_failure(self, df: pd.DataFrame) -> str:
        if df.empty:
            return "empty_after_normalization"
        if df["date_ts"].isna().any():
            return "null_date_ts"
        if df["close"].isna().any() or (df["close"] <= 0).any():
            return "non_positive_close"
        full_mask = df["is_full_ohlcv"].astype(bool)
        if full_mask.any():
            if df.loc[full_mask, ["open", "high", "low"]].isna().any().any():
                return "missing_full_ohlc"
            if (df.loc[full_mask, ["open", "high", "low", "close"]] <= 0).any().any():
                return "non_positive_ohlc"
            if (df.loc[full_mask, "high"] < df.loc[full_mask, "low"]).any():
                return "high_below_low"
        partial_mask = ~full_mask
        if partial_mask.any() and df.loc[partial_mask, ["open", "high", "low"]].notna().any().any():
            return "fake_ohlc_on_partial_data"
        volume = pd.to_numeric(df["volume"], errors="coerce")
        if volume.dropna().lt(0).any():
            return "negative_volume"
        if df.duplicated(["symbol", "date_ts"]).any():
            return "duplicate_symbol_date"
        if self.mcfg.get("fail_on_binance_usage", True):
            if df["exchange"].astype(str).str.contains("binance", case=False).any():
                return "binance_exchange_detected"
            if not self.mcfg.get("allow_usdt_fallback", False) and df["exchange_symbol"].astype(str).str.contains("USDT", case=False).any():
                return "usdt_exchange_symbol_detected"
            if df["source"].astype(str).str.contains("binance", case=False).any():
                return "binance_source_detected"
        return ""

    def _coverage_row(
        self,
        request: AssetRequest,
        requested_start: pd.Timestamp,
        requested_end: pd.Timestamp,
        source_used: str,
        row_count: int,
        start_date: str,
        end_date: str,
        missing_days: int,
        forward_filled_days: int,
        incomplete_rows_dropped: int,
        failure_reason: str,
        passed_qa: bool,
        fetched: bool,
        provider_attempts: List[str],
        provider_failure_reasons: Dict[str, str],
        data_type: str,
        is_full_ohlcv: bool,
        fallback_used: bool,
    ) -> Dict[str, Any]:
        return {
            "symbol": request.symbol,
            "coin_id": request.coin_id,
            "cmc_id": request.cmc_id,
            "exchange": request.exchange,
            "exchange_symbol": request.exchange_symbol,
            "requested": True,
            "fetched": bool(fetched),
            "source_used": source_used,
            "row_count": int(row_count),
            "start_date": start_date,
            "end_date": end_date,
            "requested_start_date": requested_start.date().isoformat(),
            "requested_end_date": (requested_end - pd.Timedelta(days=1)).date().isoformat(),
            "missing_days": int(missing_days),
            "forward_filled_days": int(forward_filled_days),
            "incomplete_rows_dropped": int(incomplete_rows_dropped),
            "failure_reason": failure_reason,
            "provider_attempts": json.dumps(provider_attempts),
            "provider_failure_reasons": json.dumps(provider_failure_reasons, sort_keys=True),
            "data_type": data_type,
            "is_full_ohlcv": bool(is_full_ohlcv),
            "quote_currency": self.mcfg.get("quote_currency", "USD"),
            "fallback_used": bool(fallback_used),
            "passed_qa": bool(passed_qa),
        }

    @staticmethod
    def _summarize_failure(provider_failure_reasons: Dict[str, str]) -> str:
        if not provider_failure_reasons:
            return "all_providers_failed"
        key = next(iter(provider_failure_reasons))
        return f"{key}: {provider_failure_reasons[key]}"

    def persist(self, result: Dict[str, Any]) -> None:
        market_df: pd.DataFrame = result["market_ohlcv"]
        coverage_df: pd.DataFrame = result["coverage_report"]
        out_dir = self.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        market_path = out_dir / "market_ohlcv.parquet"
        coverage_path = out_dir / "market_coverage_report.parquet"
        manifest_path = out_dir / "market_manifest.json"
        quality_path = out_dir / "data_quality_daily.md"

        for col in CANONICAL_COLUMNS:
            if col not in market_df.columns:
                market_df[col] = pd.Series(dtype="object")
        required_coverage_cols = [
            "symbol", "coin_id", "cmc_id", "exchange", "exchange_symbol", "requested", "fetched", "source_used",
            "row_count", "start_date", "end_date", "requested_start_date", "requested_end_date", "missing_days",
            "forward_filled_days", "incomplete_rows_dropped", "failure_reason", "passed_qa", "is_full_ohlcv",
            "data_type", "quote_currency", "provider_attempts", "provider_failure_reasons", "fallback_used",
        ]
        for col in required_coverage_cols:
            if col not in coverage_df.columns:
                coverage_df[col] = pd.Series(dtype="object")

        requested_assets = int(result["requested_assets"])
        fetched_assets = int(result["fetched_assets"])
        full_ohlcv_assets = int(self.metrics.get("full_ohlcv_assets", 0))
        fatal_errors = result.get("fatal_errors", [])

        if fatal_errors:
            quality_lines = [
                "# Market Data Daily Quality Report",
                "",
                "## Final Status",
                "- PASS: false",
                "",
                "## Fatal Errors",
                *[f"- {err}" for err in fatal_errors],
            ]
            quality_path.write_text("\n".join(quality_lines))
            self.output_paths.update({"data_quality_daily": str(quality_path)})
            raise MarketDataAgentError("; ".join(fatal_errors))

        invalid_close = market_df["close"].notna() & (pd.to_numeric(market_df["close"], errors="coerce") <= 0) if not market_df.empty else pd.Series(dtype=bool)
        if not market_df.empty and invalid_close.any():
            raise MarketDataAgentError("Refusing to persist market output with non-positive close rows")
        if not market_df.empty:
            full_mask = market_df["is_full_ohlcv"].astype(bool)
            if full_mask.any():
                full_bad = (
                    market_df.loc[full_mask, ["open", "high", "low", "close"]]
                    .apply(pd.to_numeric, errors="coerce")
                    .le(0)
                    .any(axis=1)
                ) | (
                    pd.to_numeric(market_df.loc[full_mask, "high"], errors="coerce")
                    < pd.to_numeric(market_df.loc[full_mask, "low"], errors="coerce")
                )
                if full_bad.any():
                    raise MarketDataAgentError("Refusing to persist full OHLCV rows with invalid OHLC values")

        market_df.to_parquet(market_path, index=False)
        coverage_df.to_parquet(coverage_path, index=False)
        self._progress(
            f"[market] Persisting market outputs: rows={len(market_df)}, fetched_assets={fetched_assets}, full_ohlcv_assets={full_ohlcv_assets}"
        )

        by_symbol_dir = out_dir / "by_symbol"
        by_symbol_dir.mkdir(parents=True, exist_ok=True)
        for symbol, symbol_df in market_df.groupby("symbol"):
            symbol_path = by_symbol_dir / f"{symbol}_ohlcv.parquet"
            symbol_df.to_parquet(symbol_path, index=False)
            self.output_paths[f"ohlcv_{symbol}"] = str(symbol_path)

        # Phase 6: Hive-partitioned copy (symbol=…/year=…) for filter-pushdown reads,
        # IN ADDITION to the canonical flat market_ohlcv.parquet (never replacing it).
        if self.mcfg.get("write_partitioned_market", True):
            partitioned_dir = self._write_partitioned_market(market_df, out_dir)
            if partitioned_dir:
                self.output_paths["partitioned_dir"] = str(partitioned_dir)

        coverage_ratio = float(fetched_assets / max(requested_assets, 1))
        full_ohlcv_coverage_ratio = float(full_ohlcv_assets / max(requested_assets, 1))
        warnings: List[str] = []
        limitations: List[str] = []
        if fetched_assets > full_ohlcv_assets:
            warnings.append(f"{fetched_assets - full_ohlcv_assets} assets persisted with partial fallback history")
            limitations.append(
                "Partial fallback sources do not provide full OHLCV and must not count toward OHLCV-required research coverage."
            )

        manifest = {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "data_content_hash": self._content_hash(market_df),
            "as_of_date": self._as_of_date().date().isoformat(),
            "price_anomalies_total": int(self.metrics.get("price_anomalies_total", 0)),
            "anomaly_policy": str(self.mcfg.get("anomaly_policy", "flag_only")),
            "created_at_utc": self._now_utc().isoformat(),
            "universe_snapshot_date": self.universe_snapshot_date.date().isoformat() if self.universe_snapshot_date is not None else "",
            "requested_assets": requested_assets,
            "raw_fetched_assets": int(coverage_df["fetched"].fillna(False).astype(bool).sum()) if "fetched" in coverage_df.columns else fetched_assets,
            "qa_passed_assets": int(coverage_df["passed_qa"].fillna(False).astype(bool).sum()) if "passed_qa" in coverage_df.columns else fetched_assets,
            "fetched_assets": fetched_assets,
            "full_ohlcv_assets": full_ohlcv_assets,
            "persisted_assets": int(market_df["symbol"].nunique()) if not market_df.empty else 0,
            "failed_assets": sorted(self.failed_assets),
            "coverage_ratio": coverage_ratio,
            "full_ohlcv_coverage_ratio": full_ohlcv_coverage_ratio,
            "backfill_days": int(self.mcfg.get("backfill_days", 2000)),
            "provider": "coinmarketcap" if self.mcfg.get("use_cmc_ohlcv", False) else "",
            "lookback_days": int(self.mcfg.get("lookback_days", self.mcfg.get("backfill_days", 2000))),
            "data_frequency": self.mcfg.get("interval", self.mcfg.get("timeframe", "1d")),
            "min_history_days": int(self.mcfg.get("min_history_days", 365)),
            "exchanges_used": sorted(self.exchanges_used),
            "fallback_providers_used": sorted(self.fallbacks_used),
            "api_call_count_by_provider": {
                **self.api_call_count_by_provider,
                **{k: int(v) for k, v in self.http.api_call_count_by_provider.items()},
            },
            "cache_hit_count_by_provider": {
                **self.cache_hit_count_by_provider,
                **{k: int(v) for k, v in self.http.cache_hit_count_by_provider.items()},
            },
            "output_files": {
                "market_ohlcv": str(market_path),
                "coverage_report": str(coverage_path),
                "manifest": str(manifest_path),
                "data_quality_daily": str(quality_path),
            },
            "warnings": warnings,
            "limitations": limitations,
        }
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)

        quality_lines = [
            "# Market Data Daily Quality Report",
            "",
            f"- Requested assets: {requested_assets}",
            f"- Fetched assets: {fetched_assets}",
            f"- Full OHLCV assets: {full_ohlcv_assets}",
            f"- Failed assets: {len(self.failed_assets)}",
            f"- Coverage ratio: {coverage_ratio:.3f}",
        ]
        failed_rows = coverage_df[coverage_df["passed_qa"] == False] if not coverage_df.empty else pd.DataFrame()  # noqa: E712
        quality_lines.extend(["", "## Failures", ""])
        if failed_rows.empty:
            quality_lines.append("None")
        else:
            for _, row in failed_rows.iterrows():
                quality_lines.append(f"- {row['symbol']}: {row['failure_reason']}")
        quality_path.write_text("\n".join(quality_lines))

        self.output_paths.update(
            {
                "market_ohlcv": str(market_path),
                "coverage_report": str(coverage_path),
                "manifest": str(manifest_path),
                "data_quality_daily": str(quality_path),
            }
        )

        # Phase 7: log this run to MLflow (provenance + artifacts). Non-fatal if absent.
        self._log_to_mlflow(manifest, [manifest_path, quality_path])

    def _log_to_mlflow(self, manifest: Dict[str, Any], artifact_paths: List[Path]) -> None:
        """Log the market run to MLflow: params, metrics, content hash, and artifacts.

        Backed by ``./mlruns`` (config ``mlflow.tracking_uri``). Fully defensive — if
        MLflow is not importable or logging fails, it warns and returns without affecting
        the run. Gated by ``mlflow.log_market_run`` (default True).
        """
        mlcfg = self.cfg.get("mlflow", {}) or {}
        if not mlcfg.get("log_market_run", True):
            return
        try:
            import mlflow
        except Exception as exc:  # pragma: no cover - environment dependent
            self.logger.warning(f"mlflow not available, skipping logging: {exc}")
            return
        try:
            uri = str(mlcfg.get("tracking_uri", "mlruns"))
            if "://" not in uri and not Path(uri).is_absolute():
                uri = str(Path(self.cfg["_project_root"]) / uri)
            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(mlcfg.get("experiment_name", "CHF_experiments"))
            with mlflow.start_run(run_name=f"market_{self.run_id}"):
                mlflow.set_tags(
                    {
                        "agent": "MarketDataAgent",
                        "run_id": self.run_id,
                        "snapshot_id": self.snapshot_id or "",
                        "data_content_hash": manifest.get("data_content_hash", ""),
                    }
                )
                params = {
                    k: manifest.get(k)
                    for k in [
                        "as_of_date", "universe_snapshot_date", "requested_assets",
                        "lookback_days", "min_history_days", "anomaly_policy", "provider",
                    ]
                    if manifest.get(k) is not None
                }
                if params:
                    mlflow.log_params(params)
                metrics = {
                    k: float(manifest.get(k, 0) or 0)
                    for k in [
                        "fetched_assets", "full_ohlcv_assets", "persisted_assets",
                        "coverage_ratio", "full_ohlcv_coverage_ratio", "price_anomalies_total",
                    ]
                }
                mlflow.log_metrics(metrics)
                if mlcfg.get("log_artifacts", True):
                    for art in artifact_paths:
                        try:
                            if art and Path(art).exists():
                                mlflow.log_artifact(str(art))
                        except Exception as exc:  # pragma: no cover
                            self.logger.warning(f"mlflow artifact log failed for {art}: {exc}")
            self.metrics["mlflow_logged"] = 1.0
        except Exception as exc:  # pragma: no cover - environment dependent
            self.logger.warning(f"mlflow logging failed (non-fatal): {exc}")

    def _market_symbol_for_provider(
        self,
        provider: CCXTMarketProvider,
        request: AssetRequest,
        exchange_name: str,
    ) -> Optional[str]:
        allow_usdt = bool(self.mcfg.get("allow_usdt_fallback", False))
        if exchange_name == request.exchange:
            if provider.has_market(request.exchange_symbol):
                return request.exchange_symbol
        return provider.resolve_market_symbol(
            request.symbol,
            preferred_quote=str(self.mcfg.get("quote_currency", "USD")),
            allow_usdt=allow_usdt,
        )

    def _fatal_errors(
        self,
        requested_assets: int,
        fetched_assets: int,
        full_ohlcv_assets: int,
        coverage_df: pd.DataFrame,
        market_df: pd.DataFrame,
    ) -> List[str]:
        errors: List[str] = []
        min_assets = int(self.mcfg.get("minimum_assets_required", 50))
        max_failed = int(self.mcfg.get("maximum_failed_assets_allowed", max(0, requested_assets - min_assets)))
        if fetched_assets == 0:
            errors.append("Market OHLCV fetch returned zero successful assets")
        if self.mcfg.get("fail_on_empty_output", True) and market_df.empty:
            errors.append("market_ohlcv.parquet would be empty")
        if coverage_df.empty:
            errors.append("market_coverage_report.parquet would be empty")
        elif coverage_df["provider_attempts"].astype(str).str.len().eq(0).all():
            errors.append("no provider attempts were recorded")
        if full_ohlcv_assets < min_assets:
            errors.append(f"Full OHLCV assets below minimum_assets_required: {full_ohlcv_assets} < {min_assets}")
        if len(self.failed_assets) > max_failed:
            errors.append(
                f"Failed asset count exceeded maximum_failed_assets_allowed: {len(self.failed_assets)} > {max_failed}"
            )
        return errors
