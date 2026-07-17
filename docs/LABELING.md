# Advanced Labeling & Feature-Transform Machinery

Standalone, leakage-safe implementations of the highest-leverage labeling and
feature-transform methods from Marcos Lopez de Prado, *Advances in Financial
Machine Learning* (AFML, 2018). These are **pure functions** with a clean,
panel-friendly API. They are intentionally **not yet wired** into `LabelAgent`
or `ModelAgent`; a later integration agent will consume them (wiring sketch
below).

- `features/labeling.py` — triple-barrier method, meta-labeling, sample weights.
- `features/frac_diff.py` — fixed-width fractional differentiation + `min_ffd`.
- `tests/test_labeling.py` — offline synthetic tests (24, all green).

Conventions match the canonical pipeline (`agents/label_agent.py`,
`models/walk_forward.py`): panels keyed on tz-aware `date_ts` + `symbol`; the
per-instrument primitives take a price `pd.Series` on a `DatetimeIndex` so they
compose with a `groupby("symbol")` loop. Volatility is a **daily** quantity —
annualize for crypto with `sigma_daily * sqrt(365)` (the same `sqrt(365)`
convention used elsewhere in the repo).

---

## 1. Why these beat fixed-horizon return labels

The current `LabelAgent` uses **fixed-horizon** labels:
`label_fwd_logret_h(t) = ln(close(t+h) / close(t))` (and its sign/rank/quantile
derivatives). That is simple and leakage-safe, but has well-known weaknesses:

| Problem with fixed-horizon labels | Fix provided here |
|---|---|
| Ignores the **path** — a +5% that first drew down −20% is labeled identically to a smooth +5%. | **Triple-barrier**: labels by which of {take-profit, stop-loss, time} is hit *first*, so path/risk is encoded. |
| **Fixed** barrier width ignores regime — the same ±x% means different things in calm vs. volatile markets. | Barriers are **scaled by rolling realized volatility** (`get_daily_vol`), so a "move" is measured in sigmas. |
| Forces a single model to learn **both** direction *and* confidence. | **Meta-labeling**: a primary model picks the side; a secondary model learns *when to act*, lifting precision/F1 and enabling position sizing. |
| Treats every observation as an **equal, independent** draw. | **Sample weights** by realized return (information content) and by **uniqueness** (down-weight overlapping labels). |
| Prices are **non-stationary** (unit root) — ML models generalize poorly on them; naive differencing (returns) erases memory. | **Fractional differentiation** (`frac_diff_ffd` + `min_ffd`): minimum differencing that is stationary *and* memory-preserving. |

---

## 2. API reference

### `features/labeling.py`

```python
get_daily_vol(close: pd.Series, span: int = 100) -> pd.Series
```
EWMA daily-return volatility — the horizontal-barrier scaler. Backward-looking
(`ewm` uses returns dated `<= t`).

```python
triple_barrier_labels(
    prices, events, pt_sl, vertical_barrier_days, vol_lookback=100,
    *, target=None, price_col="close",
) -> pd.DataFrame   # index=event t0 (name "date_ts")
# columns: label {+1,-1,0}, touch_time, ret, barrier_touched {"pt","sl","vertical"}, t1, target
```
Upper barrier at `+pt*sigma_t0`, lower at `-sl*sigma_t0`, vertical at
`t0 + vertical_barrier_days`. Label = sign of the first barrier touched (vertical
→ 0). A `pt_sl` component of `0` disables that horizontal barrier (one-sided).
Pass `target=` a pre-computed vol Series to reuse one estimate / make barriers
exactly reproducible.

```python
meta_labels(primary_signal: pd.Series, triple_barrier_ret: pd.Series) -> pd.Series
# {0,1}, name "meta_label"
```
`1` iff the primary side was profitable (`primary * ret > 0`), else `0`
("don't act"). Aligned on the index intersection.

```python
sample_weights_by_return(triple_barrier_ret, *, normalize="mean") -> pd.Series
sample_weights_by_uniqueness(t1, *, close_index=None, normalize="mean") -> pd.Series
```
Return-magnitude weights (`|ret|`) and concurrency-based **average-uniqueness**
weights (down-weight overlapping `[t0, t1]` spans). `normalize`: `"mean"`
(sum → N, mean 1, the sklearn `sample_weight` convention), `"sum"` (sum → 1), or
`"none"` (raw). Multiply the two element-wise for the full AFML weighting.

### `features/frac_diff.py`

```python
frac_diff_ffd(series: pd.Series, d: float, thresh: float = 1e-5) -> pd.Series
min_ffd(series, thresh=1e-5, d_range=(0.0, 1.0), step=0.05, adf_pvalue=0.05) -> float
adf_pvalue(series: pd.Series) -> float   # ADF p-value helper (constant, AIC lag)
```
`frac_diff_ffd` is a fixed-width backward convolution with binomial weights
(uses only values dated `<= t`). `min_ffd` sweeps `d` and returns the smallest
that passes the ADF stationarity test (`statsmodels.tsa.stattools.adfuller`,
constant term, AIC lag selection); falls back to `d_range[1]` if none passes.

### No-look-ahead guarantee

