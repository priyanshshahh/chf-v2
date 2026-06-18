#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config  # noqa: E402


def _merge_section(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section or section == "market_data":
        return cfg
    merged = dict(cfg)
    if section in cfg:
        target = "market_data" if section.startswith("market_data") else section
        merged[target] = dict(cfg.get(target, {}))
        merged[target].update(cfg.get(section, {}))
    return merged


def _resolve_output_dir(cfg: Dict[str, Any]) -> Path:
    path = Path(cfg["_project_root"]) / "data" / "raw" / "market"
    return path


def _bad_price_rows(df: pd.DataFrame, mask: pd.Series) -> List[Dict[str, Any]]:
    cols = [
        "date_ts",
        "symbol",
        "exchange",
        "exchange_symbol",
        "source",
        "data_type",
        "is_full_ohlcv",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    keep = [col for col in cols if col in df.columns]
    return df.loc[mask, keep].head(10).to_dict(orient="records")


def inspect_market_outputs(cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    mcfg = cfg.get("market_data", {})
    cmc_mode = bool(mcfg.get("use_cmc_ohlcv", False)) or mcfg.get("primary_provider") == "coinmarketcap"
    out_dir = _resolve_output_dir(cfg)
    market_path = out_dir / "market_ohlcv.parquet"
    coverage_path = out_dir / "market_coverage_report.parquet"
    manifest_path = out_dir / "market_manifest.json"
    failures: List[str] = []
    warnings: List[str] = []

    for path in [market_path, coverage_path, manifest_path]:
        if not path.exists():
            failures.append(f"missing file: {path}")
        if "demo" in str(path).lower() and mcfg.get("fail_on_demo_data", True):
            failures.append(f"demo path rejected: {path}")
    if failures:
        return failures, warnings

    con = duckdb.connect(database=":memory:")
    market = con.execute(f"SELECT * FROM read_parquet('{market_path}')").df()
    coverage = con.execute(f"SELECT * FROM read_parquet('{coverage_path}')").df()

    required_market_cols = {
        "date_ts", "symbol", "exchange", "exchange_symbol", "open", "high", "low", "close",
        "volume", "source", "snapshot_id", "fetched_at_utc", "is_forward_filled", "is_incomplete_dropped",
        "data_type", "is_full_ohlcv", "quote_currency",
    }
    required_coverage_cols = {
        "symbol", "coin_id", "exchange", "exchange_symbol", "requested", "fetched", "source_used",
        "row_count", "start_date", "end_date", "requested_start_date", "requested_end_date", "missing_days",
        "forward_filled_days", "incomplete_rows_dropped", "failure_reason", "passed_qa", "is_full_ohlcv",
        "data_type", "quote_currency", "provider_attempts", "provider_failure_reasons", "fallback_used",
    }
    if cmc_mode:
        required_market_cols.update({"cmc_id", "market_cap"})
        required_coverage_cols.add("cmc_id")
    missing_market = sorted(required_market_cols - set(market.columns))
    missing_coverage = sorted(required_coverage_cols - set(coverage.columns))
    for col in missing_market:
        failures.append(f"FAIL: market_ohlcv.parquet missing required column {col}")
    for col in missing_coverage:
        failures.append(f"FAIL: market_coverage_report.parquet missing required column {col}")
    if missing_market or missing_coverage:
        return failures, warnings
    if market.empty:
        failures.append("market_ohlcv.parquet is empty")
    if coverage.empty:
        failures.append("market_coverage_report.parquet is empty")
    if failures:
        return failures, warnings
    if market["date_ts"].isna().any():
        failures.append("date_ts contains nulls")
    close_numeric = pd.to_numeric(market["close"], errors="coerce")
    close_null_mask = close_numeric.isna()
    if close_null_mask.any():
        failures.append("FAIL: close contains null/non-numeric values")
        counts = market.loc[close_null_mask, "symbol"].astype(str).value_counts().to_dict()
        warnings.append(f"bad close row counts by symbol: {counts}")
        warnings.append(f"first bad close rows: {_bad_price_rows(market, close_null_mask)}")
    close_non_positive_mask = close_numeric <= 0
    if close_non_positive_mask.any():
        failures.append("FAIL: close contains non-positive values")
        counts = market.loc[close_non_positive_mask, "symbol"].astype(str).value_counts().to_dict()
        warnings.append(f"bad close row counts by symbol: {counts}")
        warnings.append(f"first bad close rows: {_bad_price_rows(market, close_non_positive_mask)}")
    full_mask = market["is_full_ohlcv"].astype(bool)
    if full_mask.any():
        full = market.loc[full_mask].copy()
        open_numeric = pd.to_numeric(full["open"], errors="coerce")
        high_numeric = pd.to_numeric(full["high"], errors="coerce")
        low_numeric = pd.to_numeric(full["low"], errors="coerce")
        close_full_numeric = pd.to_numeric(full["close"], errors="coerce")
        full_null_mask = open_numeric.isna() | high_numeric.isna() | low_numeric.isna() | close_full_numeric.isna()
        if full_null_mask.any():
            failures.append("FAIL: full OHLCV rows contain null/non-numeric OHLC values")
            warnings.append(f"first bad full OHLCV rows: {_bad_price_rows(full, full_null_mask)}")
        full_non_positive_mask = (open_numeric <= 0) | (high_numeric <= 0) | (low_numeric <= 0) | (close_full_numeric <= 0)
        if full_non_positive_mask.any():
            failures.append("FAIL: full OHLCV rows contain non-positive OHLC values")
            warnings.append(f"first bad full OHLCV rows: {_bad_price_rows(full, full_non_positive_mask)}")
        full_high_low_mask = high_numeric < low_numeric
        if full_high_low_mask.any():
            failures.append("FAIL: high < low found on full OHLCV rows")
            warnings.append(f"first bad high/low rows: {_bad_price_rows(full, full_high_low_mask)}")
    partial_mask = ~full_mask
    if partial_mask.any():
        if market.loc[partial_mask, ["open", "high", "low"]].notna().any().any():
            failures.append("partial fallback rows contain fake open/high/low values")
    volume = market["volume"]
    if pd.to_numeric(volume, errors="coerce").dropna().lt(0).any():
        failures.append("volume contains negative values")
    # Phase 2: canonical USD dollar-volume sanity (column-gated for legacy files).
    if "volume_basis" in market.columns:
        bad_basis = set(market["volume_basis"].dropna().astype(str).unique()) - {"base", "quote_usd", "none"}
        if bad_basis:
            failures.append(f"unknown volume_basis values: {sorted(bad_basis)}")
    if "dollar_volume_usd" in market.columns:
        if pd.to_numeric(market["dollar_volume_usd"], errors="coerce").dropna().lt(0).any():
            failures.append("dollar_volume_usd contains negative values")
    # Phase 3: synthetic forward-filled bars must be flagged AND forward-filled (consistency).
    if "is_synthetic_ohlc" in market.columns and "is_forward_filled" in market.columns:
        synth = market["is_synthetic_ohlc"].fillna(False).astype(bool)
        ff = market["is_forward_filled"].fillna(False).astype(bool)
        if (synth & ~ff).any():
            failures.append("is_synthetic_ohlc=true on a row that is not forward-filled (inconsistent)")
    # Phase 5: price-anomaly density (warning — flagged bad prints should be rare).
    if "is_price_anomaly" in market.columns and len(market):
        frac = market["is_price_anomaly"].fillna(False).astype(bool).mean()
        if frac > 0.01:
            warnings.append(f"price anomaly fraction is high: {frac:.3%} of rows flagged is_price_anomaly")
    # Phase 8 (E): warn if any asset mixes price bases (venue close spliced with index close).
    if "price_basis" in market.columns and "symbol" in market.columns:
        per_sym = market.groupby("symbol")["price_basis"].nunique()
        mixed = per_sym[per_sym > 1]
        if len(mixed):
            warnings.append(f"{len(mixed)} asset(s) mix price_basis within their series (e.g. {list(mixed.index[:5])})")
    # Phase 9: volume_scope valid values; stale-price density (warning only).
    if "volume_scope" in market.columns:
        bad_scope = set(market["volume_scope"].dropna().astype(str).unique()) - {"single_venue", "global", "unknown"}
        if bad_scope:
            failures.append(f"unknown volume_scope values: {sorted(bad_scope)}")
    if "is_stale_price" in market.columns and len(market):
        frac = market["is_stale_price"].fillna(False).astype(bool).mean()
        if frac > 0.05:
            warnings.append(f"stale-price fraction is high: {frac:.3%} of rows flagged is_stale_price (frozen feeds?)")
    if market.duplicated(["symbol", "date_ts"]).any():
        failures.append("duplicate symbol + date_ts rows found")
    if cmc_mode and market.duplicated(["cmc_id", "date_ts"]).any():
        failures.append("duplicate cmc_id + date_ts rows found")
    if market["exchange"].astype(str).str.contains("binance", case=False).any():
        failures.append("binance exchange detected")
    if market["source"].astype(str).str.contains("binance", case=False).any():
        failures.append("binance source detected")
    if not bool(mcfg.get("allow_usdt_fallback", False)) and market["exchange_symbol"].astype(str).str.contains("USDT", case=False).any():
        failures.append("USDT exchange symbol detected")

    min_assets = int(mcfg.get("minimum_assets_required", 1))
    passed_symbols = set(
        coverage.loc[
            coverage["is_full_ohlcv"].astype(bool) & coverage["passed_qa"].astype(bool),
            "symbol",
        ].astype(str)
    )
    full_ohlcv_assets = market.loc[
        market["is_full_ohlcv"].astype(bool) & market["symbol"].astype(str).isin(passed_symbols),
        "symbol",
    ].nunique() if not market.empty else 0
    if full_ohlcv_assets < min_assets:
        failures.append(f"full OHLCV assets below minimum_assets_required: {full_ohlcv_assets} < {min_assets}")

    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    if not manifest.get("output_files"):
        failures.append("manifest missing output_files")
    if "failed_assets" not in manifest:
        failures.append("manifest missing failed_assets")
    # Phase 4: determinism provenance (warning-level; legacy manifests predate these).
    if not market.empty and not manifest.get("data_content_hash"):
        warnings.append("manifest missing data_content_hash (re-run market stage to populate)")
    if cmc_mode:
        if "cmc_id" not in market.columns or market["cmc_id"].isna().any():
            failures.append("cmc market rows missing cmc_id")
        if not market["source"].astype(str).str.contains("coinmarketcap", case=False).any():
            failures.append("coinmarketcap source not found in market output")
        current_day = pd.Timestamp.now(tz="UTC").normalize()
        if (pd.to_datetime(market["date_ts"], utc=True) >= current_day).any():
            failures.append("current-day incomplete candle detected")
        if int(manifest.get("lookback_days") or 0) < 1095:
            failures.append("manifest lookback_days < 1095 for CMC mode")

    # --- Phase 1: point-in-time universe membership checks (gated) ---
    if bool(mcfg.get("attach_membership_mask", False)):
        if "is_universe_member" not in market.columns:
            failures.append("attach_membership_mask=true but is_universe_member column missing")
        elif market["is_universe_member"].notna().sum() == 0:
            failures.append("attach_membership_mask=true but is_universe_member is entirely null")
    if str(mcfg.get("universe_membership_mode", "latest_snapshot")).lower() == "union_full_history":
        months = pd.to_datetime(market["date_ts"], utc=True).dt.to_period("M").nunique()
        if months < 2:
            failures.append(
                f"union_full_history mode but market panel spans only {months} month(s) "
                "(survivorship collapse not resolved)"
            )
    return failures, warnings


def validate_market_outputs(cfg: Dict[str, Any]) -> List[str]:
    failures, _warnings = inspect_market_outputs(cfg)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MarketDataAgent research-mode outputs")
    parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    parser.add_argument("--section", default="market_data", help="Config section to merge into market_data")
    args = parser.parse_args()
    cfg = load_config(Path(args.config) if args.config else None)
    cfg = _merge_section(cfg, args.section)
    failures, warnings = inspect_market_outputs(cfg)
    for warning in warnings:
        print(f"Market validation warning: {warning}")
    if failures:
        print("Market validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Market validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
