"""
UniverseAgentFree — survivorship-bias-free historical universe from a FREE dataset.
====================================================================================

Back-compat shim. The three historical universe agents are now unified inside
``agents/universe_agent.py``; this class selects the ``local_dataset`` source (reads a
free historical rankings dataset — CSV/Parquet/JSON — and takes an as-of monthly
cross-section) and delegates to the unified ``UniverseAgent``. The dataset loading
and as-of logic live in ``agents/universe_sources.py``.

The point-in-time market-cap *membership* is verified at past dates; the
365-day-maturity, exchange-tradability, and on-chain-coverage gates cannot be
reconstructed from a bare rankings dataset, so they are disabled for this source
(recorded in the manifest). Category-tag classification is handled by the unified
classifier.

Getting a REAL dataset (no synthetic data):
    python3 scripts/build_coingecko_history.py --top 250        # CoinGecko free (~365d)
    # or the deeper, survivorship-free keyless source:
    python3 scripts/build_cmc_web_history.py --start 2021-01-01 --top 300 --freq monthly

Then:
    python3 agents/universe_agent_free.py --dataset data/external/coingecko_history.parquet
    python3 scripts/verify_universe_run.py --section universe_free

Expected dataset columns (case-insensitive; override via ``universe.column_map``).
Only date / symbol / market_cap are required:
    date | symbol | name | market_cap | rank | volume_24h | price
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.universe_agent import UniverseAgent  # noqa: E402
from configs.config import load_config  # noqa: E402


class UniverseAgentFree(UniverseAgent):
    """Historical point-in-time universe sourced from a free local dataset."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        dataset_path: Optional[str] = None,
        section: str = "universe_free",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        super().__init__(config)
        merged = dict(self.ucfg)
        merged.update(dict(self.cfg.get(section, {})) if section else {})
        self.ucfg = merged
        self.ucfg["source"] = "local_dataset"
        # A bare rankings dataset cannot verify these gates at past dates, so they are
        # force-disabled for this source (recorded in the manifest limitation). Use the
        # cmc_web_pit source for strict point-in-time-correct gating.
        self.ucfg["require_365d_maturity"] = False
        self.ucfg["require_exchange_tradability"] = False
        self.ucfg["require_onchain_coverage"] = False
        if dataset_path or self.ucfg.get("historical_dataset_path"):
            self.ucfg["historical_dataset_path"] = dataset_path or self.ucfg.get("historical_dataset_path")
        if start_date:
            self.ucfg["start_date"] = start_date
        if end_date:
            self.ucfg["end_date"] = end_date
        if output_dir:
            self.ucfg["output_dir"] = output_dir
        self.output_dir = self._resolve_output_dir()
        self.cache_dir = self._resolve_cache_dir()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a survivorship-bias-free historical universe from a free local dataset."
    )
    parser.add_argument("--config", default=None, help="Path to run_config.yaml")
    parser.add_argument("--section", default="universe_free", help="Config section merged over [universe]")
    parser.add_argument("--dataset", default=None, help="Path to the historical rankings dataset (CSV/Parquet/JSON)")
    parser.add_argument("--start", default=None, help="First snapshot month (YYYY-MM-01), overrides config")
    parser.add_argument("--end", default=None, help="Last snapshot month (YYYY-MM-01), overrides config")
    parser.add_argument("--output-dir", default=None, help="Override universe output directory")
    args = parser.parse_args()

    cfg = load_config(Path(args.config) if args.config else None)
    agent = UniverseAgentFree(
        cfg,
        dataset_path=args.dataset,
        section=args.section,
        start_date=args.start,
        end_date=args.end,
        output_dir=args.output_dir,
    )
    ok = agent.execute(max_retries=1)
    if not ok:
        print("[universe-free] ERROR: UniverseAgentFree failed.")
        return 1
    print(f"[universe-free] Done. Output: {agent.output_paths}")
    print(f"[universe-free] Mode: {agent.universe_mode} | survivor_only={agent.survivor_only_universe} "
          f"| snapshots={agent.historical_snapshots_created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
