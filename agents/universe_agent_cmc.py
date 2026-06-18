"""
UniverseAgentCMC — point-in-time universe from DOWNLOADED CoinMarketCap listings.
=================================================================================

Back-compat shim. The three historical universe agents are now unified inside
``agents/universe_agent.py``; this class simply selects the ``cmc_listings_download``
source (reads a downloaded ``cmc_listings_historical.parquet`` produced by
``scripts/build_cmc_history.py``) and delegates to the unified ``UniverseAgent``.
The CMC-specific ingestion logic lives in ``agents/universe_sources.py``.

This is a genuinely survivorship-bias-free path: membership is the real CMC rank
as of each month, and delisted coins remain in their historical snapshots.

NOTE: On the Hobbyist CMC plan, the Pro ``listings/historical`` API is HTTP-400
limited to ~1 month, so this download is shallow. For deep survivorship-free
history prefer the keyless ``cmc_web_pit`` source (``scripts/build_cmc_web_history.py``).

Usage:
    python3 scripts/build_cmc_history.py --start 2025-06-01 --end 2026-06-01 --top 100
    python3 agents/universe_agent_cmc.py --listings data/external/cmc/cmc_listings_historical.parquet
    python3 scripts/verify_universe_run.py --section universe_cmc_3y
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


class UniverseAgentCMC(UniverseAgent):
    """Build the PIT universe from a downloaded CMC listings/historical Parquet."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        listings_path: Optional[str] = None,
        section: str = "universe_cmc_3y",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        output_dir: Optional[str] = None,
    ):
        super().__init__(config)
        merged = dict(self.ucfg)
        merged.update(dict(self.cfg.get(section, {})) if section else {})
        self.ucfg = merged
        self.ucfg["source"] = "cmc_listings_download"
        if listings_path or self.ucfg.get("cmc_listings_path"):
            self.ucfg["cmc_listings_path"] = listings_path or self.ucfg.get("cmc_listings_path")
        if start_date:
            self.ucfg["start_date"] = start_date
        if end_date:
            self.ucfg["end_date"] = end_date
        if output_dir:
            self.ucfg["output_dir"] = output_dir
        # Recompute dirs in case the section/CLI overrode them.
        self.output_dir = self._resolve_output_dir()
        self.cache_dir = self._resolve_cache_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PIT universe from downloaded CMC listings.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--section", default="universe_cmc_3y")
    parser.add_argument("--listings", default=None, help="Path to cmc_listings_historical.parquet")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    cfg = load_config(Path(args.config) if args.config else None)
    agent = UniverseAgentCMC(
        cfg, listings_path=args.listings, section=args.section,
        start_date=args.start, end_date=args.end, output_dir=args.output_dir,
    )
    ok = agent.execute(max_retries=1)
    if not ok:
        print("[universe-cmc] ERROR: UniverseAgentCMC failed.")
        return 1
    print(f"[universe-cmc] Done. Output: {agent.output_paths}")
    print(f"[universe-cmc] Mode: {agent.universe_mode} | survivor_only={agent.survivor_only_universe} "
          f"| snapshots={agent.historical_snapshots_created} | unique_coins={agent.unique_assets_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
