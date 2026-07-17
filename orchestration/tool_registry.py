"""Tool registry: wraps the existing CHF CLI stages as callable tools.

Each pipeline stage stays a deterministic execution primitive; this module
only declares metadata (inputs, outputs, approval gates, verifier) and builds
the subprocess command:

    .venv/bin/python main.py <stage> --config configs/run_config.yaml

Nothing here calls an LLM and nothing here executes anything — execution is
the job of ``orchestration.execution_agent``.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestration.contracts import ToolSpec

# Result-affecting stages rewrite research outputs (predictions, allocations,
# backtests, alpha experiments) and therefore always require human approval.
RESULT_AFFECTING_STAGES = {"model", "portfolio", "backtest", "alpha_research"}

# Canonical-data stages rewrite stores that the frozen research depends on
# (universe definition, canonical feature store, canonical modeling dataset).
# They are not result-affecting per se, but still require approval.
CANONICAL_DATA_STAGES = {"universe", "features", "labels"}

# stage -> verifier script filename (used only if the file exists on disk)
VERIFIER_SCRIPTS = {
    "universe": "verify_universe_run.py",
    "market": "verify_market_run.py",
    "onchain": "verify_onchain_run.py",
    "features": "verify_feature_run.py",
    "labels": "verify_label_run.py",
    "model": "verify_model_run.py",
    "portfolio": "verify_portfolio_run.py",
    "backtest": "verify_backtest_run.py",
    "alpha_research": "verify_alpha_research_run.py",
    "papertrade": None,
}

# Canonical stage DAG. ``writes_paths`` are the freshness-bearing outputs;
# ``reads_paths`` are derived from the outputs of ``depends_on`` stages.
_STAGE_DAG: List[Dict[str, Any]] = [
    {
        "name": "universe",
        "description": "UniverseAgent: monthly point-in-time universe snapshot",
        "depends_on": [],
        "writes_paths": ["data/raw/universe/universe_monthly.parquet"],
    },
    {
        "name": "market",
        "description": "MarketDataAgent: OHLCV ingestion for the universe",
        "depends_on": ["universe"],
        "writes_paths": ["data/raw/market"],
    },
    {
        "name": "onchain",
        "description": "OnChainAgent: on-chain metric ingestion",
        "depends_on": ["universe"],
        "writes_paths": ["data/raw/onchain"],
    },
    {
        "name": "features",
        "description": "FeatureAgent: canonical feature store build",
        "depends_on": ["market", "onchain"],
        "writes_paths": ["data/features/full_features.parquet"],
    },
    {
        "name": "labels",
        "description": "LabelAgent: forward-return labels + modeling dataset",
        "depends_on": ["features"],
        "writes_paths": [
            "data/labels/modeling_dataset.parquet",
            "data/labels/label_matrix.parquet",
        ],
    },
    {
        "name": "model",
        "description": "ModelAgent: walk-forward model training + predictions",
        "depends_on": ["labels"],
        "writes_paths": ["data/predictions/fold_metrics.parquet"],
    },
    {
        "name": "portfolio",
        "description": "PortfolioAgent: allocation construction",
        "depends_on": ["model"],
        "writes_paths": ["data/allocations/latest_allocation.parquet"],
    },
    {
        "name": "backtest",
        "description": "BacktestAgent: vectorized backtest + benchmarks",
        "depends_on": ["portfolio"],
        "writes_paths": ["data/backtests/backtest_summary.parquet"],
    },
    {
        "name": "alpha_research",
        "description": "AlphaResearchAgent: candidate alpha experiments",
        "depends_on": ["labels"],
        "writes_paths": ["data/predictions/alpha_research_manifest.json"],
    },
    {
        "name": "papertrade",
        "description": "Paper-trading engine: one daily cycle over all books",
        "depends_on": ["portfolio"],
        "writes_paths": ["data/papertrade/papertrade_manifest.json"],
    },
]

# Cap directory scans so freshness checks stay cheap on large stores.
_MAX_DIR_SCAN_FILES = 5000


def _newest_mtime(path: Path) -> Optional[float]:
    """Newest file mtime under ``path`` (file or directory), or None."""
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_mtime
    newest: Optional[float] = None
    count = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        count += 1
        mtime = child.stat().st_mtime
        if newest is None or mtime > newest:
            newest = mtime
        if count >= _MAX_DIR_SCAN_FILES:
            break
    return newest


class ToolRegistry:
    """Registry of pipeline stages exposed as tools."""

    def __init__(
        self,
        project_root: Path,
        config_path: str = "configs/run_config.yaml",
        python_bin: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.config_path = config_path
        if python_bin is None:
            venv_python = self.project_root / ".venv" / "bin" / "python"
            python_bin = str(venv_python) if venv_python.exists() else sys.executable
        self.python_bin = python_bin
        self._specs: Dict[str, ToolSpec] = {}
        for node in _STAGE_DAG:
            name = node["name"]
            reads: List[str] = []
            for dep in node["depends_on"]:
                dep_node = next(n for n in _STAGE_DAG if n["name"] == dep)
                reads.extend(dep_node["writes_paths"])
            verifier_file = VERIFIER_SCRIPTS.get(name)
            verifier = None
            if verifier_file and (self.project_root / "scripts" / verifier_file).exists():
                verifier = f"scripts/{verifier_file}"
            self._specs[name] = ToolSpec(
                name=name,
                description=node["description"],
                depends_on=list(node["depends_on"]),
                reads_paths=reads,
                writes_paths=list(node["writes_paths"]),
                result_affecting=name in RESULT_AFFECTING_STAGES,
                requires_approval=(
                    name in RESULT_AFFECTING_STAGES or name in CANONICAL_DATA_STAGES
                ),
                verifier=verifier,
            )

    # ------------------------------------------------------------------
    # lookups
    # ------------------------------------------------------------------
    def stages(self) -> List[str]:
        return [node["name"] for node in _STAGE_DAG]

    def get(self, stage: str) -> ToolSpec:
        if stage not in self._specs:
            raise KeyError(f"Unknown stage/tool: {stage}")
        return self._specs[stage]

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def build_command(self, stage: str) -> List[str]:
        """Subprocess argv for one stage. Tests monkeypatch this method."""
        self.get(stage)  # validate
        return [self.python_bin, "main.py", stage, "--config", self.config_path]

    def build_verifier_command(self, stage: str) -> Optional[List[str]]:
        spec = self.get(stage)
        if not spec.verifier:
            return None
        return [self.python_bin, spec.verifier]

    # ------------------------------------------------------------------
    # freshness
    # ------------------------------------------------------------------
    def artifact_freshness(self, stage: str) -> Dict[str, Any]:
        """Compute output-vs-input mtime freshness for one stage.

        A stage is *fresh* when every declared output exists and the oldest
        output is at least as new as the newest input. ``papertrade`` is a
        daily cycle: it is fresh only if its manifest's ``as_of`` equals
        today's UTC date.
        """
        spec = self.get(stage)
        out_mtimes: List[float] = []
        outputs_exist = True
        for rel in spec.writes_paths:
            mtime = _newest_mtime(self.project_root / rel)
            if mtime is None:
                outputs_exist = False
            else:
                out_mtimes.append(mtime)
        in_mtimes: List[float] = []
        for rel in spec.reads_paths:
            mtime = _newest_mtime(self.project_root / rel)
            if mtime is not None:
                in_mtimes.append(mtime)

        newest_input = max(in_mtimes) if in_mtimes else None
        oldest_output = min(out_mtimes) if out_mtimes else None

        if stage == "papertrade":
            fresh = self._papertrade_ran_today()
        elif not outputs_exist:
            fresh = False
        elif newest_input is None:
            fresh = True  # no inputs: existing outputs count as fresh
        else:
            fresh = oldest_output is not None and oldest_output >= newest_input

        return {
            "stage": stage,
            "outputs_exist": outputs_exist,
            "oldest_output_mtime": oldest_output,
            "newest_input_mtime": newest_input,
            "fresh": fresh,
        }

    def _papertrade_ran_today(self) -> bool:
        manifest = self.project_root / "data" / "papertrade" / "papertrade_manifest.json"
        if not manifest.exists():
            return False
        try:
            with open(manifest, "r") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        today = datetime.now(timezone.utc).date().isoformat()
        return doc.get("as_of") == today

    def freshness_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {stage: self.artifact_freshness(stage) for stage in self.stages()}
