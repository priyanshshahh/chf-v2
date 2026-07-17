"""Hard portfolio limit projection for CHF.

Deterministic, pure-function enforcement of:
  - max single-asset weight
  - max top-5 concentration
  - max per-category weight (when category metadata is available)
  - gross exposure <= max_gross_exposure (remainder is cash)

Algorithm: clip -> renormalize -> iterate to convergence. Any symbol that is
clipped by any rule is frozen (it may be scaled *down* again but never receives
redistributed mass), which bounds the iteration count and guarantees
deterministic convergence. Unallocatable mass falls to cash.

No LLM, no network. CLI:
    python -m portfolio.risk_limits --weights '{"BTC":0.5,"ETH":0.5}'
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

DEFAULT_CONFIG_PATH = "configs/risk.yaml"

DEFAULT_LIMITS = {
    "max_single_asset_weight": 0.20,
    "max_top5_concentration": 0.60,
    "max_category_weight": 0.40,
    "max_gross_exposure": 1.0,
    "max_iterations": 100,
    "tolerance": 1e-9,
}


# -- config ------------------------------------------------------------------


def load_risk_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load configs/risk.yaml; returns the 'risk' mapping."""
    import yaml

    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    risk = raw.get("risk")
    if not isinstance(risk, dict):
        raise ValueError(f"no 'risk' section in {path}")
    return risk


def limits_from_config(risk_cfg: Mapping) -> dict:
    out = dict(DEFAULT_LIMITS)
    for key in out:
        section = risk_cfg.get("limits", {}) if isinstance(risk_cfg, Mapping) else {}
        if key in section:
            out[key] = type(out[key])(section[key])
    return out


# -- category metadata -------------------------------------------------------


def load_categories(sources: Sequence[str]) -> Optional[Dict[str, List[str]]]:
    """Load SYMBOL -> [category, ...] from the first usable source.

    Supported source shapes:
      - JSON file mapping symbol -> list of category names.
      - Directory of CMC pro_api_categories JSON payloads, each with a
        ``data`` list of ``{"name": ..., "coins"|"symbols": [...]}`` entries
        or top-level ``{"name": ..., "coins": [{"symbol": ...}, ...]}``.

    Returns None when no source is usable (degrade to no-category checks).
    """
    for src in sources or []:
        path = Path(src)
        try:
            if path.is_file():
                mapping = _categories_from_json_file(path)
            elif path.is_dir():
                mapping = _categories_from_cmc_dir(path)
            else:
                continue
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if mapping:
            return mapping
    return None


def _categories_from_json_file(path: Path) -> Optional[Dict[str, List[str]]]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        return None
    out: Dict[str, List[str]] = {}
    for sym, cats in raw.items():
        if isinstance(cats, (list, tuple)):
            names = sorted({str(c) for c in cats if c})
            if names:
                out[str(sym).upper()] = names
    return out or None


