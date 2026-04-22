"""
CHF Ablation Study
==================
Runs two model variants to isolate the marginal value of on-chain features:

  1. market_only  — uses only price-derived features
     (momentum_7d, momentum_14d, momentum_30d, momentum_90d,
      volatility_30d, beta_60d, skewness_30d, turnover_ratio)

  2. market_plus_onchain — uses all features including on-chain
     (adds nvt_ratio, mvrv_proxy, active_address_growth, tvl_ratio)

Each variant runs the full walk-forward CV and logs to MLflow.
Results are saved to data/reports/ablation_results.json.

Run command
-----------
python main.py ablation

Success criterion
-----------------
data/reports/ablation_results.json exists with both variants' Rank IC values.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# ── Feature column groups ────────────────────────────────────────────────────
MARKET_ONLY_FEATURES = [
    "ret_7d",
    "ret_14d",
    "ret_30d",
    "ret_90d",
    "vol_30d",
    "beta_btc_60d",
    "skew_30d",
    "vol_ratio_30d",
    "reversal_3_30",
]

ONCHAIN_FEATURES = [
    "nvt_ratio",
    "mvrv_proxy",
    "adr_growth_30d",
    "tvl_ratio",
]

ALL_FEATURES = MARKET_ONLY_FEATURES + ONCHAIN_FEATURES


def run_ablation(
    feature_df: pd.DataFrame,
    label_df: pd.DataFrame,
    cfg: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run ablation study comparing market-only vs market+onchain feature sets.

    Parameters
    ----------
    feature_df : pd.DataFrame
        Full feature store with columns for all features.
    label_df : pd.DataFrame
        Label DataFrame with forward return columns.
    cfg : dict
        Project config dict.
    output_dir : Path, optional
        Where to save ablation_results.json.

    Returns
    -------
    dict with keys 'market_only' and 'market_plus_onchain', each containing
    walk-forward Rank IC statistics.
    """
    from models.walk_forward import WalkForwardValidator

    results: Dict[str, Any] = {}
    label_col = "label_value"
    if label_col not in label_df.columns:
        configured_horizon = cfg.get("modeling", {}).get("default_horizon", 7)
        fallback_col = f"fwd_return_{configured_horizon}d"
        if fallback_col in label_df.columns:
            label_col = fallback_col
        else:
            fwd_cols = [c for c in label_df.columns if c.startswith("fwd_return")]
            if not fwd_cols:
                return {"error": "No label column found"}
            label_col = fwd_cols[0]

    # Merge features and labels
    label_subset_cols = ["symbol", "date_ts", label_col]
    if label_col == "label_value" and "horizon_days" in label_df.columns:
        target_horizon = cfg.get("modeling", {}).get("default_horizon", 7)
        label_df = label_df[label_df["horizon_days"] == target_horizon].copy()

    panel = feature_df.merge(
        label_df[label_subset_cols],
        on=["symbol", "date_ts"],
        how="inner",
    ).dropna(subset=[label_col])

    model_cfg = cfg.get("modeling", {})
    n_splits = model_cfg.get("n_splits", 5)
    embargo_days = model_cfg.get("embargo_days", 7)
    test_size = model_cfg.get("test_size_days", 90)

    for variant_name, feature_cols in [
        ("market_only", MARKET_ONLY_FEATURES),
        ("market_plus_onchain", ALL_FEATURES),
    ]:
        # Use only features that actually exist in the panel
        available = [c for c in feature_cols if c in panel.columns]
        if not available:
            results[variant_name] = {"error": f"No features available: {feature_cols}"}
            continue

        X = panel[available].copy()
        y = panel[label_col].copy()

        # Drop rows where all features are NaN
        valid_mask = X.notna().any(axis=1) & y.notna()
        X = X[valid_mask].fillna(0)
        y = y[valid_mask]

        if len(X) < 100:
            results[variant_name] = {
                "error": f"Insufficient data: {len(X)} rows",
                "n_features": len(available),
                "features_used": available,
            }
            continue

        # Walk-forward CV
        wfv = WalkForwardValidator(
            n_splits=n_splits,
            embargo_days=embargo_days,
            test_size_days=test_size,
        )

        fold_ics: List[float] = []
        fold_hit_rates: List[float] = []

        for fold_idx, (train_idx, test_idx) in enumerate(wfv.split(X)):
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

            if len(X_train) < 20 or len(X_test) < 5:
                continue

            # Use LightGBM if available, else RandomForest
            try:
                import lightgbm as lgb
                model = lgb.LGBMRegressor(
                    n_estimators=100,
                    learning_rate=0.05,
                    max_depth=4,
                    num_leaves=15,
                    random_state=cfg.get("project", {}).get("seed", 42),
                    verbose=-1,
                )
            except ImportError:
                from sklearn.ensemble import RandomForestRegressor
                model = RandomForestRegressor(
                    n_estimators=100,
                    max_depth=5,
                    random_state=cfg.get("project", {}).get("seed", 42),
                )

            model.fit(X_train, y_train)
            preds = model.predict(X_test)

            # Rank IC (Spearman correlation)
            from scipy.stats import spearmanr
            ic, _ = spearmanr(preds, y_test.values)
            if not np.isnan(ic):
                fold_ics.append(float(ic))

            # Hit rate
            hit = float(np.mean(np.sign(preds) == np.sign(y_test.values)))
            fold_hit_rates.append(hit)

        results[variant_name] = {
            "n_features": len(available),
            "features_used": available,
            "n_folds": len(fold_ics),
            "mean_rank_ic": float(np.mean(fold_ics)) if fold_ics else None,
            "std_rank_ic": float(np.std(fold_ics)) if fold_ics else None,
            "mean_hit_rate": float(np.mean(fold_hit_rates)) if fold_hit_rates else None,
            "fold_ics": fold_ics,
            "label_col": label_col,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # Compute marginal value of on-chain features
    if "market_only" in results and "market_plus_onchain" in results:
        mo_ic = results["market_only"].get("mean_rank_ic")
        mc_ic = results["market_plus_onchain"].get("mean_rank_ic")
        if mo_ic is not None and mc_ic is not None:
            results["onchain_marginal_ic_lift"] = round(mc_ic - mo_ic, 6)
            results["onchain_features_help"] = mc_ic > mo_ic

    # Save results
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / "ablation_results.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"[Ablation] Results saved to {out_path}")

    return results


def print_ablation_summary(results: Dict[str, Any]) -> None:
    """Print a human-readable ablation summary."""
    print("\n" + "=" * 60)
    print("ABLATION STUDY RESULTS")
    print("=" * 60)
    for variant in ("market_only", "market_plus_onchain"):
        if variant not in results:
            continue
        r = results[variant]
        if "error" in r:
            print(f"\n[{variant}] ERROR: {r['error']}")
            continue
        print(f"\n[{variant}]")
        print(f"  Features used : {r['n_features']}")
        print(f"  Folds         : {r['n_folds']}")
        ic = r.get("mean_rank_ic")
        std = r.get("std_rank_ic")
        hr = r.get("mean_hit_rate")
        print(f"  Mean Rank IC  : {ic:.4f} ± {std:.4f}" if ic is not None else "  Mean Rank IC  : N/A")
        print(f"  Mean Hit Rate : {hr:.4f}" if hr is not None else "  Mean Hit Rate : N/A")

    lift = results.get("onchain_marginal_ic_lift")
    if lift is not None:
        print(f"\nOn-chain marginal IC lift : {lift:+.4f}")
        print(f"On-chain features help    : {results.get('onchain_features_help')}")
    print("=" * 60 + "\n")
