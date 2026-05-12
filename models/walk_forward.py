from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Generator, Iterable, List

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


@dataclass
class WalkForwardSplit:
    fold_id: int
    horizon_days: int
    train_start: pd.Timestamp
    train_end_raw: pd.Timestamp
    train_end_purged: pd.Timestamp
    embargo_start: pd.Timestamp
    embargo_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_rows: int
    test_rows: int
    train_symbols: int
    test_symbols: int
    purge_days: int
    embargo_days: int


def _coerce_utc_dates(df: pd.DataFrame, date_col: str) -> pd.Series:
    return pd.to_datetime(df[date_col], utc=True, errors="coerce").dt.normalize()


def generate_purged_walk_forward_splits(
    df: pd.DataFrame,
    *,
    date_col: str = "date_ts",
    symbol_col: str = "symbol",
    horizon_days: int = 7,
    initial_train_days: int = 504,
    test_days: int = 30,
    step_days: int = 30,
    purge_days: int | None = None,
    embargo_days: int = 30,
    min_train_rows: int = 1000,
    min_test_rows: int = 100,
    min_test_symbols: int = 10,
) -> Generator[WalkForwardSplit, None, None]:
    if df.empty:
        return
    purge_days = int(purge_days if purge_days is not None else horizon_days)
    data = df.copy()
    data[date_col] = _coerce_utc_dates(data, date_col)
    data = data.dropna(subset=[date_col]).sort_values([date_col, symbol_col]).reset_index(drop=True)
    unique_dates = data[date_col].drop_duplicates().sort_values().tolist()
    if len(unique_dates) < initial_train_days + test_days:
        return

    fold_id = 0
    test_start_pos = initial_train_days + embargo_days
    while test_start_pos + test_days - 1 < len(unique_dates):
        test_start = unique_dates[test_start_pos]
        test_end = unique_dates[min(test_start_pos + test_days - 1, len(unique_dates) - 1)]
        raw_train_end_pos = test_start_pos - embargo_days - 1
        if raw_train_end_pos < 0:
            break
        train_end_raw = unique_dates[raw_train_end_pos]
        train_end_purged = min(train_end_raw, test_start - pd.Timedelta(days=purge_days + 1))
        embargo_start = train_end_purged + pd.Timedelta(days=1)
        embargo_end = test_start - pd.Timedelta(days=1)
        train_mask = data[date_col] <= train_end_purged
        test_mask = (data[date_col] >= test_start) & (data[date_col] <= test_end)
        train_idx = np.flatnonzero(train_mask.to_numpy())
        test_idx = np.flatnonzero(test_mask.to_numpy())
        if len(train_idx) >= min_train_rows and len(test_idx) >= min_test_rows:
            train_symbols = int(data.iloc[train_idx][symbol_col].nunique())
            test_symbols = int(data.iloc[test_idx][symbol_col].nunique())
            if test_symbols >= min_test_symbols:
                yield WalkForwardSplit(
                    fold_id=fold_id,
                    horizon_days=int(horizon_days),
                    train_start=unique_dates[0],
                    train_end_raw=train_end_raw,
                    train_end_purged=train_end_purged,
                    embargo_start=embargo_start,
                    embargo_end=embargo_end,
                    test_start=test_start,
                    test_end=test_end,
                    train_idx=train_idx,
                    test_idx=test_idx,
                    train_rows=len(train_idx),
                    test_rows=len(test_idx),
                    train_symbols=train_symbols,
                    test_symbols=test_symbols,
                    purge_days=purge_days,
                    embargo_days=embargo_days,
                )
                fold_id += 1
        test_start_pos += step_days