def _categories_from_cmc_dir(path: Path) -> Optional[Dict[str, List[str]]]:
    out: Dict[str, List[str]] = {}
    for file in sorted(path.glob("**/*.json")):
        try:
            with open(file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        payloads = raw.get("data", raw) if isinstance(raw, dict) else raw
        if isinstance(payloads, dict):
            payloads = [payloads]
        if not isinstance(payloads, list):
            continue
        for entry in payloads:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("title") or entry.get("slug")
            coins = entry.get("coins") or entry.get("symbols") or []
            if not name or not isinstance(coins, list):
                continue
            for coin in coins:
                if isinstance(coin, dict):
                    sym = coin.get("symbol")
                else:
                    sym = coin
                if not sym:
                    continue
                out.setdefault(str(sym).upper(), [])
                if str(name) not in out[str(sym).upper()]:
                    out[str(sym).upper()].append(str(name))
    for sym in out:
        out[sym] = sorted(out[sym])
    return out or None


# -- core projection ---------------------------------------------------------


def apply_risk_limits(
    target_weights: Mapping[str, float],
    limits: Optional[Mapping] = None,
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> Dict[str, object]:
    """Project target weights onto the hard-limit set.

    Returns ``{"weights": {sym: w}, "adjustments": [...], "cash_weight": float,
    "converged": bool, "iterations": int, "category_checks": bool}``.
    Every weight change is recorded as
    ``{"symbol", "reason", "before", "after"}``.
    """
    cfg = dict(DEFAULT_LIMITS)
    if limits:
        cfg.update({k: limits[k] for k in DEFAULT_LIMITS if k in limits})
    max_single = float(cfg["max_single_asset_weight"])
    max_top5 = float(cfg["max_top5_concentration"])
    max_cat = float(cfg["max_category_weight"])
    max_gross = float(cfg["max_gross_exposure"])
    max_iter = int(cfg["max_iterations"])
    tol = float(cfg["tolerance"])

    adjustments: List[dict] = []
    w: Dict[str, float] = {}
    for sym, val in sorted(target_weights.items()):
        val = float(val)
        sym = str(sym).upper()
        if val < 0.0:
            adjustments.append(
                {"symbol": sym, "reason": "negative_weight_dropped",
                 "before": val, "after": 0.0}
            )
            continue
        if val > 0.0:
            w[sym] = val

    cat_map: Dict[str, List[str]] = {}
    if categories:
        for sym in w:
            for cat in categories.get(sym, []) or []:
                cat_map.setdefault(str(cat), []).append(sym)

    gross_target = min(sum(w.values()), max_gross)
    if sum(w.values()) > max_gross + tol:
        scale = max_gross / sum(w.values())
        for sym in sorted(w):
            before = w[sym]
            w[sym] = before * scale
            adjustments.append(
                {"symbol": sym, "reason": "gross_exposure_cap",
                 "before": before, "after": w[sym]}
            )

    frozen: set = set()
    converged = False
    iterations = 0
    for iterations in range(1, max_iter + 1):
        changed = False

        # 1. single-asset cap
        for sym in sorted(w):
            if w[sym] > max_single + tol:
                adjustments.append(
                    {"symbol": sym, "reason": "max_single_asset_weight",
                     "before": w[sym], "after": max_single}
                )
                w[sym] = max_single
                frozen.add(sym)
                changed = True

        # 2. top-5 concentration (deterministic tie-break: weight desc, symbol asc)
        ranked = sorted(w, key=lambda s: (-w[s], s))
        top5 = ranked[:5]
        s5 = sum(w[s] for s in top5)
        if s5 > max_top5 + tol:
            scale = max_top5 / s5
            for sym in top5:
                before = w[sym]
                w[sym] = before * scale
                adjustments.append(
                    {"symbol": sym, "reason": "max_top5_concentration",
                     "before": before, "after": w[sym]}
                )
                frozen.add(sym)
            changed = True

        # 3. category caps
        for cat in sorted(cat_map):
            members = [s for s in cat_map[cat] if s in w]
            sc = sum(w[s] for s in members)
            if sc > max_cat + tol:
                scale = max_cat / sc
                for sym in sorted(members):
                    before = w[sym]
                    w[sym] = before * scale
                    adjustments.append(
                        {"symbol": sym, "reason": f"max_category_weight:{cat}",
                         "before": before, "after": w[sym]}
                    )
                    frozen.add(sym)
                changed = True

        # 4. renormalize: redistribute deficit to unfrozen symbols below cap
        deficit = gross_target - sum(w.values())
        if deficit > tol:
            eligible = [s for s in sorted(w)
                        if s not in frozen and w[s] < max_single - tol]
            pool = sum(w[s] for s in eligible)
            if eligible and pool > tol:
                for sym in eligible:
                    before = w[sym]
                    add = deficit * before / pool
                    after = min(before + add, max_single)
                    if after > before + tol:
                        w[sym] = after
                        adjustments.append(
                            {"symbol": sym, "reason": "renormalize",
                             "before": before, "after": after}
                        )
                        if after >= max_single - tol:
                            frozen.add(sym)
                        changed = True

        if not changed:
            converged = True
            break

    total = sum(w.values())
    return {
        "weights": {s: w[s] for s in sorted(w)},
        "adjustments": adjustments,
        "cash_weight": max(0.0, 1.0 - total),
        "gross_exposure": total,
        "converged": converged,
        "iterations": iterations,
        "category_checks": bool(cat_map),
    }


# -- CLI ---------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CHF hard-limit projection")
    parser.add_argument("--weights", required=True,
                        help='JSON dict of target weights, e.g. \'{"BTC":0.5}\'')
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)

    risk_cfg = load_risk_config(args.config)
    limits = limits_from_config(risk_cfg)
    categories = load_categories(
        risk_cfg.get("limits", {}).get("category_sources", [])
    )
    result = apply_risk_limits(json.loads(args.weights), limits, categories)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
