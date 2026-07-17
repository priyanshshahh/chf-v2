"""
Advanced labeling machinery for the CHF research pipeline.

Implements the triple-barrier method, meta-labeling and sample-weighting
schemes from Marcos Lopez de Prado, *Advances in Financial Machine Learning*
(2018), chapters 3-4. These are STANDALONE, leakage-safe pure functions with a
clean panel-friendly API; they are intentionally *not* wired into
``agents/label_agent.py`` or ``agents/model_agent.py`` (a later integration
agent does that — see ``docs/LABELING.md`` for the wiring plan).

Conventions borrowed from the canonical pipeline
(``agents/label_agent.py`` / ``models/walk_forward.py``):
    * Panels are keyed on ``date_ts`` (tz-aware, UTC) + ``symbol``; here the
      per-instrument primitives take a price ``pd.Series`` indexed by a
      ``DatetimeIndex`` so they compose with either a single symbol slice or a
      ``groupby("symbol")`` loop.
    * Realized volatility is a *daily* quantity. Crypto trades 365 days/year,
      so annualize a daily vol with ``sigma_daily * sqrt(365)`` (the same
      sqrt(365) convention used elsewhere in the repo). The functions here
      return daily quantities and never annualize implicitly.

No-look-ahead guarantee
-----------------------
Every label for an event at time ``t0`` is a function of the forward price path
``prices.loc[t0 : t0 + vertical_barrier]`` *only*. The barrier levels are scaled
by volatility observed at or before ``t0`` (EWMA up to and including ``t0``), and
the realized return is measured from ``prices.loc[t0]``. No value dated strictly
before the event leaks forward and no value dated after the first-touch time
leaks backward. Each public function restates this guarantee in its docstring.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd


PriceLike = Union[pd.Series, pd.DataFrame]
EventsLike = Union[pd.DatetimeIndex, pd.Index, Sequence[Any], pd.Series, pd.DataFrame]


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #
def _as_price_series(prices: PriceLike, *, price_col: str = "close") -> pd.Series:
    """Coerce ``prices`` to a clean, sorted, tz-aware price Series.

    Accepts a Series (used directly) or a DataFrame (``price_col`` extracted,
    defaulting to ``close`` to match ``market_ohlcv`` conventions).
    """
    if isinstance(prices, pd.DataFrame):
        if price_col not in prices.columns:
            raise ValueError(f"prices DataFrame missing '{price_col}' column")
        series = prices[price_col]
    elif isinstance(prices, pd.Series):
        series = prices
    else:
        raise TypeError("prices must be a pandas Series or DataFrame")

    series = pd.to_numeric(series, errors="coerce")
    idx = pd.to_datetime(series.index, utc=True, errors="coerce")
    series = pd.Series(series.to_numpy(), index=idx, name=series.name)
    series = series[~series.index.isna()]
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series


def _as_event_index(events: EventsLike) -> pd.DatetimeIndex:
    """Coerce a variety of ``events`` inputs to a sorted, unique DatetimeIndex.

    ``Series``/``DataFrame`` contribute their *index* (event start times); an
    ``Index``/list/array contributes its values.
    """
    if isinstance(events, (pd.Series, pd.DataFrame)):
        raw = events.index
    else:
        raw = events
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(raw), utc=True, errors="coerce"))
    idx = idx[~idx.isna()]
    return idx.drop_duplicates().sort_values()


def _coerce_pt_sl(pt_sl: Union[float, Sequence[float]]) -> Tuple[float, float]:
    """Return (profit-take multiplier, stop-loss multiplier).

    A scalar applies symmetrically. A multiplier of ``0`` disables that
    horizontal barrier (Lopez de Prado convention).
    """
    if np.isscalar(pt_sl):
        pt = sl = float(pt_sl)  # type: ignore[arg-type]
    else:
        seq = list(pt_sl)  # type: ignore[arg-type]
        if len(seq) != 2:
            raise ValueError("pt_sl must be a scalar or a length-2 [pt, sl] sequence")
        pt, sl = float(seq[0]), float(seq[1])
    if pt < 0 or sl < 0:
        raise ValueError("pt_sl multipliers must be non-negative")
    return pt, sl


# --------------------------------------------------------------------------- #
# Volatility (barrier scaler)
# --------------------------------------------------------------------------- #
def get_daily_vol(close: pd.Series, span: int = 100) -> pd.Series:
    """EWMA daily-return volatility — the horizontal-barrier scaler.

    Computes simple daily returns ``close_t / close_{t-1} - 1`` then an
    exponentially-weighted moving standard deviation with the given ``span``
    (Lopez de Prado, AFML §3.1). The result is a *daily* sigma; multiply by
    ``sqrt(365)`` for a crypto-annualized figure.

    Parameters
    ----------
    close : pd.Series
        Price series indexed by a (tz-aware) DatetimeIndex, one instrument.
    span : int
        EWMA span (center of mass ``= (span - 1) / 2``). Default 100.

    Returns
    -------
    pd.Series
        EWMA daily volatility aligned to ``close.index``. The first value is
        NaN (no prior return); early values reflect the EWMA warm-up.

    No-look-ahead guarantee
    -----------------------
    ``sigma_t`` uses only returns dated ``<= t`` (pandas ``ewm`` is strictly
    backward-looking), so it is safe to read at an event time ``t0`` to scale
    that event's forward barriers.
    """
    close = _as_price_series(close)
    if span < 1:
        raise ValueError("span must be >= 1")
    returns = close.pct_change()
    vol = returns.ewm(span=span).std()
    vol.name = "daily_vol"
    return vol


# --------------------------------------------------------------------------- #
# Triple-barrier method
# --------------------------------------------------------------------------- #
def triple_barrier_labels(
    prices: PriceLike,
    events: EventsLike,
    pt_sl: Union[float, Sequence[float]],
    vertical_barrier_days: int,
    vol_lookback: int = 100,
    *,
    target: Optional[pd.Series] = None,
    price_col: str = "close",
) -> pd.DataFrame:
    """Triple-barrier labels for a single instrument (Lopez de Prado, AFML §3.2-3.4).

    For each event time ``t0`` this places three barriers on the forward price
    path and labels by whichever is touched first:

    * **Upper (take-profit)** horizontal barrier at return ``+pt * sigma_t0``.
    * **Lower (stop-loss)** horizontal barrier at return ``-sl * sigma_t0``.
    * **Vertical (time)** barrier at ``t0 + vertical_barrier_days``.

    where ``sigma_t0`` is the rolling realized daily volatility observed at
    ``t0`` (see :func:`get_daily_vol`) and ``pt``, ``sl`` are the ``pt_sl``
    multipliers. Returns are measured from the event-time price
    ``prices.loc[t0]``.

    Parameters
    ----------
    prices : pd.Series | pd.DataFrame
        Forward-looking price path for one instrument (Series, or DataFrame
        with ``price_col``), indexed by a DatetimeIndex.
    events : DatetimeIndex | list | Series | DataFrame
        Event start times. A Series/DataFrame contributes its index. Every
        event must be present in ``prices.index``.
    pt_sl : float | (float, float)
        Barrier width multipliers ``[pt, sl]``. A scalar is symmetric; a ``0``
        disables that horizontal barrier (pure vertical / one-sided labeling).
    vertical_barrier_days : int
        Calendar-day width of the time barrier. ``<= 0`` disables it (the label
        then requires a horizontal touch, else stays 0 at the path end).
    vol_lookback : int
        EWMA span for the internally-computed volatility target. Default 100.
    target : pd.Series, optional
        Pre-computed per-index daily volatility to use as the barrier scaler.
        When ``None`` (default) it is computed via ``get_daily_vol(prices,
        vol_lookback)``. Supplying it lets callers reuse one vol estimate across
        many event sets (and makes barrier levels exactly reproducible in tests).
    price_col : str
        Column to use when ``prices`` is a DataFrame. Default ``"close"``.

    Returns
    -------
    pd.DataFrame
        Indexed by event time ``t0`` (name ``date_ts``) with columns:

        * ``label`` — ``+1`` upper first, ``-1`` lower first, ``0`` vertical
          first (or no touch). ``Int64``.
        * ``touch_time`` — timestamp of the first barrier touch.
        * ``ret`` — realized simple return ``prices[touch]/prices[t0] - 1``.
        * ``barrier_touched`` — ``"pt"`` | ``"sl"`` | ``"vertical"``.

        Additional bookkeeping columns ``t1`` (vertical-barrier time) and
        ``target`` (the sigma used) are included for downstream weighting.

    No-look-ahead guarantee
    -----------------------
    Each event's label depends only on ``prices.loc[t0 : t0 +
    vertical_barrier_days]`` and on ``sigma_t0`` (EWMA vol using returns dated
    ``<= t0``). Nothing before ``t0`` and nothing after the first-touch time
    influences the label, so labels are safe to align back onto features dated
    at ``t0``.
    """
    price_series = _as_price_series(prices, price_col=price_col)
    event_idx = _as_event_index(events)
    pt, sl = _coerce_pt_sl(pt_sl)

    if target is None:
        target = get_daily_vol(price_series, span=vol_lookback)
    target = _as_price_series(target)
    target = target.reindex(price_series.index).ffill()

    vb = pd.Timedelta(days=int(vertical_barrier_days)) if vertical_barrier_days and vertical_barrier_days > 0 else None

    records = []
    for t0 in event_idx:
        if t0 not in price_series.index:
            raise KeyError(f"event {t0} not found in prices index")
        p0 = price_series.loc[t0]
        trgt = target.get(t0, np.nan)
        t1 = (t0 + vb) if vb is not None else price_series.index[-1]

        # Forward path over [t0, t1]; return measured from the event-time price.
        path = price_series.loc[t0:t1]
        rets = path / p0 - 1.0

        up = pt * trgt if (pt > 0 and np.isfinite(trgt)) else np.nan
        dn = -sl * trgt if (sl > 0 and np.isfinite(trgt)) else np.nan

        label = 0
        barrier = "vertical"
        # Vertical-barrier fallback: last observed point on the path.
        touch_time = rets.index[-1]
        ret = float(rets.iloc[-1])

        # Scan strictly *after* t0 for the first horizontal touch.
        for ts, r in rets.iloc[1:].items():
            hit_up = np.isfinite(up) and r >= up
            hit_dn = np.isfinite(dn) and r <= dn
            if hit_up or hit_dn:
                # If both cross on the same bar, the take-profit is credited.
                if hit_up:
                    label, barrier = 1, "pt"
                else:
                    label, barrier = -1, "sl"
                touch_time = ts
                ret = float(r)
                break

        records.append(
            {
                "date_ts": t0,
                "label": label,
                "touch_time": touch_time,
                "ret": ret,
                "barrier_touched": barrier,
                "t1": t1,
                "target": float(trgt) if np.isfinite(trgt) else np.nan,
            }
        )

    out = pd.DataFrame.from_records(records)
    if out.empty:
        out = pd.DataFrame(
            columns=["date_ts", "label", "touch_time", "ret", "barrier_touched", "t1", "target"]
        )
    out = out.set_index("date_ts")
    out["label"] = out["label"].astype("Int64")
    return out


# --------------------------------------------------------------------------- #
# Meta-labeling
# --------------------------------------------------------------------------- #
def meta_labels(
    primary_signal: pd.Series,
    triple_barrier_ret: pd.Series,
) -> pd.Series:
    """Meta-labels for the secondary (filter) model (Lopez de Prado, AFML §3.6).

    Given a primary model's *side* per event in ``{-1, 0, +1}`` and the realized
    triple-barrier return, produce a binary label for a secondary model that
    decides whether to *act* on the primary call:

        * ``1`` — the primary side was correct (the position it implied made
          money): ``sign(primary) == sign(ret)`` and both are non-zero, i.e.
          ``primary * ret > 0``.
        * ``0`` — otherwise (wrong side, a flat primary signal, or a zero
          realized return): *don't act*.

    The secondary model is then trained to predict this {0,1} label from
    features, learning *when to trust* the primary model. This decouples side
    (primary) from size/precision (secondary) and typically lifts precision and
    F1 versus a single-stage classifier.

    Parameters
    ----------
    primary_signal : pd.Series
        Primary model side in ``{-1, 0, +1}``, indexed by event time.
    triple_barrier_ret : pd.Series
        Realized return per event (e.g. the ``ret`` column returned by
        :func:`triple_barrier_labels`), indexed by event time.

    Returns
    -------
    pd.Series
        ``{0, 1}`` meta-labels (``int64``) aligned to the intersection of the
        two inputs' indices, named ``meta_label``.

    No-look-ahead guarantee
    -----------------------
    A pure function of already-realized quantities (the primary side known at
    ``t0`` and the triple-barrier return, itself leakage-safe). It introduces no
    new look-ahead beyond that already present in its inputs.
    """
    side = pd.to_numeric(primary_signal, errors="coerce")
    ret = pd.to_numeric(triple_barrier_ret, errors="coerce")
    joined = pd.concat([side.rename("side"), ret.rename("ret")], axis=1, join="inner")
    position_ret = joined["side"] * joined["ret"]
    meta = (position_ret > 0).astype("int64")
    meta.name = "meta_label"
    return meta


# --------------------------------------------------------------------------- #
# Sample weights
# --------------------------------------------------------------------------- #
def _normalize_weights(weights: pd.Series, how: str) -> pd.Series:
    """Normalize a weight vector. ``"mean"`` -> sum == len (avg weight 1);
    ``"sum"`` -> sum == 1; ``"none"`` -> raw."""
    weights = weights.astype(float)
    if how == "none":
        return weights
    total = float(weights.sum())
    if total <= 0 or not np.isfinite(total):
        # Degenerate (all-zero) -> uniform.
        n = len(weights)
        if n == 0:
            return weights
        uniform = 1.0 if how == "mean" else 1.0 / n
        return pd.Series(uniform, index=weights.index, name=weights.name)
    if how == "mean":
        return weights / total * len(weights)
    if how == "sum":
        return weights / total
    raise ValueError("normalize must be one of {'mean', 'sum', 'none'}")


def sample_weights_by_return(
    triple_barrier_ret: pd.Series,
    *,
    normalize: str = "mean",
) -> pd.Series:
    """Weight samples by the magnitude of their realized return (AFML §4.3).

    Observations tied to larger absolute returns carry more information about
    the label, so they are up-weighted by ``|ret|``. (This is the simplified,
    per-event form of the return-attribution weight; combine multiplicatively
    with :func:`sample_weights_by_uniqueness` for the full AFML weighting.)

    Parameters
    ----------
    triple_barrier_ret : pd.Series
        Realized per-event return (the ``ret`` column from
        :func:`triple_barrier_labels`), indexed by event time.
    normalize : {"mean", "sum", "none"}
        ``"mean"`` (default) scales weights to sum to ``len`` (mean 1, the
        scikit-learn ``sample_weight`` convention); ``"sum"`` scales to sum to
        1; ``"none"`` returns raw ``|ret|``.

    Returns
    -------
    pd.Series
        Non-negative sample weights named ``sample_weight``.

    No-look-ahead guarantee
    -----------------------
    A pure function of already-realized returns; introduces no look-ahead.
    """
    ret = pd.to_numeric(triple_barrier_ret, errors="coerce").fillna(0.0)
    raw = ret.abs()
    weights = _normalize_weights(raw, normalize)
    weights.name = "sample_weight"
    return weights


def sample_weights_by_uniqueness(
    t1: pd.Series,
    *,
    close_index: Optional[pd.DatetimeIndex] = None,
    normalize: str = "mean",
) -> pd.Series:
    """Concurrency-based *average uniqueness* sample weights (AFML §4.3).

    Overlapping labels (whose ``[t0, t1]`` spans cover shared bars) are not
    independent draws; naive training over-counts crowded periods. Each event's
    weight is its **average uniqueness**: the mean over its lifespan of
    ``1 / c_t`` where ``c_t`` is the number of events concurrently active at
    time ``t``. Heavily-overlapping events are down-weighted.

    Parameters
    ----------
    t1 : pd.Series
        Event end times: indexed by event start time ``t0`` with values equal to
        the event's vertical-barrier / first-touch time ``t1``. This is exactly
        the ``t1`` (or ``touch_time``) column from :func:`triple_barrier_labels`.
    close_index : pd.DatetimeIndex, optional
        Bar grid over which concurrency is counted. Defaults to the sorted union
        of all ``t0`` and ``t1`` timestamps (sufficient for exact average
        uniqueness on a piecewise-constant concurrency curve).
    normalize : {"mean", "sum", "none"}
        See :func:`sample_weights_by_return`. Default ``"mean"``.

    Returns
    -------
    pd.Series
        Sample weights indexed by event start time, named ``sample_weight``.

    No-look-ahead guarantee
    -----------------------
    Uniqueness is a property of the (already-determined) label spans, computed
    symmetrically over their lifetimes; it is a re-weighting of the training
    set, not a feature, and adds no forward-looking information.
    """
    t1 = t1.dropna()
    t1 = pd.Series(
        pd.to_datetime(t1.to_numpy(), utc=True, errors="coerce"),
        index=pd.to_datetime(t1.index, utc=True, errors="coerce"),
        name="t1",
    ).dropna().sort_index()

    if t1.empty:
        return pd.Series(dtype=float, name="sample_weight")

    if close_index is None:
        grid = pd.DatetimeIndex(t1.index).union(pd.DatetimeIndex(t1.to_numpy()))
    else:
        grid = pd.DatetimeIndex(pd.to_datetime(close_index, utc=True)).sort_values()
    grid = grid.drop_duplicates().sort_values()

    # Concurrency c_t: number of events active on each grid bar.
    count = pd.Series(0.0, index=grid)
    for t0, t1_end in t1.items():
        count.loc[t0:t1_end] += 1.0

    # Average uniqueness per event = mean of 1/c_t over its span.
    weights = pd.Series(index=t1.index, dtype=float, name="sample_weight")
    inv_count = 1.0 / count.replace(0.0, np.nan)
    for t0, t1_end in t1.items():
        span = inv_count.loc[t0:t1_end]
        weights.loc[t0] = float(span.mean()) if len(span) else np.nan

    weights = weights.fillna(0.0)
    weights = _normalize_weights(weights, normalize)
    weights.name = "sample_weight"
    return weights