def rank_ic_by_date(predictions: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if predictions.empty:
        return pd.DataFrame(columns=["date_ts", "rank_ic", "n_symbols"])
    for date_ts, grp in predictions.groupby("date_ts"):
        valid = grp[["prediction", "actual_forward_return"]].dropna()
        if len(valid) < 3:
            rows.append({"date_ts": date_ts, "rank_ic": np.nan, "n_symbols": len(valid)})
            continue
        corr, _ = spearmanr(valid["prediction"], valid["actual_forward_return"], nan_policy="omit")
        rows.append({"date_ts": date_ts, "rank_ic": float(corr) if pd.notna(corr) else np.nan, "n_symbols": len(valid)})
    return pd.DataFrame(rows)


def compute_topk_metrics(predictions: pd.DataFrame) -> Dict[str, float]:
    metrics = {
        "top_5_mean_actual_return": np.nan,
        "top_10_mean_actual_return": np.nan,
        "top_20_mean_actual_return": np.nan,
        "bottom_10_mean_actual_return": np.nan,
        "top_bottom_10_spread": np.nan,
        "top_10_hit_rate": np.nan,
    }
    if predictions.empty:
        return metrics
    grouped = predictions.groupby("date_ts", sort=True)
    top5 = []
    top10 = []
    top20 = []
    bottom10 = []
    hit10 = []
    for _, grp in grouped:
        grp = grp.sort_values("prediction", ascending=False)
        if grp.empty:
            continue
        actual = pd.to_numeric(grp["actual_forward_return"], errors="coerce")
        median_actual = actual.median()
        top5_grp = grp.head(5)
        top10_grp = grp.head(10)
        top20_grp = grp.head(20)
        bot10_grp = grp.tail(10)
        top5.append(pd.to_numeric(top5_grp["actual_forward_return"], errors="coerce").mean())
        top10_vals = pd.to_numeric(top10_grp["actual_forward_return"], errors="coerce")
        top10.append(top10_vals.mean())
        top20.append(pd.to_numeric(top20_grp["actual_forward_return"], errors="coerce").mean())
        bottom10.append(pd.to_numeric(bot10_grp["actual_forward_return"], errors="coerce").mean())
        hit10.append(float(((top10_vals > 0) | (top10_vals > median_actual)).mean()))
    if top5:
        metrics.update(
            {
                "top_5_mean_actual_return": float(np.nanmean(top5)),
                "top_10_mean_actual_return": float(np.nanmean(top10)),
                "top_20_mean_actual_return": float(np.nanmean(top20)),
                "bottom_10_mean_actual_return": float(np.nanmean(bottom10)),
                "top_bottom_10_spread": float(np.nanmean(top10) - np.nanmean(bottom10)),
                "top_10_hit_rate": float(np.nanmean(hit10)),
            }
        )
    return metrics


def compute_error_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    if valid.sum() == 0:
        return {"rmse": np.nan, "mae": np.nan, "r2": np.nan}
    yt = y_true[valid]
    yp = y_pred[valid]
    rmse = float(np.sqrt(np.mean((yt - yp) ** 2)))
    mae = float(np.mean(np.abs(yt - yp)))
    denom = np.sum((yt - np.mean(yt)) ** 2)
    r2 = float(1 - np.sum((yt - yp) ** 2) / denom) if denom > 0 else np.nan
    return {"rmse": rmse, "mae": mae, "r2": r2}


def summarize_predictions(predictions: pd.DataFrame, *, n_features: int) -> Dict[str, Any]:
    ic_by_date = rank_ic_by_date(predictions)
    ic_values = pd.to_numeric(ic_by_date["rank_ic"], errors="coerce").dropna()
    rank_ic_mean = float(ic_values.mean()) if not ic_values.empty else np.nan
    rank_ic_std = float(ic_values.std()) if len(ic_values) > 1 else np.nan
    rank_ic_tstat = float(rank_ic_mean / (rank_ic_std / np.sqrt(len(ic_values)))) if len(ic_values) > 1 and rank_ic_std and rank_ic_std > 0 else np.nan
    rank_ic_hit_rate = float((ic_values > 0).mean()) if not ic_values.empty else np.nan
    topk = compute_topk_metrics(predictions)
    errors = compute_error_metrics(
        pd.to_numeric(predictions["actual_forward_return"], errors="coerce").to_numpy(),
        pd.to_numeric(predictions["prediction"], errors="coerce").to_numpy(),
    )
    return {
        "fold_count": int(predictions["fold_id"].nunique()) if "fold_id" in predictions.columns else 0,
        "test_date_count": int(predictions["date_ts"].nunique()) if "date_ts" in predictions.columns else 0,
        "test_symbol_count": int(predictions["symbol"].nunique()) if "symbol" in predictions.columns else 0,
        "prediction_rows": int(len(predictions)),
        "prediction_coverage": float(predictions["prediction"].notna().mean()) if "prediction" in predictions.columns and len(predictions) else 0.0,
        "rank_ic_mean": rank_ic_mean,
        "rank_ic_std": rank_ic_std,
        "rank_ic_tstat": rank_ic_tstat,
        "rank_ic_hit_rate": rank_ic_hit_rate,
        "n_features": int(n_features),
        **topk,
        **errors,
    }


# Backward-compatible names for older imports.
walk_forward_splits = generate_purged_walk_forward_splits
rank_ic = rank_ic_by_date


def compute_fold_metrics(*args, **kwargs):
    raise NotImplementedError("Use rank_ic_by_date / summarize_predictions in the rebuilt research pipeline.")


def aggregate_fold_metrics(*args, **kwargs):
    raise NotImplementedError("Use summarize_predictions in the rebuilt research pipeline.")


class WalkForwardValidator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def split(self, X: pd.DataFrame) -> Generator[tuple[np.ndarray, np.ndarray], None, None]:
        for split in generate_purged_walk_forward_splits(X, **self.kwargs):
            yield split.train_idx, split.test_idx
