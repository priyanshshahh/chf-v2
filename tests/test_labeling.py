"""
Offline synthetic tests for the advanced labeling + frac-diff machinery.

Pure/offline: constructs deterministic price paths with known barrier touches,
hand-computed volatility, and a controlled random walk — no network, no fixtures.
Run: ``.venv/bin/python -m pytest tests/test_labeling.py -q``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.labeling import (
    get_daily_vol,
    meta_labels,
    sample_weights_by_return,
    sample_weights_by_uniqueness,
    triple_barrier_labels,
)
from features.frac_diff import _ffd_weights, adf_pvalue, frac_diff_ffd, min_ffd


def _daily_index(n: int, start: str = "2021-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="D", tz="UTC")


# --------------------------------------------------------------------------- #
# get_daily_vol
# --------------------------------------------------------------------------- #
def test_get_daily_vol_matches_hand_computation():
    idx = _daily_index(6)
    close = pd.Series([100.0, 101.0, 102.0, 100.0, 103.0, 104.0], index=idx)
    span = 3

    vol = get_daily_vol(close, span=span)
    # Independent recomputation of the same definition.
    expected = close.pct_change().ewm(span=span).std()

    pd.testing.assert_series_equal(vol, expected, check_names=False)
    assert np.isnan(vol.iloc[0])  # first return undefined
    assert vol.index.equals(close.index)


def test_get_daily_vol_backward_only():
    # sigma at t must not change when future prices change (no look-ahead).
    idx = _daily_index(10)
    close = pd.Series(100.0 * (1.0 + 0.01 * np.arange(10)), index=idx)
    vol_full = get_daily_vol(close, span=5)

    truncated = close.iloc[:6].copy()
    vol_trunc = get_daily_vol(truncated, span=5)
    # Values up to t=5 identical regardless of later data.
    pd.testing.assert_series_equal(
        vol_full.iloc[:6], vol_trunc, check_names=False
    )


# --------------------------------------------------------------------------- #
# triple_barrier_labels
# --------------------------------------------------------------------------- #
def _fixed_target(index: pd.DatetimeIndex, value: float) -> pd.Series:
    return pd.Series(value, index=index, name="daily_vol")


def test_triple_barrier_upper_touch():
    # Rises steadily; with target=0.02 and pt=2 the upper barrier is +0.04.
    idx = _daily_index(10)
    close = pd.Series(
        [100, 101, 102, 103, 104, 105, 106, 107, 108, 109], index=idx, dtype=float
    )
    target = _fixed_target(idx, 0.02)
    events = [idx[0]]

    res = triple_barrier_labels(
        close, events, pt_sl=[2.0, 2.0], vertical_barrier_days=30, target=target
    )
    row = res.iloc[0]
    assert row["label"] == 1
    assert row["barrier_touched"] == "pt"
    # +0.04 barrier first reached at price 104 (ret 0.04) on day index 4.
    assert row["touch_time"] == idx[4]
    assert row["ret"] == pytest.approx(0.04)


def test_triple_barrier_lower_touch():
    idx = _daily_index(10)
    close = pd.Series(
        [100, 99, 98, 97, 96, 95, 94, 93, 92, 91], index=idx, dtype=float
    )
    target = _fixed_target(idx, 0.02)  # lower barrier at -0.04
    res = triple_barrier_labels(
        close, [idx[0]], pt_sl=[2.0, 2.0], vertical_barrier_days=30, target=target
    )
    row = res.iloc[0]
    assert row["label"] == -1
    assert row["barrier_touched"] == "sl"
    assert row["touch_time"] == idx[4]  # price 96 -> ret -0.04
    assert row["ret"] == pytest.approx(-0.04)


def test_triple_barrier_vertical_touch():
    # Flat-ish path never reaching +/-0.04; vertical barrier fires.
    idx = _daily_index(6)
    close = pd.Series([100, 100.5, 101, 100.5, 101, 101.5], index=idx, dtype=float)
    target = _fixed_target(idx, 0.02)  # barriers +/-0.04
    res = triple_barrier_labels(
        close, [idx[0]], pt_sl=[2.0, 2.0], vertical_barrier_days=5, target=target
    )
    row = res.iloc[0]
    assert row["label"] == 0
    assert row["barrier_touched"] == "vertical"
    assert row["touch_time"] == idx[5]
    assert row["ret"] == pytest.approx(0.015)


def test_triple_barrier_vertical_barrier_days_limits_window():
    # Upper would eventually be hit, but the vertical barrier truncates first.
    idx = _daily_index(10)
    close = pd.Series(
        [100, 100.5, 101, 108, 109, 110, 111, 112, 113, 114], index=idx, dtype=float
    )
    target = _fixed_target(idx, 0.02)  # +0.04 barrier
    # vertical at day 2 -> only sees 100, 100.5, 101 (max ret 0.01 < 0.04).
    res = triple_barrier_labels(
        close, [idx[0]], pt_sl=[2.0, 2.0], vertical_barrier_days=2, target=target
    )
    row = res.iloc[0]
    assert row["label"] == 0
    assert row["barrier_touched"] == "vertical"
    assert row["touch_time"] == idx[2]


def test_triple_barrier_one_sided_disables_stop():
    # sl multiplier 0 disables the lower barrier: a drop cannot trigger -1.
    idx = _daily_index(8)
    close = pd.Series([100, 95, 90, 92, 93, 94, 95, 96], index=idx, dtype=float)
    target = _fixed_target(idx, 0.02)
    res = triple_barrier_labels(
        close, [idx[0]], pt_sl=[2.0, 0.0], vertical_barrier_days=10, target=target
    )
    row = res.iloc[0]
    assert row["label"] == 0  # never reaches +0.04, and stop is disabled
    assert row["barrier_touched"] == "vertical"


def test_triple_barrier_multiple_events_and_index_name():
    idx = _daily_index(12)
    close = pd.Series(
        [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
        index=idx,
        dtype=float,
    )
    target = _fixed_target(idx, 0.02)
    events = [idx[0], idx[3]]
    res = triple_barrier_labels(
        close, events, pt_sl=[2.0, 2.0], vertical_barrier_days=30, target=target
    )
    assert list(res.index) == [idx[0], idx[3]]
    assert res.index.name == "date_ts"
    assert (res["label"] == 1).all()
    # t1 column present for downstream uniqueness weighting.
    assert "t1" in res.columns


def test_triple_barrier_event_missing_in_index_raises():
    idx = _daily_index(5)
    close = pd.Series(np.arange(100.0, 105.0), index=idx)
    bad_event = [pd.Timestamp("2030-01-01", tz="UTC")]
    with pytest.raises(KeyError):
        triple_barrier_labels(close, bad_event, pt_sl=1.0, vertical_barrier_days=3)


# --------------------------------------------------------------------------- #
# meta_labels
# --------------------------------------------------------------------------- #
def test_meta_labels_one_iff_side_matches_return_sign():
    idx = _daily_index(6)
    side = pd.Series([1, 1, -1, -1, 0, 1], index=idx)
    ret = pd.Series([0.05, -0.03, -0.04, 0.02, 0.10, 0.0], index=idx)
    meta = meta_labels(side, ret)

    # long & up -> 1; long & down -> 0; short & down -> 1; short & up -> 0;
    # flat -> 0; zero return -> 0.
    expected = pd.Series([1, 0, 1, 0, 0, 0], index=idx, name="meta_label")
    pd.testing.assert_series_equal(meta, expected, check_dtype=False)


def test_meta_labels_align_on_intersection():
    a = pd.Series([1, -1, 1], index=_daily_index(3))
    b = pd.Series([0.02, -0.02], index=_daily_index(2))  # shorter
    meta = meta_labels(a, b)
    assert len(meta) == 2
    assert (meta == 1).all()


# --------------------------------------------------------------------------- #
# sample_weights_by_return
# --------------------------------------------------------------------------- #
def test_sample_weights_by_return_proportional_and_normalized():
    idx = _daily_index(4)
    ret = pd.Series([0.01, -0.02, 0.03, -0.04], index=idx)
    w = sample_weights_by_return(ret, normalize="mean")

    # Mean-normalized weights sum to the sample count.
    assert w.sum() == pytest.approx(len(ret))
    # Proportional to |ret|: ratios preserved.
    assert w.iloc[3] / w.iloc[0] == pytest.approx(0.04 / 0.01)
    assert (w >= 0).all()


def test_sample_weights_by_return_sum_normalization():
    ret = pd.Series([0.01, 0.03], index=_daily_index(2))
    w = sample_weights_by_return(ret, normalize="sum")
    assert w.sum() == pytest.approx(1.0)


def test_sample_weights_by_return_all_zero_is_uniform():
    ret = pd.Series([0.0, 0.0, 0.0], index=_daily_index(3))
    w = sample_weights_by_return(ret, normalize="mean")
    assert w.sum() == pytest.approx(3.0)
    assert np.allclose(w.to_numpy(), 1.0)


# --------------------------------------------------------------------------- #
# sample_weights_by_uniqueness
# --------------------------------------------------------------------------- #
def test_sample_weights_uniqueness_downweights_overlap():
    idx = _daily_index(10)
    # Events A & B overlap heavily; event C is isolated.
    t1 = pd.Series(
        {
            idx[0]: idx[3],  # A: [0,3]
            idx[1]: idx[4],  # B: [1,4]  overlaps A
            idx[7]: idx[9],  # C: [7,9]  no overlap
        }
    )
    w = sample_weights_by_uniqueness(t1, normalize="none")
    # Isolated event has uniqueness 1.0; overlapping events strictly less.
    assert w.loc[idx[7]] == pytest.approx(1.0)
    assert w.loc[idx[0]] < 1.0
    assert w.loc[idx[1]] < 1.0


def test_sample_weights_uniqueness_no_overlap_all_unique():
    idx = _daily_index(12)
    t1 = pd.Series({idx[0]: idx[1], idx[4]: idx[5], idx[8]: idx[9]})
    w = sample_weights_by_uniqueness(t1, normalize="none")
    assert np.allclose(w.to_numpy(), 1.0)


def test_sample_weights_uniqueness_mean_normalized_sums_to_len():
    idx = _daily_index(10)
    t1 = pd.Series({idx[0]: idx[3], idx[1]: idx[4], idx[7]: idx[9]})
    w = sample_weights_by_uniqueness(t1, normalize="mean")
    assert w.sum() == pytest.approx(len(t1))


def test_uniqueness_matches_hand_computation():
    idx = _daily_index(6)
    # Two events fully overlapping the same 3 bars: concurrency 2 everywhere.
    t1 = pd.Series({idx[0]: idx[2], idx[0 + 0]: idx[2]})
    # Build distinct start times mapping onto same span.
    t1 = pd.Series({idx[0]: idx[2], idx[1]: idx[2]})
    # bar concurrency: day0 -> {A}=1 ; day1 -> {A,B}=2 ; day2 -> {A,B}=2
    # A span [0,2]: mean(1/1, 1/2, 1/2) = (1 + .5 + .5)/3 = 2/3
    # B span [1,2]: mean(1/2, 1/2) = 0.5
    w = sample_weights_by_uniqueness(t1, normalize="none")
    assert w.loc[idx[0]] == pytest.approx(2.0 / 3.0)
    assert w.loc[idx[1]] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# frac_diff_ffd / min_ffd
# --------------------------------------------------------------------------- #
def test_ffd_weights_d_zero_is_identity():
    w = _ffd_weights(0.0, thresh=1e-5)
    assert w.shape == (1,)
    assert w[0] == pytest.approx(1.0)


def test_ffd_weights_d_one_is_first_difference():
    w = _ffd_weights(1.0, thresh=1e-5)
    # d=1 -> weights [1, -1] (oldest-first), i.e. x_t - x_{t-1}.
    assert list(w) == pytest.approx([-1.0, 1.0])


def test_frac_diff_d_one_equals_first_difference():
    idx = _daily_index(20)
    series = pd.Series(np.linspace(1.0, 5.0, 20), index=idx, name="logp")
    ffd = frac_diff_ffd(series, d=1.0, thresh=1e-5)
    expected = series.diff().dropna()
    np.testing.assert_allclose(ffd.to_numpy(), expected.to_numpy(), atol=1e-10)


def test_min_ffd_random_walk_stationary_and_preserves_memory():
    rng = np.random.default_rng(0)
    n = 2000
    idx = _daily_index(n)
    # Log-price random walk (unit root -> non-stationary).
    log_price = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 100.0, index=idx, name="logp")

    # Original is non-stationary; ADF should fail to reject unit root.
    assert adf_pvalue(log_price) > 0.05

    d = min_ffd(log_price, thresh=1e-4)
    assert 0.0 < d <= 1.0

    ffd = frac_diff_ffd(log_price, d=d, thresh=1e-4).dropna()
    # Stationary at the found d.
    assert adf_pvalue(ffd) < 0.05

    # Memory preserved: FFD series still strongly correlated with the level.
    aligned = pd.concat(
        [log_price.rename("orig"), ffd.rename("ffd")], axis=1, join="inner"
    ).dropna()
    corr = aligned["orig"].corr(aligned["ffd"])
    assert abs(corr) > 0.3


def test_min_ffd_prefers_smaller_d_than_full_difference():
    rng = np.random.default_rng(11)
    n = 1500
    idx = _daily_index(n)
    log_price = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 50.0, index=idx, name="logp")
    d = min_ffd(log_price, thresh=1e-4)
    # Fractional differencing should achieve stationarity below full d=1.
    assert d < 1.0


def test_frac_diff_no_lookahead_prefix_stable():
    rng = np.random.default_rng(3)
    n = 300
    idx = _daily_index(n)
    series = pd.Series(np.cumsum(rng.normal(0, 1, n)) + 10.0, index=idx, name="logp")
    full = frac_diff_ffd(series, d=0.4, thresh=1e-4)
    prefix = frac_diff_ffd(series.iloc[:150], d=0.4, thresh=1e-4)
    common = full.index.intersection(prefix.index)
    # Truncating future data must not change already-computed values.
    np.testing.assert_allclose(
        full.loc[common].to_numpy(), prefix.loc[common].to_numpy(), atol=1e-10
    )
