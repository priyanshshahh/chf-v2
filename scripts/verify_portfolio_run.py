#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from configs.config import load_config


FORBIDDEN_OUTPUT_COLUMNS = {
    "actual_return",
    "label_value",
    "future_return",
    "realized_return",
    "actual_forward_return",
    "target",
    "y",
    "y_",
}


def _merge(cfg: Dict[str, Any], section: str | None) -> Dict[str, Any]:
    if not section:
        return cfg
    merged = dict(cfg)
    base_key = "portfolio" if section.startswith("portfolio") else section
    base = dict(cfg.get(base_key, {}))
    base.update(cfg.get(section, {}))
    merged[base_key] = base
    return merged


def _resolve_output_dir(cfg: Dict[str, Any]) -> Path:
    raw = cfg.get("portfolio", {}).get("output_dir", "data/allocations")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cfg["_project_root"]) / path
    return path


def validate_portfolio_outputs(cfg: Dict[str, Any]) -> List[str]:
    pcfg = cfg.get("portfolio", {})
    out_dir = _resolve_output_dir(cfg)
    alloc_path = out_dir / "allocations_from_predictions.parquet"
    coverage_path = out_dir / "allocation_coverage_report.parquet"
    manifest_path = out_dir / "allocation_manifest.json"
    market_path = Path(cfg["_project_root"]) / pcfg.get("market_path", "data/raw/market/market_ohlcv.parquet")
    failures: List[str] = []
    for path in [alloc_path, coverage_path, manifest_path]:
        if not path.exists():
            failures.append(f"FAIL: missing {path.name}")
    if failures:
        return failures

    allocations = pd.read_parquet(alloc_path)
    coverage = pd.read_parquet(coverage_path)
    with open(manifest_path, "r") as fh:
        manifest = json.load(fh)
    market = pd.read_parquet(market_path) if market_path.exists() else pd.DataFrame()

    if allocations.empty:
        failures.append("FAIL: allocations_from_predictions.parquet is empty")
    if coverage.empty:
        failures.append("FAIL: allocation_coverage_report.parquet is empty")
    required_alloc_cols = {
        "date_ts", "signal_date", "execution_date", "symbol", "model_name", "horizon_days", "predicted_return",
        "prediction_rank", "prediction_zscore", "signal_score", "side", "raw_weight", "target_weight", "weight",
        "previous_weight", "turnover_contribution", "risk_estimate", "rebalance_frequency", "strategy_name",
        "alpha_gate_passed", "allocation_mode", "snapshot_id", "run_id", "created_at_utc",
    }
    required_cov_cols = {
        "date_ts", "signal_date", "execution_date", "strategy_name", "model_name", "horizon_days", "feature_set",
        "candidate_count", "selected_count", "dropped_missing_prediction_count", "dropped_missing_price_count",
        "dropped_missing_risk_count", "gross_exposure", "net_exposure", "weight_sum", "cash_weight",
        "max_weight_actual", "turnover", "alpha_gate_passed", "allocation_mode", "passed_qa", "failure_reason",
    }
    for col in sorted(required_alloc_cols - set(allocations.columns)):
        failures.append(f"FAIL: allocations_from_predictions.parquet missing required column {col}")
    for col in sorted(required_cov_cols - set(coverage.columns)):
        failures.append(f"FAIL: allocation_coverage_report.parquet missing required column {col}")
    if failures:
        return failures

    if allocations.duplicated(["date_ts", "symbol", "strategy_name"]).any():
        failures.append("FAIL: duplicate date_ts + symbol + strategy_name rows")
    numeric_weights = pd.to_numeric(allocations["weight"], errors="coerce")
    if numeric_weights.isna().any() or (~np.isfinite(numeric_weights)).any():
        failures.append("FAIL: NaN or infinite weights detected")
    if not pcfg.get("allow_short", False) and (numeric_weights < -1e-12).any():
        failures.append("FAIL: negative weights detected in long-only mode")
    max_weight = float(pcfg.get("max_weight", pcfg.get("max_position_weight", 0.15)))
    if (numeric_weights > max_weight + 1e-8).any():
        failures.append("FAIL: weight exceeds max_weight")
    gross = allocations.groupby(["date_ts", "strategy_name"])["weight"].apply(lambda s: float(s.abs().sum()))
    if (gross > float(pcfg.get("target_gross_exposure", 1.0)) + 1e-6).any():
        failures.append("FAIL: gross exposure exceeds configured target")
    joined = coverage.set_index(["execution_date", "strategy_name"])
    alloc_sums = allocations.groupby(["execution_date", "strategy_name"])["weight"].sum()
    for key, val in alloc_sums.items():
        cash_weight = float(joined.loc[key, "cash_weight"]) if key in joined.index else np.nan
        if not np.isfinite(cash_weight):
            failures.append("FAIL: missing cash_weight for allocation coverage row")
            continue
        if abs((float(val) + cash_weight) - float(pcfg.get("target_gross_exposure", 1.0))) > 1e-4:
            failures.append("FAIL: weight_sum plus cash_weight does not reconcile to target exposure")
            break
    signal = pd.to_datetime(allocations["signal_date"], utc=True)
    execution = pd.to_datetime(allocations["execution_date"], utc=True)
    if int(pcfg.get("execution_lag_days", 1)) > 0 and (execution <= signal).any():
        failures.append("FAIL: same-day or lookahead execution detected")
    if not (pd.to_datetime(allocations["date_ts"], utc=True) == execution).all():
        failures.append("FAIL: date_ts != execution_date")
    lowered = {str(col).lower() for col in allocations.columns}
    if lowered & FORBIDDEN_OUTPUT_COLUMNS:
        failures.append("FAIL: forbidden label/target/realized columns present in allocations output")
    qa_failures = coverage[(coverage["selected_count"] > 0) & (~coverage["passed_qa"].fillna(False))]
    if not qa_failures.empty:
        failures.append("FAIL: coverage report contains failed QA rows for non-empty allocations")
    if int(manifest.get("allocation_rows", -1)) != int(len(allocations)):
        failures.append("FAIL: manifest allocation_rows does not match actual rows")
    if not market.empty:
        market["date_ts"] = pd.to_datetime(market["date_ts"], utc=True).dt.normalize()
        market_pairs = set(zip(market["date_ts"], market["symbol"]))
        for row in allocations[["date_ts", "symbol"]].itertuples(index=False):
            if (row.date_ts, row.symbol) not in market_pairs:
                failures.append("FAIL: allocation exists on date/symbol missing market close price")
                break
    min_assets = int(pcfg.get("min_assets_per_rebalance", 5))
    bad_cov = coverage[(coverage["selected_count"] > 0) & (coverage["selected_count"] < min_assets) & coverage["passed_qa"].fillna(False)]
    if not bad_cov.empty:
        failures.append("FAIL: selected_count below min_assets_per_rebalance on passed QA rebalance")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/run_config.yaml")
    parser.add_argument("--section", default="portfolio")
    args = parser.parse_args()
    cfg = _merge(load_config(Path(args.config)), args.section)
    failures = validate_portfolio_outputs(cfg)
    if failures:
        print("Portfolio validation: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Portfolio validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
