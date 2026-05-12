"""
Research-grade LabelAgent for the canonical CHF pipeline.

Consumes:
- data/raw/market/market_ohlcv.parquet
- data/features/full_features.parquet
- data/features/full_features_pruned.parquet (optional)
- data/raw/market/market_manifest.json
- data/features/feature_manifest.json

Produces:
- labels_{horizon}d.parquet
- label_matrix.parquet
- modeling_dataset.parquet
- modeling_dataset_unpruned.parquet (optional)
- label_coverage_report.parquet
- label_manifest.json
- data_quality_labels.md
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from agents.base import AgentBase
from features.feature_engineering import ALLOWED_PROHIBITED_EXACT, ensure_utc


LABEL_LEAKAGE_TOKENS = (
    "target",
    "label",
    "future",
    "forward",
    "fwd",
    "lead",
    "next_return",
    "ret_fwd",
    "y_",
)

LABEL_METADATA_COLUMNS = {
    "date_ts",
    "symbol",
    "feature_set",
    "feature_version",
    "snapshot_id",
    "run_id",
    "created_at_utc",
    "onchain_lag_days",
}


class LabelAgentError(RuntimeError):
    """Raised when the canonical label generation contract is violated."""


@dataclass
class LabelHorizonResult:
    horizon: int
    labels: pd.DataFrame
    coverage: Dict[str, Any]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _is_utc_midnight(series: pd.Series) -> bool:
    dt = pd.to_datetime(series, utc=True, errors="coerce")
    if dt.isna().any():
        return False
    return bool(((dt.dt.hour == 0) & (dt.dt.minute == 0) & (dt.dt.second == 0)).all())


def _require_columns(df: pd.DataFrame, required: Iterable[str], name: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise LabelAgentError(f"{name} missing required columns: {missing}")


def _find_prohibited_columns(columns: Iterable[str]) -> List[str]:
    bad: List[str] = []
    for col in columns:
        if col in LABEL_METADATA_COLUMNS or col in ALLOWED_PROHIBITED_EXACT:
            continue
        lower = col.lower()
        prefix_y = lower.startswith("y_")
        token_hit = any(token in lower for token in LABEL_LEAKAGE_TOKENS if token != "y_")
        if prefix_y or token_hit:
            bad.append(col)
    return bad


def _quantile_bucket(series: pd.Series, buckets: int) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    ranks = valid.rank(method="average", pct=True)
    bucket = np.ceil(ranks * buckets).clip(1, buckets)
    out = pd.Series(np.nan, index=series.index, dtype="float64")
    out.loc[valid.index] = bucket
    return out


class LabelAgent(AgentBase):
    """Canonical, leakage-safe target generation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._project_root = Path(self.cfg["_project_root"])
        self._label_cfg = self.cfg.get("labels", {})
        self._market_df: Optional[pd.DataFrame] = None
        self._feature_df: Optional[pd.DataFrame] = None
        self._pruned_feature_df: Optional[pd.DataFrame] = None
        self._selected_symbols: List[str] = []
        self._input_files: Dict[str, str] = {}
        self._input_manifest_summaries: Dict[str, Any] = {}
        self._output_dir: Optional[Path] = None
        self._used_pruned_for_modeling = False

    @property
    def label_cfg(self) -> Dict[str, Any]:
        return self._label_cfg

    def prepare(self) -> None:
        self._label_cfg = self.cfg.get("labels", {})
        self._output_dir = _resolve_path(self._project_root, self._label_cfg.get("output_dir", "data/labels"))
        self._output_dir.mkdir(parents=True, exist_ok=True)

        market_path = _resolve_path(self._project_root, self._label_cfg["input_market_path"])
        feature_path = _resolve_path(self._project_root, self._label_cfg["input_features_path"])
        pruned_path = _resolve_path(self._project_root, self._label_cfg["input_pruned_features_path"])
        market_manifest_path = _resolve_path(self._project_root, self._label_cfg["input_market_manifest_path"])
        feature_manifest_path = _resolve_path(self._project_root, self._label_cfg["input_feature_manifest_path"])

        required_paths = {
            "market": market_path,
            "features": feature_path,
            "market_manifest": market_manifest_path,
            "feature_manifest": feature_manifest_path,
        }
        for name, path in required_paths.items():
            if not path.exists():
                raise FileNotFoundError(f"Required {name} input missing: {path}")
        self._input_files = {k: str(v) for k, v in required_paths.items()}
        if pruned_path.exists():
            self._input_files["pruned_features"] = str(pruned_path)

        self._market_df = pd.read_parquet(market_path)
        _require_columns(self._market_df, ["date_ts", "symbol", "close", "snapshot_id"], "market_ohlcv")
        self._market_df["date_ts"] = ensure_utc(self._market_df["date_ts"])
        self._market_df = self._market_df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)
        if self._market_df.duplicated(["symbol", "date_ts"]).any():
            raise LabelAgentError("market_ohlcv.parquet contains duplicate symbol + date_ts rows")

        self._market_df["close"] = pd.to_numeric(self._market_df["close"], errors="coerce")
        bad_close = self._market_df["close"].isna() | (self._market_df["close"] <= 0)
        if bad_close.any() and self._label_cfg.get("fail_on_non_positive_prices", True):
            sample = self._market_df.loc[bad_close, ["symbol", "date_ts", "close"]].head(10).to_dict("records")
            raise LabelAgentError(f"market_ohlcv contains non-positive/invalid close prices: {sample}")

        self._feature_df = pd.read_parquet(feature_path)
        _require_columns(self._feature_df, ["date_ts", "symbol", "snapshot_id", "run_id"], "full_features")
        self._feature_df["date_ts"] = ensure_utc(self._feature_df["date_ts"])
        self._feature_df = self._feature_df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)
        if self._feature_df.duplicated(["symbol", "date_ts"]).any():
            raise LabelAgentError("full_features.parquet contains duplicate symbol + date_ts rows")

        prohibited = _find_prohibited_columns(self._feature_df.columns)
        if prohibited and self._label_cfg.get("fail_on_target_leakage", True):
            raise LabelAgentError(f"Feature input contains prohibited columns: {prohibited}")

        if pruned_path.exists():
            self._pruned_feature_df = pd.read_parquet(pruned_path)
            _require_columns(self._pruned_feature_df, ["date_ts", "symbol", "snapshot_id", "run_id"], "full_features_pruned")
            self._pruned_feature_df["date_ts"] = ensure_utc(self._pruned_feature_df["date_ts"])
            self._pruned_feature_df = self._pruned_feature_df.sort_values(["symbol", "date_ts"]).reset_index(drop=True)
            if self._pruned_feature_df.duplicated(["symbol", "date_ts"]).any():
                raise LabelAgentError("full_features_pruned.parquet contains duplicate symbol + date_ts rows")
            prohibited_pruned = _find_prohibited_columns(self._pruned_feature_df.columns)
            if prohibited_pruned and self._label_cfg.get("fail_on_target_leakage", True):
                raise LabelAgentError(f"Pruned feature input contains prohibited columns: {prohibited_pruned}")

        with open(market_manifest_path, "r") as fh:
            self._input_manifest_summaries["market"] = json.load(fh)
        with open(feature_manifest_path, "r") as fh:
            self._input_manifest_summaries["features"] = json.load(fh)
        market_manifest_snapshot = self._input_manifest_summaries["market"].get("snapshot_id")
        if market_manifest_snapshot and "snapshot_id" in self._market_df.columns:
            market_snapshots = set(self._market_df["snapshot_id"].dropna().astype(str).unique())
            if market_snapshots and str(market_manifest_snapshot) not in market_snapshots:
                raise LabelAgentError("market snapshot_id does not match market manifest snapshot_id")
        feature_manifest_snapshot = self._input_manifest_summaries["features"].get("snapshot_id")
        if feature_manifest_snapshot and "snapshot_id" in self._feature_df.columns:
            feature_snapshots = set(self._feature_df["snapshot_id"].dropna().astype(str).unique())
            if feature_snapshots and str(feature_manifest_snapshot) not in feature_snapshots:
                raise LabelAgentError("feature snapshot_id does not match feature manifest snapshot_id")

        max_symbols = self._label_cfg.get("max_symbols")
        feature_symbols = (
            self._feature_df[["symbol"]]
            .drop_duplicates()
            .sort_values("symbol")
            .reset_index(drop=True)
        )
        selected = feature_symbols["symbol"].tolist()
        if max_symbols:
            selected = selected[: int(max_symbols)]
        self._selected_symbols = selected
        if not self._selected_symbols:
            raise LabelAgentError("No symbols available after canonical feature selection")

        self._market_df = self._market_df[self._market_df["symbol"].isin(self._selected_symbols)].copy()
        self._feature_df = self._feature_df[self._feature_df["symbol"].isin(self._selected_symbols)].copy()
        if self._pruned_feature_df is not None:
            self._pruned_feature_df = self._pruned_feature_df[self._pruned_feature_df["symbol"].isin(self._selected_symbols)].copy()

        self.logger.info(
            "LabelAgent prepared | symbols=%s | market_rows=%s | feature_rows=%s",
            len(self._selected_symbols),
            len(self._market_df),
            len(self._feature_df),
        )

    def run(self) -> Dict[str, Any]:
        horizons = [int(h) for h in self._label_cfg.get("horizons", [7, 14, 30])]
        drop_incomplete = bool(self._label_cfg.get("drop_incomplete_horizon_rows", True))
        self.generate_snapshot_id(f"labels:{','.join(map(str, horizons))}")

        horizon_results: List[LabelHorizonResult] = []
        for horizon in horizons:
            label_df, coverage = self._compute_horizon_labels(horizon, drop_incomplete)
            horizon_results.append(LabelHorizonResult(horizon=horizon, labels=label_df, coverage=coverage))

        label_matrix = self._build_label_matrix(horizon_results)
        modeling_dataset = self._build_modeling_dataset(label_matrix, use_pruned=True)
        modeling_dataset_unpruned = None
        if self._label_cfg.get("also_write_unpruned_modeling_dataset", True):
            modeling_dataset_unpruned = self._build_modeling_dataset(label_matrix, use_pruned=False)

        if self._label_cfg.get("fail_on_feature_label_misalignment", True):
            matrix_keys = set(map(tuple, label_matrix[["symbol", "date_ts"]].itertuples(index=False, name=None)))
            model_keys = set(map(tuple, modeling_dataset[["symbol", "date_ts"]].itertuples(index=False, name=None)))
            if matrix_keys != model_keys:
                raise LabelAgentError("Modeling dataset and label_matrix symbol/date keys are misaligned")

        coverage_report = pd.DataFrame([result.coverage for result in horizon_results]).sort_values("horizon_days").reset_index(drop=True)
        manifest = self._build_manifest(horizon_results, label_matrix, modeling_dataset, modeling_dataset_unpruned)

        self.metrics["label_matrix_rows"] = int(len(label_matrix))
        self.metrics["modeling_dataset_rows"] = int(len(modeling_dataset))
        self.metrics["modeling_dataset_symbols"] = int(modeling_dataset["symbol"].nunique()) if not modeling_dataset.empty else 0
        self.metrics["horizon_count"] = len(horizons)
        self.metrics["max_horizon_days"] = max(horizons) if horizons else 0
        for result in horizon_results:
            self.metrics[f"label_rows_h{result.horizon}"] = int(len(result.labels))
            self.metrics[f"dropped_incomplete_h{result.horizon}"] = int(result.coverage["dropped_incomplete_rows"])

        return {
            "horizons": {result.horizon: result.labels for result in horizon_results},
            "label_matrix": label_matrix,
            "modeling_dataset": modeling_dataset,
            "modeling_dataset_unpruned": modeling_dataset_unpruned,
            "coverage_report": coverage_report,
            "manifest": manifest,
            "data_quality_md": self._build_data_quality_md(horizon_results, label_matrix, modeling_dataset),
        }

    def _compute_horizon_labels(self, horizon: int, drop_incomplete: bool) -> tuple[pd.DataFrame, Dict[str, Any]]:
        frames: List[pd.DataFrame] = []
        total_candidates = 0
        dropped_bad_price = 0
        dropped_incomplete = 0
        dropped_non_exact = 0
        dropped_low_cross_section_rows = 0
        dropped_low_cross_section_dates = 0
        missing_feature_rows = 0
        feature_keys = set(map(tuple, self._feature_df[["symbol", "date_ts"]].itertuples(index=False, name=None)))

        for symbol, grp in self._market_df.groupby("symbol", sort=True):
            grp = grp.sort_values("date_ts").reset_index(drop=True).copy()
            total_candidates += len(grp)
            grp["close_t"] = pd.to_numeric(grp["close"], errors="coerce")
            grp["close_t_plus_h"] = grp["close_t"].shift(-horizon)
            grp["future_date_ts"] = grp["date_ts"].shift(-horizon)
            grp["expected_future_date_ts"] = grp["date_ts"] + pd.Timedelta(days=horizon)
            grp["is_exact_horizon"] = grp["future_date_ts"].eq(grp["expected_future_date_ts"])
            grp["horizon_days"] = horizon
            grp["label_fwd_logret"] = np.log(grp["close_t_plus_h"] / grp["close_t"])
            grp["label_simple_return"] = grp["close_t_plus_h"] / grp["close_t"] - 1.0
            grp["label_direction"] = (grp["label_fwd_logret"] > 0).astype("Int64")
            grp["is_complete"] = (
                grp["future_date_ts"].notna()
                & grp["close_t"].notna()
                & grp["close_t_plus_h"].notna()
                & (grp["close_t"] > 0)
                & (grp["close_t_plus_h"] > 0)
                & grp["is_exact_horizon"]
            )
            bad_price_mask = (
                grp["close_t"].isna()
                | grp["close_t_plus_h"].isna()
                | (grp["close_t"] <= 0)
                | (grp["close_t_plus_h"] <= 0)
            ) & grp["future_date_ts"].notna()
            dropped_bad_price += int(bad_price_mask.sum())
            if drop_incomplete:
                dropped_incomplete += int((~grp["is_complete"]).sum())
                dropped_non_exact += int((grp["future_date_ts"].notna() & ~grp["is_exact_horizon"]).sum())
                grp = grp[grp["is_complete"]].copy()

            grp = grp[[
                "date_ts",
                "symbol",
                "horizon_days",
                "future_date_ts",
                "close_t",
                "close_t_plus_h",
                "label_fwd_logret",
                "label_simple_return",
                "label_direction",
                "is_complete",
            ]]
            grp["label_value"] = grp["label_fwd_logret"]
            grp["label_type"] = self._label_cfg.get("label_type", "forward_log_return")
            grp["snapshot_id"] = self.snapshot_id
            grp["run_id"] = self.run_id
            grp["created_at_utc"] = _utcnow_iso()
            grp = grp.replace([np.inf, -np.inf], np.nan)
            grp = grp.dropna(subset=["label_fwd_logret", "label_simple_return", "future_date_ts", "close_t", "close_t_plus_h"])

            present_mask = grp[["symbol", "date_ts"]].apply(tuple, axis=1).isin(feature_keys)
            missing_feature_rows += int((~present_mask).sum())
            grp = grp[present_mask].copy()
            frames.append(grp)

        label_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not label_df.empty:
            label_df["date_ts"] = ensure_utc(label_df["date_ts"])
            label_df["future_date_ts"] = ensure_utc(label_df["future_date_ts"])
            if label_df.duplicated(["symbol", "date_ts"]).any():
                raise LabelAgentError(f"Duplicate symbol/date rows detected for horizon={horizon}")
            if (label_df["close_t"] <= 0).any() or (label_df["close_t_plus_h"] <= 0).any():
                raise LabelAgentError(f"Non-positive prices reached final labels for horizon={horizon}")
            if label_df["label_fwd_logret"].isna().any() or label_df["label_simple_return"].isna().any():
                raise LabelAgentError(f"Null labels reached final labels for horizon={horizon}")
            if not np.isfinite(label_df["label_fwd_logret"]).all() or not np.isfinite(label_df["label_simple_return"]).all():
                raise LabelAgentError(f"Infinite labels reached final labels for horizon={horizon}")
            label_df["label_rank_pct"] = label_df.groupby("date_ts")["label_fwd_logret"].rank(
                method=self._label_cfg.get("rank_method", "average"),
                pct=bool(self._label_cfg.get("rank_pct", True)),
            )
            buckets = int(self._label_cfg.get("quantile_buckets", 5))
            label_df["label_quantile_bucket"] = (
                label_df.groupby("date_ts", group_keys=False)["label_fwd_logret"].apply(lambda s: _quantile_bucket(s, buckets))
            ).astype("Int64")
            min_assets_per_date = int(self._label_cfg.get("min_assets_per_label_date", 20))
            if min_assets_per_date > 0:
                counts = label_df.groupby("date_ts")["symbol"].transform("nunique")
                low_mask = counts < min_assets_per_date
                dropped_low_cross_section_rows = int(low_mask.sum())
                dropped_low_cross_section_dates = int(label_df.loc[low_mask, "date_ts"].nunique())
                label_df = label_df.loc[~low_mask].copy()
            label_df = label_df.sort_values(["date_ts", "symbol"]).reset_index(drop=True)

        valid_rows = len(label_df)
        coverage = {
            "horizon_days": horizon,
            "total_candidate_rows": int(total_candidates),
            "valid_label_rows": int(valid_rows),
            "dropped_incomplete_rows": int(dropped_incomplete),
            "dropped_non_exact_horizon_rows": int(dropped_non_exact),
            "dropped_bad_price_rows": int(dropped_bad_price),
            "dropped_missing_feature_rows": int(missing_feature_rows),
            "dropped_low_cross_section_dates": int(dropped_low_cross_section_dates),
            "dropped_low_cross_section_rows": int(dropped_low_cross_section_rows),
            "symbols_with_labels": int(label_df["symbol"].nunique()) if not label_df.empty else 0,
            "first_label_date": label_df["date_ts"].min().isoformat() if not label_df.empty else None,
            "last_label_date": label_df["date_ts"].max().isoformat() if not label_df.empty else None,
            "first_future_date": label_df["future_date_ts"].min().isoformat() if not label_df.empty else None,
            "last_future_date": label_df["future_date_ts"].max().isoformat() if not label_df.empty else None,
            "null_label_count": int(label_df["label_fwd_logret"].isna().sum()) if not label_df.empty else 0,
            "infinite_label_count": int((~np.isfinite(label_df["label_fwd_logret"])).sum()) if not label_df.empty else 0,
            "non_finite_label_count": int((~np.isfinite(label_df["label_fwd_logret"])).sum()) if not label_df.empty else 0,
            "positive_label_count": int((label_df["label_fwd_logret"] > 0).sum()) if not label_df.empty else 0,
            "negative_label_count": int((label_df["label_fwd_logret"] < 0).sum()) if not label_df.empty else 0,
            "zero_label_count": int((label_df["label_fwd_logret"] == 0).sum()) if not label_df.empty else 0,
            "mean_label": float(label_df["label_fwd_logret"].mean()) if not label_df.empty else None,
            "std_label": float(label_df["label_fwd_logret"].std()) if not label_df.empty else None,
            "min_label": float(label_df["label_fwd_logret"].min()) if not label_df.empty else None,
            "p01_label": float(label_df["label_fwd_logret"].quantile(0.01)) if not label_df.empty else None,
            "p50_label": float(label_df["label_fwd_logret"].quantile(0.50)) if not label_df.empty else None,
            "p99_label": float(label_df["label_fwd_logret"].quantile(0.99)) if not label_df.empty else None,
            "max_label": float(label_df["label_fwd_logret"].max()) if not label_df.empty else None,
            "passed_qa": bool(valid_rows >= int(self._label_cfg.get("min_rows_per_horizon_required", 1))),
            "failure_reason": None,
        }
        if valid_rows < int(self._label_cfg.get("min_rows_per_horizon_required", 1)):
            coverage["failure_reason"] = "below_min_rows_per_horizon_required"
        return label_df, coverage

    def _build_label_matrix(self, results: List[LabelHorizonResult]) -> pd.DataFrame:
        if not results:
            return pd.DataFrame()
        matrix = None
        horizon_cols = []
        for result in results:
            h = result.horizon
            df = result.labels[[
                "date_ts",
                "symbol",
                "label_fwd_logret",
                "label_simple_return",
                "label_direction",
                "label_rank_pct",
                "label_quantile_bucket",
            ]].copy()
            rename_map = {
                "label_fwd_logret": f"label_fwd_logret_{h}d",
                "label_simple_return": f"label_simple_return_{h}d",
                "label_direction": f"label_direction_{h}d",
                "label_rank_pct": f"label_rank_pct_{h}d",
                "label_quantile_bucket": f"label_quantile_bucket_{h}d",
            }
            horizon_cols.extend(rename_map.values())
            df = df.rename(columns=rename_map)
            matrix = df if matrix is None else matrix.merge(df, on=["date_ts", "symbol"], how="inner")
        if matrix is None:
            return pd.DataFrame()
        matrix["max_horizon_complete"] = True
        matrix["snapshot_id"] = self.snapshot_id
        matrix["run_id"] = self.run_id
        matrix["created_at_utc"] = _utcnow_iso()
        matrix = matrix.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        if matrix.duplicated(["symbol", "date_ts"]).any():
            raise LabelAgentError("label_matrix contains duplicate symbol/date rows")
        if matrix[horizon_cols].isna().any().any():
            raise LabelAgentError("label_matrix contains null label values after all-horizon alignment")
        return matrix

    def _build_modeling_dataset(self, label_matrix: pd.DataFrame, *, use_pruned: bool) -> pd.DataFrame:
        if label_matrix.empty:
            return pd.DataFrame()
        feature_df = self._pruned_feature_df if use_pruned and self._pruned_feature_df is not None and self._label_cfg.get("use_pruned_features_for_modeling_dataset", True) else self._feature_df
        if use_pruned and feature_df is self._pruned_feature_df:
            self._used_pruned_for_modeling = True
        merged = feature_df.merge(label_matrix, on=["date_ts", "symbol"], how="inner", suffixes=("", "_label"))
        merged = merged.sort_values(["date_ts", "symbol"]).reset_index(drop=True)
        label_cols = [col for col in label_matrix.columns if col.startswith("label_")] + ["max_horizon_complete"]
        required = [f"label_fwd_logret_{int(h)}d" for h in self._label_cfg.get("horizons", [7, 14, 30])]
        merged = merged.dropna(subset=required)
        if merged.duplicated(["symbol", "date_ts"]).any():
            raise LabelAgentError("modeling_dataset contains duplicate symbol/date rows")
        prohibited = _find_prohibited_columns([c for c in feature_df.columns if c not in LABEL_METADATA_COLUMNS])
        if prohibited and self._label_cfg.get("fail_on_target_leakage", True):
            raise LabelAgentError(f"Feature columns still contain prohibited names before modeling join: {prohibited}")
        metadata = ["date_ts", "symbol", "snapshot_id", "run_id", "created_at_utc"]
        present_labels = [col for col in label_cols if col in merged.columns]
        numeric = merged.select_dtypes(include=["number", "bool"]).columns.tolist()
        ordered = metadata + [col for col in merged.columns if col not in metadata]
        merged = merged[ordered]
        merged[numeric] = merged[numeric].replace([np.inf, -np.inf], np.nan)
        if self._label_cfg.get("fail_on_infinite_labels", True):
            label_numeric = [col for col in present_labels if col in merged.columns and pd.api.types.is_numeric_dtype(merged[col])]
            if label_numeric and (~np.isfinite(merged[label_numeric])).any().any():
                raise LabelAgentError("modeling_dataset contains infinite label values")
        return merged

    def _build_manifest(
        self,
        results: List[LabelHorizonResult],
        label_matrix: pd.DataFrame,
        modeling_dataset: pd.DataFrame,
        modeling_dataset_unpruned: Optional[pd.DataFrame],
    ) -> Dict[str, Any]:
        market_rows = int(len(self._market_df))
        feature_rows = int(len(self._feature_df))
        pruned_rows = int(len(self._pruned_feature_df)) if self._pruned_feature_df is not None else 0
        first_label_date = label_matrix["date_ts"].min().isoformat() if not label_matrix.empty else None
        last_label_date = label_matrix["date_ts"].max().isoformat() if not label_matrix.empty else None
        horizons = [int(r.horizon) for r in results]
        output_dir = self._output_dir
        manifest = {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "created_at_utc": _utcnow_iso(),
            "input_files": self._input_files,
            "input_manifest_summaries": self._input_manifest_summaries,
            "output_files": {
                **{f"labels_{r.horizon}d": str(output_dir / f"labels_{r.horizon}d.parquet") for r in results},
                "label_matrix": str(output_dir / "label_matrix.parquet"),
                "modeling_dataset": str(output_dir / "modeling_dataset.parquet"),
                "modeling_dataset_unpruned": str(output_dir / "modeling_dataset_unpruned.parquet") if modeling_dataset_unpruned is not None else None,
                "label_coverage_report": str(output_dir / "label_coverage_report.parquet"),
                "label_manifest": str(output_dir / "label_manifest.json"),
                "data_quality_report": str(output_dir / "data_quality_labels.md"),
                "partitioned": str(output_dir / "partitioned"),
            },
            "horizons": horizons,
            "label_type": self._label_cfg.get("label_type", "forward_log_return"),
            "formula": "label_fwd_logret_h(t) = ln(close(t+h) / close(t))",
            "market_rows": market_rows,
            "feature_rows": feature_rows,
            "pruned_feature_rows": pruned_rows,
            "label_rows_by_horizon": {str(r.horizon): int(len(r.labels)) for r in results},
            "label_matrix_rows": int(len(label_matrix)),
            "modeling_dataset_rows": int(len(modeling_dataset)),
            "modeling_dataset_unpruned_rows": int(len(modeling_dataset_unpruned)) if modeling_dataset_unpruned is not None else 0,
            "symbols_by_horizon": {str(r.horizon): int(r.labels["symbol"].nunique()) if not r.labels.empty else 0 for r in results},
            "label_matrix_symbols": int(label_matrix["symbol"].nunique()) if not label_matrix.empty else 0,
            "modeling_dataset_symbols": int(modeling_dataset["symbol"].nunique()) if not modeling_dataset.empty else 0,
            "first_label_date": first_label_date,
            "last_label_date": last_label_date,
            "max_horizon_days": int(max(horizons)) if horizons else 0,
            "recommended_embargo_days": int(self._label_cfg.get("recommended_embargo_days", max(horizons) if horizons else 0)),
            "purge_train_test_overlap_days": int(self._label_cfg.get("purge_train_test_overlap_days", max(horizons) if horizons else 0)),
            "drop_incomplete_horizon_rows": bool(self._label_cfg.get("drop_incomplete_horizon_rows", True)),
            "min_assets_per_label_date": int(self._label_cfg.get("min_assets_per_label_date", 20)),
            "data_hashes": {
                "input_market": _sha256_file(Path(self._input_files["market"])),
                "input_features": _sha256_file(Path(self._input_files["features"])),
                "input_pruned_features": _sha256_file(Path(self._input_files["pruned_features"])) if "pruned_features" in self._input_files else None,
                "config_hash": _sha256_payload(copy.deepcopy(self._label_cfg)),
            },
            "config_hash": _sha256_payload(copy.deepcopy(self._label_cfg)),
            "warnings": [],
            "limitations": ["Purged walk-forward CV required; no random shuffling permitted."],
        }
        return manifest

    def _build_data_quality_md(
        self,
        results: List[LabelHorizonResult],
        label_matrix: pd.DataFrame,
        modeling_dataset: pd.DataFrame,
    ) -> str:
        failures = [str(r.coverage.get("failure_reason")) for r in results if r.coverage.get("failure_reason")]
        status = "FAIL" if failures or label_matrix.empty or modeling_dataset.empty else "PASS"
        lines = [
            "# Data Quality Labels",
            "",
            f"- Market rows loaded: {len(self._market_df)}",
            f"- Feature rows loaded: {len(self._feature_df)}",
            f"- Label matrix rows: {len(label_matrix)}",
            f"- Modeling dataset rows: {len(modeling_dataset)}",
            f"- Symbols selected: {len(self._selected_symbols)}",
            f"- Recommended embargo days: {self._label_cfg.get('recommended_embargo_days', max(self._label_cfg.get('horizons', [7, 14, 30])))}",
            "",
            "## Horizon Coverage",
        ]
        for result in results:
            cov = result.coverage
            lines.append(
                f"- {result.horizon}d: valid={cov['valid_label_rows']}, "
                f"incomplete_dropped={cov['dropped_incomplete_rows']}, "
                f"bad_price_dropped={cov['dropped_bad_price_rows']}, "
                f"missing_feature_dropped={cov['dropped_missing_feature_rows']}"
            )
        lines.extend(
            [
                "",
                "## Leakage Guard Summary",
                "- Features were validated for prohibited label/target/future tokens before any join.",
                "- Labels were generated from future completed closes and aligned back to feature date_t.",
                "- The final modeling dataset uses an inner join on symbol + date_ts only.",
                "",
                "## Final Status",
                f"- {status}",
                f"- Failure reasons: {failures if failures else 'None'}",
                "",
            ]
        )
        return "\n".join(lines)

    def persist(self, result: Dict[str, Any]) -> None:
        out_dir = self._output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        horizons: Dict[int, pd.DataFrame] = result["horizons"]
        label_matrix: pd.DataFrame = result["label_matrix"]
        modeling_dataset: pd.DataFrame = result["modeling_dataset"]
        modeling_dataset_unpruned: Optional[pd.DataFrame] = result["modeling_dataset_unpruned"]
        coverage_report: pd.DataFrame = result["coverage_report"]
        manifest: Dict[str, Any] = result["manifest"]

        if self._label_cfg.get("fail_on_empty_output", True):
            if label_matrix.empty or modeling_dataset.empty:
                raise LabelAgentError("LabelAgent produced empty canonical outputs")
        if self._label_cfg.get("fail_on_low_label_coverage", True):
            self._final_coverage_checks(horizons, label_matrix, modeling_dataset)

        for horizon, df in horizons.items():
            if df.empty:
                raise LabelAgentError(f"labels_{horizon}d.parquet would be empty")
            path = out_dir / f"labels_{horizon}d.parquet"
            df.to_parquet(path, index=False)
            self.output_paths[f"labels_{horizon}d"] = str(path)

        label_matrix_path = out_dir / "label_matrix.parquet"
        label_matrix.to_parquet(label_matrix_path, index=False)
        self.output_paths["label_matrix"] = str(label_matrix_path)

        modeling_path = out_dir / "modeling_dataset.parquet"
        modeling_dataset.to_parquet(modeling_path, index=False)
        self.output_paths["modeling_dataset"] = str(modeling_path)

        if modeling_dataset_unpruned is not None:
            unpruned_path = out_dir / "modeling_dataset_unpruned.parquet"
            modeling_dataset_unpruned.to_parquet(unpruned_path, index=False)
            self.output_paths["modeling_dataset_unpruned"] = str(unpruned_path)

        coverage_path = out_dir / "label_coverage_report.parquet"
        coverage_report.to_parquet(coverage_path, index=False)
        self.output_paths["label_coverage_report"] = str(coverage_path)

        quality_path = out_dir / "data_quality_labels.md"
        quality_path.write_text(result["data_quality_md"])
        self.output_paths["data_quality_labels"] = str(quality_path)

        partition_root = out_dir / "partitioned"
        if self._label_cfg.get("output_partitioned", True):
            for horizon, df in horizons.items():
                part_df = df.copy()
                part_df["year"] = pd.to_datetime(part_df["date_ts"], utc=True).dt.year
                part_df["month"] = pd.to_datetime(part_df["date_ts"], utc=True).dt.month
                for (year, month), grp in part_df.groupby(["year", "month"]):
                    part_dir = partition_root / f"horizon={horizon}" / f"year={year}" / f"month={int(month):02d}"
                    part_dir.mkdir(parents=True, exist_ok=True)
                    grp.drop(columns=["year", "month"]).to_parquet(part_dir / "part.parquet", index=False)
            self.output_paths["partitioned"] = str(partition_root)

        manifest["data_hashes"]["label_matrix"] = _sha256_file(label_matrix_path)
        manifest["data_hashes"]["modeling_dataset"] = _sha256_file(modeling_path)
        if modeling_dataset_unpruned is not None:
            manifest["data_hashes"]["modeling_dataset_unpruned"] = _sha256_file(out_dir / "modeling_dataset_unpruned.parquet")

        manifest_path = out_dir / "label_manifest.json"
        with open(manifest_path, "w") as fh:
            json.dump(manifest, fh, indent=2)
        self.output_paths["label_manifest"] = str(manifest_path)

    def _final_coverage_checks(
        self,
        horizons: Dict[int, pd.DataFrame],
        label_matrix: pd.DataFrame,
        modeling_dataset: pd.DataFrame,
    ) -> None:
        min_symbols = int(self._label_cfg.get("min_symbols_required", 1))
        min_label_rows = int(self._label_cfg.get("min_label_rows_required", 1))
        min_rows_per_h = int(self._label_cfg.get("min_rows_per_horizon_required", 1))
        min_common = int(self._label_cfg.get("min_common_rows_all_horizons", 1))

        if label_matrix["symbol"].nunique() < min_symbols:
            raise LabelAgentError("Label matrix symbol coverage below configured minimum")
        if len(label_matrix) < min_common:
            raise LabelAgentError("Label matrix row coverage below configured minimum")
        if len(modeling_dataset) < min_label_rows:
            raise LabelAgentError("Modeling dataset row coverage below configured minimum")
        for horizon, df in horizons.items():
            if len(df) < min_rows_per_h:
                raise LabelAgentError(f"labels_{horizon}d coverage below configured minimum")

    def load_labels(self, horizon: int) -> pd.DataFrame:
        path = self._output_dir / f"labels_{horizon}d.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()
