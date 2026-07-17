"""
Fractional differentiation for the CHF research pipeline.

Fixed-width-window fractional differentiation (Lopez de Prado, *Advances in
Financial Machine Learning* §5) makes a price/level series stationary while
preserving as much *memory* (predictive, long-range structure) as possible.
Integer differencing (e.g. simple returns, ``d = 1``) achieves stationarity but
erases nearly all memory; fractional ``d`` in ``(0, 1)`` finds the minimum
differencing that passes a stationarity test, keeping the series both
ML-friendly (stationary) and informative (memory-rich).

Standalone, leakage-safe pure functions — not wired into any agent yet (see
``docs/LABELING.md``). Panel usage: apply per instrument
(``groupby("symbol")``) on a price series indexed by a tz-aware DatetimeIndex.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def _ffd_weights(d: float, thresh: float) -> np.ndarray:
    """Fixed-width-window fractional-difference weights (AFML §5.4).

    Generates the binomial weight sequence
        ``w_0 = 1``, ``w_k = -w_{k-1} * (d - k + 1) / k``
    and truncates once ``|w_k| < thresh``. Returned oldest-weight-first so it
    can be dotted directly against a chronological price window.
    """
    if thresh <= 0:
        raise ValueError("thresh must be > 0")
    w = [1.0]
    k = 1
    while True:
        w_ = -w[-1] / k * (d - k + 1)
        if abs(w_) < thresh:
            break
        w.append(w_)
        k += 1
    return np.array(w[::-1], dtype=float)


def frac_diff_ffd(series: pd.Series, d: float, thresh: float = 1e-5) -> pd.Series:
    """Fixed-width-window fractionally-differentiated series (AFML §5.4).

    Convolves the series with the truncated fractional-difference weights over a
    *fixed* window (constant width once ``|w_k| < thresh``), which — unlike the
    expanding-window variant — yields a driftless, uniformly-weighted transform.

    Parameters
    ----------
    series : pd.Series
        Input level series (typically log-price), indexed by a DatetimeIndex.
        Forward-filled internally before differencing; the result starts once a
        full window is available.
    d : float
        Fractional differencing order, ``>= 0``. ``0`` is a (near) identity;
        ``1`` reproduces first differences.
    thresh : float
        Weight-magnitude cutoff controlling window width. Default ``1e-5``.

    Returns
    -------
    pd.Series
        Fractionally-differentiated series aligned to the valid tail of the
        input index (the first ``width`` observations are consumed as the warm-up
        window), named ``"<name>_ffd"``.

    No-look-ahead guarantee
    -----------------------
    ``ffd_t`` is a backward convolution: ``sum_k w_k * x_{t-k}`` over the fixed
    window ending at ``t``. It uses only values dated ``<= t`` and so is safe as
    a feature transform aligned at ``t``.
    """
    if d < 0:
        raise ValueError("d must be >= 0")
    if not isinstance(series, pd.Series):
        raise TypeError("series must be a pandas Series")

    name = series.name if series.name is not None else "value"
    work = pd.to_numeric(series, errors="coerce").ffill().dropna()

    weights = _ffd_weights(d, thresh)
    width = len(weights) - 1

    values = work.to_numpy(dtype=float)
    index = work.index
    n = len(values)
    if n <= width:
        return pd.Series(dtype=float, name=f"{name}_ffd")

    out_vals = np.full(n - width, np.nan, dtype=float)
    for i in range(width, n):
        window = values[i - width : i + 1]
        out_vals[i - width] = float(np.dot(weights, window))

    result = pd.Series(out_vals, index=index[width:], name=f"{name}_ffd")
    return result


def min_ffd(
    series: pd.Series,
    thresh: float = 1e-5,
    d_range: Tuple[float, float] = (0.0, 1.0),
    step: float = 0.05,
    adf_pvalue: float = 0.05,
) -> float:
    """Minimum ``d`` whose FFD series passes the ADF stationarity test (AFML §5.5).

    Sweeps ``d`` from ``d_range[0]`` to ``d_range[1]`` in increments of ``step``,
    fractionally differentiates the series at each ``d`` (:func:`frac_diff_ffd`),
    and runs the Augmented Dickey-Fuller test (``statsmodels.tsa.stattools
    .adfuller``). Returns the smallest ``d`` whose ADF p-value is ``<
    adf_pvalue`` — i.e. the least differencing that rejects the unit-root null
    and thus retains the most memory.

    Parameters
    ----------
    series : pd.Series
        Input level series (typically log-price).
    thresh : float
        Weight cutoff forwarded to :func:`frac_diff_ffd`. Default ``1e-5``.
    d_range : (float, float)
        Inclusive ``(d_min, d_max)`` search bounds. Default ``(0.0, 1.0)``.
    step : float
        Search increment. Default ``0.05``.
    adf_pvalue : float
        Significance level for stationarity. Default ``0.05``.

    Returns
    -------
    float
        The minimum ``d`` on the grid that yields a stationary series. If no ``d``
        on the grid passes, returns ``d_range[1]`` (the maximally-differenced,
        most-likely-stationary candidate).

    Notes
    -----
    Requires ``statsmodels``. Install with
    ``.venv/bin/python -m pip install statsmodels`` if missing.
    """
    try:
        from statsmodels.tsa.stattools import adfuller
    except ImportError as exc:  # pragma: no cover - exercised only without statsmodels
        raise ImportError(
            "min_ffd requires statsmodels; install with "
            "'.venv/bin/python -m pip install statsmodels'"
        ) from exc

    d_min, d_max = float(d_range[0]), float(d_range[1])
    n_steps = int(round((d_max - d_min) / step))
    grid = [round(d_min + i * step, 10) for i in range(n_steps + 1)]

    for d in grid:
        diff = frac_diff_ffd(series, d, thresh=thresh).dropna()
        if len(diff) < 20 or diff.nunique() <= 1:
            continue
        pval = adfuller(diff.to_numpy(), regression="c", autolag="AIC")[1]
        if pval < adf_pvalue:
            return float(d)
    return float(d_max)


def adf_pvalue(series: pd.Series) -> float:
    """Convenience: Augmented Dickey-Fuller p-value for a series.

    Small helper used in tests/diagnostics, matching :func:`min_ffd`'s test
    (constant term, AIC lag selection). A p-value below the chosen significance
    level rejects the unit-root null (the series is stationary).
    """
    from statsmodels.tsa.stattools import adfuller

    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(adfuller(clean.to_numpy(), regression="c", autolag="AIC")[1])
