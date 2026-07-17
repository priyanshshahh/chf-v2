"""Weekly champion/challenger scoring of paper-trade books.

Scores every book on 30/60/90d return, vol, Sharpe (sqrt(365)) and max
drawdown, ranks them, and tracks consecutive 60d-Sharpe wins of each
challenger against the champion (canonical_best_model). After K consecutive
winning runs it writes a promotion PROPOSAL as a *pending* approval into
memory/approvals.json via orchestration.state_store.StateStore — it never
auto-promotes. Exit 1 when a promotion proposal is pending.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from monitoring.common import (
    annualized_sharpe,
    annualized_vol,
    build_arg_parser,
    exit_code,
    list_papertrade_books,
    load_config,
    max_drawdown,
    read_json_safe,
    read_parquet_safe,
    resolve_root,
    utcnow_iso,
    write_json,
    write_report,
)


def score_book(equity: pd.DataFrame, windows: List[int], min_obs: int) -> Dict[str, Any]:
    """Windowed performance metrics from a book's equity history."""
    eq = equity.sort_values("date")
    nav = eq["nav"].astype(float)
    rets = eq["daily_return"].astype(float).iloc[1:]  # first row is a placeholder
    out: Dict[str, Any] = {"n_days": int(len(rets))}
    for w in windows:
        tail = rets.tail(w)
        if len(tail) < min_obs:
            out[f"return_{w}d"] = None
            out[f"vol_{w}d"] = None
            out[f"sharpe_{w}d"] = None
            out[f"max_dd_{w}d"] = None
            continue
        out[f"return_{w}d"] = float((1.0 + tail).prod() - 1.0)
        out[f"vol_{w}d"] = annualized_vol(tail)
        out[f"sharpe_{w}d"] = annualized_sharpe(tail)
        out[f"max_dd_{w}d"] = max_drawdown(nav.tail(w + 1))
    return out


def evaluate(
    scores: Dict[str, Dict[str, Any]],
    champion: str,
    state: Dict[str, Any],
    cfg: Dict[str, Any],
    store: Any,
) -> Dict[str, Any]:
    """Update win streaks and register pending promotion proposals.

    ``store`` must expose ``ensure_pending_approval(step_id, stage, goal,
    reason)`` (orchestration.state_store.StateStore).
    """
    k_required = int(cfg.get("k_consecutive", 3))
    sharpe_key = f"sharpe_{int(cfg.get('sharpe_window', 60))}d"
    exclude = set(cfg.get("exclude_books", []) or [])
    champ_sharpe = (scores.get(champion) or {}).get(sharpe_key)

    streaks: Dict[str, Any] = state.setdefault("streaks", {})
    proposals: List[Dict[str, Any]] = []
    for book, metrics in scores.items():
        if book == champion or book in exclude:
            continue
        challenger_sharpe = metrics.get(sharpe_key)
        win = bool(
            challenger_sharpe is not None
            and (champ_sharpe is None or challenger_sharpe > champ_sharpe)
        )
        entry = streaks.setdefault(book, {"consecutive_wins": 0})
        entry["consecutive_wins"] = entry["consecutive_wins"] + 1 if win else 0
        entry["last_run_utc"] = utcnow_iso()
        entry["last_win"] = win
        entry["last_challenger_sharpe"] = challenger_sharpe
        entry["last_champion_sharpe"] = champ_sharpe
        if entry["consecutive_wins"] >= k_required:
            reason = (
                f"champion_challenger: {book} beat champion {champion} on {sharpe_key} "
                f"for {entry['consecutive_wins']} consecutive weekly runs "
                f"(challenger={challenger_sharpe}, champion={champ_sharpe}). "
                "PROPOSAL only — requires human approval to promote."
            )
            approval = store.ensure_pending_approval(
                step_id=f"champion_promotion:{book}",
                stage="portfolio",
                goal="champion_promotion",
                reason=reason,
            )
            proposals.append(
                {
                    "challenger": book,
                    "approval_id": approval.approval_id,
                    "approval_status": approval.status.value
                    if hasattr(approval.status, "value")
                    else str(approval.status),
                    "consecutive_wins": entry["consecutive_wins"],
                }
            )
    return {"streaks": streaks, "proposals": proposals}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser("CHF champion/challenger weekly scoring").parse_args(argv)
    root = resolve_root(args.root)
    config = load_config(root, args.config)
    paths = config["paths"]
    cfg = config.get("champion_challenger", {})
    champion = str(cfg.get("champion_book", "canonical_best_model"))
    windows = [int(w) for w in cfg.get("windows", [30, 60, 90])]
    min_obs = int(cfg.get("min_obs", 15))
    sharpe_key = f"sharpe_{int(cfg.get('sharpe_window', 60))}d"

    sys.path.insert(0, str(root))
    from orchestration.state_store import StateStore  # reuse the exact Approval schema

    scores: Dict[str, Dict[str, Any]] = {}
    for book in list_papertrade_books(root, paths["papertrade_dir"]):
        equity = read_parquet_safe(root / paths["papertrade_dir"] / book / "equity.parquet")
        if equity is None or equity.empty or "daily_return" not in equity.columns:
            scores[book] = {"n_days": 0}
            continue
        scores[book] = score_book(equity, windows, min_obs)

    state_path = root / cfg.get("state_file", "data/reports/monitoring/champion_challenger_state.json")
    state = read_json_safe(state_path) or {}
    store = StateStore(root / paths["memory_dir"])
    result = evaluate(scores, champion, state, cfg, store)
    write_json(state_path, state)

    rank_rows = [
        {"book": book, **{k: v for k, v in metrics.items()}} for book, metrics in scores.items()
    ]
    table = pd.DataFrame(rank_rows)
    if sharpe_key in table.columns:
        table = table.sort_values(sharpe_key, ascending=False, na_position="last")
        table.insert(1, "rank", range(1, len(table) + 1))

    summary = {
        "status": "promotion_proposed" if result["proposals"] else "ok",
        "champion": champion,
        "sharpe_key": sharpe_key,
        "k_consecutive_required": int(cfg.get("k_consecutive", 3)),
        "scores": scores,
        "streaks": result["streaks"],
        "proposals": result["proposals"],
        "alerts": [
            f"promotion proposal pending for {p['challenger']} ({p['approval_id']})"
            for p in result["proposals"]
        ],
    }
    written = write_report(root, paths["reports_dir"], "champion_challenger", summary, table)
    print(f"champion_challenger: status={summary['status']} books={len(scores)} -> {written['json']}")
    for alert in summary["alerts"]:
        print(f"  ALERT: {alert}")
    return exit_code(bool(result["proposals"]))


if __name__ == "__main__":
    raise SystemExit(main())
