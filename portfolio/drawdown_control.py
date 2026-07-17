"""Drawdown governance state machine for CHF paper books.

States (per book): NORMAL -> DERISKED (soft breach) -> FLAT (hard breach),
with deterministic re-entry stepping back UP one state at a time when either
  (a) NAV recovers half the drawdown from trough, or
  (b) >= `reentry_days` elapsed in the current state AND NAV is above its
      `reentry_ma_days`-day moving average.

Escalation rules:
  - NORMAL -> DERISKED when drawdown from peak <= soft_breach (default -10%).
  - -> FLAT when drawdown from peak <= hard_breach (default -20%).
  - After a step-down from FLAT to DERISKED, re-escalation to FLAT requires a
    NEW low (NAV < the trough at the time of the step-down) to prevent
    flip-flopping.
  - On re-entry to NORMAL the peak/trough anchors reset to the current NAV.

State is persisted to ``data/risk/drawdown_state_<book>.json`` and every
transition is appended to the state's ``transitions`` log.

CLI:
    python -m portfolio.drawdown_control --book ridge_30d_top5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

NORMAL = "NORMAL"
DERISKED = "DERISKED"
FLAT = "FLAT"
_SEVERITY = {NORMAL: 0, DERISKED: 1, FLAT: 2}
_ORDER = [NORMAL, DERISKED, FLAT]

DEFAULTS = {
    "soft_breach": -0.10,
    "hard_breach": -0.20,
    "derisked_multiplier": 0.5,
    "flat_multiplier": 0.0,
    "reentry_recovery_fraction": 0.5,
    "reentry_days": 30,
    "reentry_ma_days": 30,
    "state_file_template": "data/risk/drawdown_state_{book}.json",
}


def initial_state(nav: float, date: str) -> Dict[str, object]:
    return {
        "state": NORMAL,
        "peak_nav": float(nav),
        "trough_nav": float(nav),
        "reentry_floor": None,   # trough locked in at last FLAT->DERISKED step-down
        "state_entered_date": str(date),
        "last_date": str(date),
        "last_nav": float(nav),
        "nav_window": [float(nav)],  # trailing NAVs for the re-entry MA
        "transitions": [],
    }


def multiplier_for_state(state: str, cfg: Optional[Mapping] = None) -> float:
    params = dict(DEFAULTS)
    if cfg:
        params.update({k: cfg[k] for k in DEFAULTS if k in cfg})
    return {
        NORMAL: 1.0,
        DERISKED: float(params["derisked_multiplier"]),
        FLAT: float(params["flat_multiplier"]),
    }[str(state)]


def _days_between(d0: str, d1: str) -> int:
    from datetime import date

    a = date.fromisoformat(str(d0)[:10])
    b = date.fromisoformat(str(d1)[:10])
    return (b - a).days


def update_state(
    state: Mapping[str, object],
    date: str,
    nav: float,
    cfg: Optional[Mapping] = None,
) -> Tuple[Dict[str, object], List[dict]]:
    """Advance the state machine by one NAV observation.

    Pure function: returns (new_state, transitions_this_step). At most one
    step-up (re-entry) per observation; escalation jumps straight to the
    breached state. Observations at or before ``last_date`` are ignored.
    """
    params = dict(DEFAULTS)
    if cfg:
        params.update({k: cfg[k] for k in DEFAULTS if k in cfg})

    st: Dict[str, object] = json.loads(json.dumps(dict(state)))  # deep copy
    if str(date) <= str(st["last_date"]):
        return st, []

    nav = float(nav)
    transitions: List[dict] = []
    ma_days = int(params["reentry_ma_days"])

    st["nav_window"] = (list(st.get("nav_window", [])) + [nav])[-ma_days:]
    st["last_date"] = str(date)
    st["last_nav"] = nav

    peak = float(st["peak_nav"])
    trough = float(st["trough_nav"])
    if nav > peak and st["state"] == NORMAL:
        peak = nav
        trough = nav  # new high while healthy: reset the episode
    trough = min(trough, nav)
    st["peak_nav"], st["trough_nav"] = peak, trough

    dd = nav / peak - 1.0 if peak > 0 else 0.0
    current = str(st["state"])

    def _log(frm: str, to: str, reason: str) -> None:
        transitions.append(
            {"date": str(date), "from": frm, "to": to, "reason": reason,
             "nav": nav, "peak_nav": peak, "trough_nav": float(st["trough_nav"]),
             "drawdown": dd}
        )
        st["state"] = to
        st["state_entered_date"] = str(date)

    # -- escalation (checked first) ------------------------------------------
    target = current
    if dd <= float(params["hard_breach"]):
        target = FLAT
    elif dd <= float(params["soft_breach"]):
        target = DERISKED if _SEVERITY[current] < _SEVERITY[DERISKED] else current

    if _SEVERITY.get(target, 0) > _SEVERITY[current]:
        blocked = (
            target == FLAT
            and st.get("reentry_floor") is not None
            and nav >= float(st["reentry_floor"])
        )
        if not blocked:
            reason = ("hard_breach" if target == FLAT else "soft_breach")
            if target == FLAT and st.get("reentry_floor") is not None:
                reason = "new_low_below_reentry_floor"
                st["reentry_floor"] = None
            _log(current, target, reason)
            st["transitions"] = list(st["transitions"]) + transitions
            return st, transitions

    # -- re-entry: step back up one state ------------------------------------
    if current in (DERISKED, FLAT):
        frac = float(params["reentry_recovery_fraction"])
        recovered = nav >= trough + frac * (peak - trough) and peak > trough
        window = list(st["nav_window"])
        ma = sum(window) / len(window)
        timed = (
            _days_between(str(st["state_entered_date"]), str(date))
            >= int(params["reentry_days"])
            and len(window) >= ma_days
            and nav > ma
        )
        if recovered or timed:
            up = _ORDER[_SEVERITY[current] - 1]
            reason = "recovered_half_drawdown" if recovered else "time_and_above_ma"
            if current == FLAT:
                st["reentry_floor"] = float(st["trough_nav"])
            _log(current, up, reason)
            if up == NORMAL:
                st["peak_nav"] = nav
                st["trough_nav"] = nav
                st["reentry_floor"] = None

    st["transitions"] = list(st["transitions"]) + transitions
    return st, transitions


def replay(
    state: Optional[Mapping[str, object]],
    nav_series: Sequence[Tuple[str, float]],
    cfg: Optional[Mapping] = None,
) -> Tuple[Dict[str, object], List[dict]]:
    """Feed (date, nav) points in order; only points after last_date apply."""
    points = sorted(((str(d), float(v)) for d, v in nav_series), key=lambda p: p[0])
    if state is None:
        if not points:
            raise ValueError("cannot initialize drawdown state from empty series")
        state = initial_state(points[0][1], points[0][0])
        points = points[1:]
    st = dict(state)
    all_transitions: List[dict] = []
    for date, nav in points:
        st, trans = update_state(st, date, nav, cfg)
        all_transitions.extend(trans)
    return st, all_transitions


# -- persistence -------------------------------------------------------------


def state_path(book: str, cfg: Optional[Mapping] = None) -> Path:
    params = dict(DEFAULTS)
    if cfg:
        params.update({k: cfg[k] for k in DEFAULTS if k in cfg})
    return Path(str(params["state_file_template"]).format(book=book))


def load_state(book: str, cfg: Optional[Mapping] = None) -> Optional[Dict[str, object]]:
    path = state_path(book, cfg)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(book: str, state: Mapping[str, object],
               cfg: Optional[Mapping] = None) -> Path:
    path = state_path(book, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    tmp.replace(path)
    return path


# -- CLI ---------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    import pandas as pd

    from portfolio.risk_limits import load_risk_config

    parser = argparse.ArgumentParser(description="CHF drawdown governance")
    parser.add_argument("--book", required=True)
    parser.add_argument("--config", default="configs/risk.yaml")
    parser.add_argument("--equity", default=None,
                        help="override equity parquet (default data/papertrade/<book>/equity.parquet)")
    args = parser.parse_args(argv)

    risk_cfg = load_risk_config(args.config)
    dd_cfg = risk_cfg.get("drawdown", {})
    equity_path = args.equity or f"data/papertrade/{args.book}/equity.parquet"
    eq = pd.read_parquet(equity_path)
    series = [(str(d)[:10], float(n)) for d, n in zip(eq["date"], eq["nav"])]

    prev = load_state(args.book, dd_cfg)
    state, transitions = replay(prev, series, dd_cfg)
    save_state(args.book, state, dd_cfg)
    print(json.dumps(
        {"book": args.book, "state": state["state"],
         "multiplier": multiplier_for_state(state["state"], dd_cfg),
         "peak_nav": state["peak_nav"], "trough_nav": state["trough_nav"],
         "new_transitions": transitions,
         "state_file": str(state_path(args.book, dd_cfg))},
        indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
