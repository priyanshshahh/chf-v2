"""
CHF Walk-Forward Validation
Implements purged, embargoed walk-forward expanding-window cross-validation.
No naive K-fold leakage: validation sets are always strictly after training sets.

Validation Design:
─────────────────
- Expanding window: train on all data up to cutoff, validate on next step
- Purge: remove samples within embargo_days of the train/test boundary
- Embargo: additional buffer to prevent leakage from overlapping label windows
- Step: advance by step_months each fold
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class WalkForwardSplit:
    """A single train/validation split."""
    fold_id: int
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    train_idx: np.ndarray
    val_idx: np.ndarray


def walk_forward_splits(
    df: pd.DataFrame,
    date_col: str = "date_ts",
    initial_train_months: int = 12,
    step_months: int = 1,
    embargo_days: int = 7,
    horizon_days: int = 7,
) -> Generator[WalkForwardSplit, None, None]:
    """
    Generate walk-forward expanding-window splits.

    Parameters
    ----------
    df : DataFrame with date_col column
    initial_train_months : minimum months of training data
    step_months : months to advance each fold
    embargo_days : days to purge around train/test boundary
    horizon_days : label horizon (used to set minimum purge window)

    Yields
    ------
    WalkForwardSplit objects
    """
    dates = pd.to_datetime(df[date_col]).sort_values()
    min_date = dates.min()
    max_date = dates.max()

    # Initial train end
    train_end = min_date + pd.DateOffset(months=initial_train_months)

    # Total embargo = embargo_days + horizon_days (label overlap)
    total_embargo = timedelta(days=embargo_days + horizon_days)

    fold_id = 0
    while True:
        val_start = train_end + total_embargo
        val_end = val_start + pd.DateOffset(months=step_months)

        if val_end > max_date:
            break

        train_mask = (
            pd.to_datetime(df[date_col]) <= train_end
        )
        val_mask = (
            (pd.to_datetime(df[date_col]) > val_start) &
            (pd.to_datetime(df[date_col]) <= val_end)
        )

        train_idx = np.where(train_mask)[0]
        val_idx = np.where(val_mask)[0]

        if len(train_idx) > 0 and len(val_idx) > 0:
            yield WalkForwardSplit(
                fold_id=fold_id,
                train_start=min_date,
                train_end=train_end.to_pydatetime(),
                val_start=val_start.to_pydatetime(),
                val_end=val_end.to_pydatetime(),
                train_idx=train_idx,
                val_idx=val_idx,
            )
            fold_id += 1

        train_end = train_end + pd.DateOffset(months=step_months)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation Metrics
# ─────────────────────────────────────────────────────────────────────────────

def rank_ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Rank Information Coefficient (Rank IC).
    Spearman correlation between predicted and realized cross-sectional returns.
    IC > 0.05 is considered meaningful in practice.
    """
    if len(y_true) < 5:
        return np.nan
    corr, _ = stats.spearmanr(y_pred, y_true, nan_policy="omit")
    return float(corr) if not np.isnan(corr) else 0.0


def hit_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Hit rate: fraction of predictions with correct sign.
    """
    if len(y_true) < 2:
        return np.nan
    correct = np.sign(y_pred) == np.sign(y_true)
    return float(correct.mean())


def compute_fold_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    fold_id: int,
    model_name: str,
    horizon: int,
) -> Dict[str, Any]:
    """Compute all evaluation metrics for a single fold."""
    from sklearn.metrics import r2_score

    ic = rank_ic(y_true, y_pred)
    hr = hit_rate(y_true, y_pred)
    r2 = r2_score(y_true, y_pred) if len(y_true) > 2 else np.nan

    return {
        "fold_id": fold_id,
        "model_name": model_name,
        "horizon_days": horizon,
        "rank_ic": ic,
        "hit_rate": hr,
        "r2": r2,
        "n_samples": len(y_true),
        "y_pred_mean": float(np.mean(y_pred)),
        "y_pred_std": float(np.std(y_pred)),
        "y_true_mean": float(np.mean(y_true)),
        "y_true_std": float(np.std(y_true)),
    }


def aggregate_fold_metrics(fold_metrics: List[Dict]) -> Dict[str, float]:
    """Aggregate metrics across folds."""
    if not fold_metrics:
        return {}

    df = pd.DataFrame(fold_metrics)
    numeric_cols = ["rank_ic", "hit_rate", "r2"]

    agg = {}
    for col in numeric_cols:
        if col in df.columns:
            agg[f"{col}_mean"] = float(df[col].mean())
            agg[f"{col}_std"] = float(df[col].std())
            agg[f"{col}_median"] = float(df[col].median())
            agg[f"ic_t_stat"] = float(
                df["rank_ic"].mean() / (df["rank_ic"].std() / np.sqrt(len(df)) + 1e-10)
            ) if col == "rank_ic" else agg.get("ic_t_stat", np.nan)

    agg["n_folds"] = len(fold_metrics)
    return agg

# Alias for backward compatibility
WalkForwardValidator = WalkForwardSplit

# Alias for backward compatibility
WalkForwardValidator = WalkForwardSplit


class WalkForwardValidator:
    """
    Walk-forward cross-validator with purged + embargoed expanding window.

    Provides a scikit-learn-compatible split() interface.

    Parameters
    ----------
    n_splits : int
        Number of folds.
    embargo_days : int
        Days to embargo between train end and test start (prevents leakage).
    test_size_days : int
        Number of days in each test fold.
    """

    def __init__(
        self,
        n_splits: int = 5,
        embargo_days: int = 7,
        test_size_days: int = 90,
    ):
        self.n_splits = n_splits
        self.embargo_days = embargo_days
        self.test_size_days = test_size_days

    def split(self, X: "pd.DataFrame") -> "Generator":
        """
        Yield (train_indices, test_indices) for each fold.

        Uses expanding training window. Test windows are non-overlapping
        and placed at the end of the dataset, separated by embargo gaps.
        """
        import pandas as pd
        import numpy as np

        n = len(X)
        if n < self.n_splits * (self.test_size_days + self.embargo_days) + 30:
            # Not enough  yield a single folddata 
            split_point = int(n * 0.7)
            yield np.arange(split_point), np.arange(split_point, n)
            return

        # Build test windows from the end of the dataset
        test_windows = []
        test_end = n
        for _ in range(self.n_splits):
            test_start = test_end - self.test_size_days
            if test_start <= 30:
                break
            test_windows.append((test_start, test_end))
            test_end = test_start - self.embargo_days

        test_windows = list(reversed(test_windows))

        for test_start, test_end in test_windows:
            train_end = test_start - self.embargo_days
            if train_end <= 0:
                continue
            train_idx = np.arange(train_end)
            test_idx = np.arange(test_start, test_end)
            if len(train_idx) < 10 or len(test_idx) < 5:
                continue
            yield train_idx, test_idx
