"""Render SHAP attribution plots from the persisted ModelAgent SHAP artifacts.

Reporting only — never feeds back into modeling or alpha verification.

What exists (checked at runtime):
  - `data/predictions/feature_importance.parquet` persists the *aggregated*
    attribution: mean(|SHAP value|) per feature (`mean_abs_shap`, averaged
    across walk-forward folds, `shap_folds` column) alongside the native
    model `importance` — see agents/model_agent.py::_compute_shap_importance.
  - Raw per-row SHAP value arrays are NOT persisted anywhere (a beeswarm plot
    needs the full [n_samples x n_features] SHAP matrix). This script scans
    for raw arrays first; if none are found it renders a horizontal-bar
    summary of mean(|SHAP|) per (model, feature_set, horizon) group instead,
    and says so.

Outputs: artifacts/plots/shap/shap_mean_abs_<model>_<feature_set>_<Nd>.png
  (falls back to native importance, clearly labeled, for groups where
  `mean_abs_shap` is entirely null — e.g. models where shap was unavailable).

Idempotent: re-running overwrites the same PNGs.

Usage:
    .venv/bin/python reports/shap_plots.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEATURE_IMPORTANCE_PATH = PROJECT_ROOT / "data" / "predictions" / "feature_importance.parquet"
OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "plots" / "shap"

TOP_N = 25

# Single-series magnitude chart: one accessible hue, recessive grid, ink for text.
BAR_COLOR = "#4269D0"
FALLBACK_BAR_COLOR = "#6C757D"  # native-importance fallback rendered in neutral gray
INK = "#1F2937"
MUTED_INK = "#6B7280"
GRID = "#E5E7EB"


def _find_raw_shap_arrays() -> List[Path]:
    """Look for persisted raw SHAP matrices (needed for a true beeswarm)."""
    hits: List[Path] = []
    for base in (PROJECT_ROOT / "artifacts", PROJECT_ROOT / "data" / "predictions"):
        if base.is_dir():
            for pattern in ("**/*shap*.npy", "**/*shap*.npz", "**/shap_values*.parquet"):
                hits.extend(base.glob(pattern))
    return sorted(hits)


def _slug(value: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(value))


def main() -> int:
    try:
        import pandas as pd
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(f"[shap-plots] FATAL: pandas/matplotlib unavailable: {exc}")
        return 1

    if not FEATURE_IMPORTANCE_PATH.exists():
        print(f"[shap-plots] nothing to do: {FEATURE_IMPORTANCE_PATH} missing "
              f"(run the model stage first)")
        return 0

    raw_arrays = _find_raw_shap_arrays()
    if raw_arrays:
        print(f"[shap-plots] found {len(raw_arrays)} raw SHAP array file(s); "
              f"note: beeswarm rendering from raw arrays is not implemented — listing them:")
        for p in raw_arrays:
            print(f"  ? {p}")
    else:
        print("[shap-plots] no raw per-row SHAP arrays are persisted (ModelAgent stores only "
              "aggregated mean(|SHAP|) per feature) — rendering horizontal-bar summaries "
              "instead of beeswarm plots.")

    df = pd.read_parquet(FEATURE_IMPORTANCE_PATH)
    required = {"model_name", "feature_set", "horizon_days", "feature_name"}
    missing = required - set(df.columns)
    if missing:
        print(f"[shap-plots] FATAL: {FEATURE_IMPORTANCE_PATH} missing columns {sorted(missing)}")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated: List[str] = []
    skipped: List[str] = []

    for (model, fset, horizon), grp in df.groupby(["model_name", "feature_set", "horizon_days"]):
        label = f"{model} / {fset} / {int(horizon)}d"
        use_shap = "mean_abs_shap" in grp.columns and grp["mean_abs_shap"].notna().any()
        value_col = "mean_abs_shap" if use_shap else "importance"
        if value_col not in grp.columns or not grp[value_col].notna().any():
            skipped.append(f"{label}: no usable mean_abs_shap or importance values")
            continue

        data = (
            grp[["feature_name", value_col]]
            .dropna()
            .groupby("feature_name", as_index=False)[value_col]
            .mean()
            .sort_values(value_col, ascending=False)
            .head(TOP_N)
            .iloc[::-1]  # largest at top after barh
        )
        if data.empty:
            skipped.append(f"{label}: empty after filtering")
            continue

        n_folds = int(grp["shap_folds"].max()) if use_shap and "shap_folds" in grp.columns else 0
        if use_shap:
            subtitle = f"mean(|SHAP value|) per feature, averaged over {n_folds} walk-forward folds"
            xlabel = "mean(|SHAP value|)"
            color = BAR_COLOR
            kind = "shap"
        else:
            subtitle = "native model importance (SHAP values unavailable for this group)"
            xlabel = "native importance"
            color = FALLBACK_BAR_COLOR
            kind = "native_importance"

        fig_height = max(3.0, 0.32 * len(data) + 1.6)
        fig, ax = plt.subplots(figsize=(9, fig_height), dpi=150)
        ax.barh(data["feature_name"], data[value_col], color=color, height=0.62)
        ax.set_title(f"SHAP summary — {label}", fontsize=12, color=INK, loc="left", fontweight="bold", pad=22)
        ax.text(0, 1.004, subtitle, transform=ax.transAxes, fontsize=8.5, color=MUTED_INK, va="bottom")
        ax.set_xlabel(xlabel, fontsize=9, color=MUTED_INK)
        ax.tick_params(colors=INK, labelsize=8)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.margins(y=0.01)
        fig.tight_layout()

        out_path = OUTPUT_DIR / f"shap_mean_abs_{_slug(model)}_{_slug(fset)}_{int(horizon)}d.png"
        if kind == "native_importance":
            out_path = OUTPUT_DIR / f"native_importance_{_slug(model)}_{_slug(fset)}_{int(horizon)}d.png"
        try:
            fig.savefig(out_path)
            generated.append(f"{out_path} [{kind}, top {len(data)} of {grp['feature_name'].nunique()} features]")
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{label}: save failed ({exc})")
        finally:
            plt.close(fig)

    print(f"\n[shap-plots] generated {len(generated)} image(s):")
    for item in generated:
        print(f"  + {item}")
    if skipped:
        print(f"[shap-plots] skipped {len(skipped)}:")
        for note in skipped:
            print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
