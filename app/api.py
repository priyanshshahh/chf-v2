"""
CHF FastAPI Endpoints (Optional)
Exposes clean REST API endpoints for pipeline outputs.
All endpoints read from pipeline outputs; no business logic duplication.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from configs.config import get_config
from configs.logging_config import get_logger

logger = get_logger("app.api")

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI not installed. API endpoints unavailable.")

if HAS_FASTAPI:
    app = FastAPI(
        title="CHF API",
        description="CHF Mini Hedge Fund — REST API for pipeline outputs",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    cfg = get_config()
    _root = Path(cfg["_project_root"])

    def _load_parquet(path: Path) -> List[Dict]:
        """Load a Parquet file and return as list of dicts."""
        try:
            import pandas as pd
            if not path.exists():
                return []
            df = pd.read_parquet(path)
            return df.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return []

    @app.get("/health")
    def health_check() -> Dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "CHF API",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
        }

    @app.get("/weights")
    def get_weights(
        strategy: str = Query("top_k_equal_weight", description="Portfolio strategy"),
    ) -> Dict[str, Any]:
        """Get latest portfolio weights."""
        path = _root / "data" / "allocations" / "latest_allocation.parquet"
        data = _load_parquet(path)
        if not data:
            raise HTTPException(status_code=404, detail="No allocation data available")
        return {"strategy": strategy, "weights": data, "count": len(data)}

    @app.get("/signals")
    def get_signals(
        model: str = Query("lightgbm", description="Model name"),
        horizon: int = Query(7, description="Prediction horizon in days"),
        limit: int = Query(50, description="Max results"),
    ) -> Dict[str, Any]:
        """Get latest model signals/predictions."""
        import pandas as pd
        path = _root / "data" / "predictions" / f"predictions_{model}_h{horizon}d.parquet"
        data = _load_parquet(path)
        if not data:
            raise HTTPException(status_code=404, detail="No prediction data available")

        # Return most recent predictions
        df = pd.DataFrame(data)
        if "date_ts" in df.columns:
            df["date_ts"] = pd.to_datetime(df["date_ts"])
            latest_date = df["date_ts"].max()
            df = df[df["date_ts"] == latest_date]
        df = df.sort_values("predicted_return", ascending=False).head(limit)
        return {
            "model": model,
            "horizon_days": horizon,
            "signals": df.to_dict(orient="records"),
            "count": len(df),
        }

    @app.get("/runs")
    def get_runs() -> Dict[str, Any]:
        """Get agent run history."""
        import sqlite3
        registry_path = _root / "metadata" / "agent_registry.db"
        if not registry_path.exists():
            return {"runs": [], "count": 0}
        with sqlite3.connect(registry_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 100"
            ).fetchall()
        runs = [dict(r) for r in rows]
        return {"runs": runs, "count": len(runs)}

    @app.get("/metrics")
    def get_metrics() -> Dict[str, Any]:
        """Get latest backtest performance metrics."""
        import pandas as pd
        path = _root / "data" / "backtests" / "backtest_summary.parquet"
        data = _load_parquet(path)
        if not data:
            raise HTTPException(status_code=404, detail="No backtest data available")
        return {"metrics": data, "count": len(data)}

    @app.get("/latest_snapshot")
    def get_latest_snapshot() -> Dict[str, Any]:
        """Get the latest snapshot metadata."""
        import json
        universe_dir = _root / "data" / "raw" / "universe"
        meta_files = sorted(universe_dir.glob("snapshot_meta_*.json"), reverse=True)
        if not meta_files:
            raise HTTPException(status_code=404, detail="No snapshot data available")
        with open(meta_files[0]) as f:
            meta = json.load(f)
        return meta

    def start_api():
        """Start the FastAPI server."""
        api_cfg = cfg.get("api", {})
        uvicorn.run(
            app,
            host=api_cfg.get("host", "0.0.0.0"),
            port=api_cfg.get("port", 8000),
            reload=api_cfg.get("reload", False),
        )

else:
    def start_api():
        logger.error("FastAPI not installed. Cannot start API server.")