Every label/transform for an event/bar at `t` is a function of data dated `<= t`
(vol/frac-diff) or of the forward path `prices.loc[t0:t0+vb]` (barrier outcome)
— never of values before `t0` leaking forward, nor after the first-touch leaking
back. Each function's docstring restates this; the test suite includes explicit
prefix-stability checks (`test_get_daily_vol_backward_only`,
`test_frac_diff_no_lookahead_prefix_stable`).

---

## 3. How a future agent wires this in

### 3a. `LabelAgent` — triple-barrier as an alternative `label_type`

Today `LabelAgent._compute_horizon_labels` emits `forward_log_return`. Add a
`label_type: "triple_barrier"` branch that, per symbol, runs the barrier method
instead of the fixed-horizon shift. Sketch config (`configs/*.yaml`, under
`labels:`):

```yaml
labels:
  label_type: triple_barrier          # or "forward_log_return" (current default)
  triple_barrier:
    pt_sl: [1.0, 1.0]                  # [take-profit, stop-loss] sigma multipliers
    vertical_barrier_days: 14          # time barrier (reuse as recommended_embargo_days)
    vol_lookback: 100                  # EWMA span for get_daily_vol
    min_ret: 0.0                       # optional: drop events below a target vol floor
  # events default to every (symbol, date_ts) row in full_features (CUSUM filter optional later)
```

Per-symbol integration (inside the existing `groupby("symbol")` loop):

```python
from features.labeling import get_daily_vol, triple_barrier_labels

close = grp.set_index("date_ts")["close"]
events = close.index                                   # or a CUSUM-filtered subset
tb = triple_barrier_labels(
    close, events,
    pt_sl=cfg["triple_barrier"]["pt_sl"],
    vertical_barrier_days=cfg["triple_barrier"]["vertical_barrier_days"],
    vol_lookback=cfg["triple_barrier"]["vol_lookback"],
)
# tb["label"] -> label_tb_{h}d ; tb["ret"] -> label_tb_ret_{h}d ; tb["t1"] -> for weights/embargo
```

Key alignment rules to preserve the existing leakage guards:
- Emit labels indexed at the **event time `t0`** (join back onto features at
  `date_ts == t0`), exactly as the current pipeline aligns `label_fwd_logret`.
- Set `recommended_embargo_days` / `purge_train_test_overlap_days` to
  `max(vertical_barrier_days)` so `generate_purged_walk_forward_splits`
  (`models/walk_forward.py`) purges the barrier horizon. `tb["t1"]` gives the
  exact per-label end time for tighter, label-specific purging later.
- Persist the extra columns (`label` {+1,-1,0}, `ret`, `barrier_touched`, `t1`)
  in `labels_{h}d.parquet` / `label_matrix.parquet`; keep them out of the
  feature side of the join so `_find_prohibited_columns` still passes.

### 3b. `ModelAgent` — meta-model as a second stage

The meta-model is a **second-stage binary classifier** filtering a primary
model's calls:

1. **Primary model** (existing `ModelAgent`) produces a side per `(symbol,
   date_ts)`: `side ∈ {-1,0,+1}` (e.g. `sign(prediction)` or a threshold).
2. **Meta-labels**: `meta = meta_labels(side, tb["ret"])` — was the primary call
   profitable?
3. **Meta-model**: train a binary classifier (`predict_proba`) on the same
   features to predict `meta`. At inference, **act only when** `p_meta ≥ τ`;
   size the position by `side * p_meta`.
4. **Sample weights** into the meta-model's `fit(..., sample_weight=w)`:

```python
from features.labeling import (
    meta_labels, sample_weights_by_return, sample_weights_by_uniqueness,
)

meta = meta_labels(primary_side, tb["ret"])
w = (sample_weights_by_return(tb["ret"], normalize="none")
     * sample_weights_by_uniqueness(tb["t1"], normalize="none"))
w = (w / w.sum() * len(w)).reindex(meta.index)          # mean-1 normalize
meta_model.fit(X.loc[meta.index], meta, sample_weight=w.values)
```

Walk-forward CV is unchanged: run the meta-stage **inside** each
`generate_purged_walk_forward_splits` fold (fit primary + meta on train, predict
on the purged/embargoed test), so no meta-label leaks across the purge boundary.

### 3c. `FeatureAgent` — fractional differentiation transform (optional)

For non-stationary level features (log-price, log-market-cap, cumulative
on-chain series), replace naive differencing with FFD:

```python
from features.frac_diff import min_ffd, frac_diff_ffd

d = min_ffd(log_price)                     # per series (fit on train slice only!)
feat = frac_diff_ffd(log_price, d=d)       # stationary + memory-preserving
```

Fit `d` on the **training slice only** (it is a fitted hyper-parameter) and apply
the frozen `d` to test slices, so the transform stays walk-forward-safe.

---

## 4. Tests

`.venv/bin/python -m pytest tests/test_labeling.py -q` → **24 passed**. Coverage:
constructed price paths that hit upper/lower/vertical barriers at known times;
`get_daily_vol` vs. a hand recomputation and a backward-only check; meta-labels
`1` exactly when side matches return sign; weight normalization (sum/mean/sum→1)
and hand-computed uniqueness under overlap; and frac-diff — `d=1` equals first
differences, a random walk becomes ADF-stationary at the discovered `d` while
staying correlated (memory) with the level, and prefix-stability (no look-ahead).
