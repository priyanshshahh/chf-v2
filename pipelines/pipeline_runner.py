"""
CHF Pipeline Runner
Orchestrates the full pipeline DAG:
Universe → MarketData → OnChain → Feature → Label → Model → Portfolio → Backtest → Report

Supports:
- Full pipeline run
- Individual agent runs
- Resume from checkpoint
- Structured JSON logging
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from configs.config import get_config
from configs.logging_config import get_logger, setup_logging

logger = get_logger("pipeline_runner")


class PipelineRunner:
    """
    Orchestrates the full CHF pipeline.
    Each agent is run in sequence with dependency checking.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = config or get_config()
        self._root = Path(self.cfg["_project_root"])
        self._results: Dict[str, bool] = {}

    def run_universe(self, snapshot_date: Optional[str] = None) -> bool:
        """Run UniverseAgent."""
        from agents.universe_agent import UniverseAgent
        agent = UniverseAgent(self.cfg, snapshot_date=snapshot_date)
        success = agent.execute(max_retries=1)
        self._results["universe"] = success
        return success

    def validate_universe_outputs(self) -> bool:
        """Validate research universe artifacts before downstream stages."""
        try:
            if not self.cfg.get("universe", {}).get("research_mode", False):
                return True
            from scripts.verify_universe_run import validate_universe_outputs

            failures = validate_universe_outputs(self.cfg)
            if failures:
                for failure in failures:
                    logger.error(f"Universe validation failed: {failure}")
                self._results["universe_validation"] = False
                return False
            self._results["universe_validation"] = True
            return True
        except Exception as exc:
            logger.error(f"Universe validation crashed: {exc}")
            self._results["universe_validation"] = False
            return False

    def run_market_data(self, symbols: Optional[List[str]] = None) -> bool:
        """Run MarketDataAgent."""
        if not self.validate_universe_outputs():
            self._results["market_data"] = False
            return False
        from agents.market_data_agent import MarketDataAgent
        agent = MarketDataAgent(self.cfg, symbols=symbols)
        success = agent.execute(max_retries=1)
        if success and not self.validate_market_outputs():
            success = False
        self._results["market_data"] = success
        return success

    def validate_market_outputs(self) -> bool:
        """Validate canonical market outputs before downstream stages."""
        try:
            from scripts.verify_market_run import validate_market_outputs

            failures = validate_market_outputs(self.cfg)
            if failures:
                for failure in failures:
                    logger.error(f"Market validation failed: {failure}")
                self._results["market_validation"] = False
                return False
            self._results["market_validation"] = True
            return True
        except Exception as exc:
            logger.error(f"Market validation crashed: {exc}")
            self._results["market_validation"] = False
            return False

    def run_onchain(self) -> bool:
        """Run OnChainAgent."""
        if not self.validate_market_outputs():
            self._results["onchain"] = False
            return False
        from agents.onchain_agent import OnChainAgent
        agent = OnChainAgent(self.cfg)
        success = agent.execute(max_retries=1)
        if success and not self.validate_onchain_outputs():
            success = False
        self._results["onchain"] = success
        return success

    def validate_onchain_outputs(self) -> bool:
        """Validate canonical on-chain outputs before downstream stages."""
        try:
            from scripts.verify_onchain_run import validate_onchain_outputs

            failures = validate_onchain_outputs(self.cfg)
            if failures:
                for failure in failures:
                    logger.error(f"On-chain validation failed: {failure}")
                self._results["onchain_validation"] = False
                return False
            self._results["onchain_validation"] = True
            return True
        except Exception as exc:
            logger.error(f"On-chain validation crashed: {exc}")
            self._results["onchain_validation"] = False
            return False

    def run_clean(self) -> bool:
        """Run data cleaning pipeline."""
        try:
            from pipelines.data_cleaner import MarketDataCleaner, OnChainDataCleaner
            from configs.config import resolve_path

            raw_dir = resolve_path(self.cfg, "raw")
            cleaned_dir = resolve_path(self.cfg, "cleaned")

            # Clean market data
            mkt_cleaner = MarketDataCleaner(self.cfg)
            qa_df = mkt_cleaner.clean_all_symbols(
                raw_dir / "market", cleaned_dir
            )
            logger.info(f"Market data cleaning complete: {len(qa_df)} symbols")

            # Clean on-chain data
            oc_cleaner = OnChainDataCleaner()
            oc_cleaner.clean_all_symbols(raw_dir / "onchain", cleaned_dir)
            logger.info("On-chain data cleaning complete")

            self._results["clean"] = True
            return True
        except Exception as e:
            logger.error(f"Data cleaning failed: {e}")
            self._results["clean"] = False
            return False

    def run_features(self) -> bool:
        """Run canonical FeatureAgent after verified market/onchain outputs exist."""
        from agents.feature_agent import FeatureAgent

        market_path = Path(self.cfg["_project_root"]) / "data/raw/market/market_ohlcv.parquet"
        onchain_path = Path(self.cfg["_project_root"]) / "data/raw/onchain/onchain_wide.parquet"
        if not market_path.exists():
            logger.error("FeatureAgent precheck failed: missing market_ohlcv.parquet")
            self._results["features"] = False
            return False
        if not onchain_path.exists():
            logger.error("FeatureAgent precheck failed: missing onchain_wide.parquet")
            self._results["features"] = False
            return False
        success = FeatureAgent(self.cfg).execute()
        if not success:
            logger.error("FeatureAgent failed")
            self._results["features"] = False
            return False
        full_path = Path(self.cfg["_project_root"]) / str((self.cfg.get("features") or {}).get("output_dir", "data/features")) / "full_features.parquet"
        if not full_path.exists():
            logger.error("FeatureAgent postcheck failed: missing full_features.parquet")
            self._results["features"] = False
            return False
        self._results["features"] = True
        return True

    def run_labels(self) -> bool:
        """Run LabelAgent against verified canonical market + feature outputs."""
        market_path = Path(self.cfg["_project_root"]) / "data/raw/market/market_ohlcv.parquet"
        full_features_path = Path(self.cfg["_project_root"]) / str((self.cfg.get("features") or {}).get("output_dir", "data/features")) / "full_features.parquet"
        feature_manifest_path = Path(self.cfg["_project_root"]) / str((self.cfg.get("features") or {}).get("output_dir", "data/features")) / "feature_manifest.json"
        if not market_path.exists():
            logger.error("LabelAgent precheck failed: missing market_ohlcv.parquet")
            self._results["labels"] = False
            return False
        if not full_features_path.exists():
            logger.error("LabelAgent precheck failed: missing full_features.parquet")
            self._results["labels"] = False
            return False
        if not feature_manifest_path.exists():
            logger.error("LabelAgent precheck failed: missing feature_manifest.json")
            self._results["labels"] = False
            return False
        try:
            from scripts.verify_feature_run import validate_feature_outputs

            feature_failures = validate_feature_outputs(self.cfg)
            if feature_failures:
                for failure in feature_failures:
                    logger.error(f"Feature validation failed before labels: {failure}")
                self._results["labels"] = False
                return False
        except Exception as exc:
            logger.error(f"Feature validation precheck for labels crashed: {exc}")
            self._results["labels"] = False
            return False
        from agents.label_agent import LabelAgent
        agent = LabelAgent(self.cfg)
        success = agent.execute()
        if success:
            try:
                from scripts.verify_label_run import validate_label_outputs

                failures = validate_label_outputs(self.cfg)
                if failures:
                    for failure in failures:
                        logger.error(f"Label validation failed: {failure}")
                    success = False
            except Exception as exc:
                logger.error(f"Label validation crashed: {exc}")
                success = False
        self._results["labels"] = success
        return success

    def run_models(self, horizons: Optional[List[int]] = None) -> bool:
        """Run ModelAgent for all configured horizons."""
        feature_manifest = Path(self.cfg["_project_root"]) / "data/features/feature_manifest.json"
        label_matrix = Path(self.cfg["_project_root"]) / "data/labels/label_matrix.parquet"
        modeling_dataset = Path(self.cfg["_project_root"]) / "data/labels/modeling_dataset.parquet"
        if not feature_manifest.exists() or not label_matrix.exists() or not modeling_dataset.exists():
            logger.error("ModelAgent precheck failed: missing canonical label/feature artifacts")
            self._results["models"] = False
            return False
        try:
            from scripts.verify_label_run import validate_label_outputs

            label_failures = validate_label_outputs(self.cfg)
            if label_failures:
                for failure in label_failures:
                    logger.error(f"Label validation failed before models: {failure}")
                self._results["models"] = False
                return False
        except Exception as exc:
            logger.error(f"Label validation precheck for models crashed: {exc}")
            self._results["models"] = False
            return False
        from agents.model_agent import ModelAgent
        success = ModelAgent(self.cfg).execute()
        if success:
            try:
                from scripts.verify_model_run import validate_model_outputs
                failures = validate_model_outputs(self.cfg)
                if failures:
                    for failure in failures:
                        logger.error(f"Model validation failed: {failure}")
                    success = False
            except Exception as exc:
                logger.error(f"Model validation crashed: {exc}")
                success = False
        self._results["models"] = success
        return success

    def run_portfolio(self, model_name: str = "lightgbm", horizon: int = 7) -> bool:
        """Run PortfolioAgent after verified model outputs exist."""
        pred_path = Path(self.cfg["_project_root"]) / "data/predictions/model_predictions.parquet"
        board_path = Path(self.cfg["_project_root"]) / "data/predictions/model_leaderboard.parquet"
        if not pred_path.exists() or not board_path.exists():
            logger.error("PortfolioAgent precheck failed: missing canonical prediction artifacts")
            self._results["portfolio"] = False
            return False
        try:
            from scripts.verify_model_run import validate_model_outputs
            failures = validate_model_outputs(self.cfg)
            if failures:
                for failure in failures:
                    logger.error(f"Model validation failed before portfolio: {failure}")
                self._results["portfolio"] = False
                return False
        except Exception as exc:
            logger.error(f"Model validation precheck for portfolio crashed: {exc}")
            self._results["portfolio"] = False
            return False
        from agents.portfolio_agent import PortfolioAgent
        agent = PortfolioAgent(self.cfg)
        success = agent.execute()
        if success:
            try:
                from scripts.verify_portfolio_run import validate_portfolio_outputs
                failures = validate_portfolio_outputs(self.cfg)
                if failures:
                    for failure in failures:
                        logger.error(f"Portfolio validation failed: {failure}")
                    success = False
            except Exception as exc:
                logger.error(f"Portfolio validation crashed: {exc}")
                success = False
        self._results["portfolio"] = success
        return success

    def run_backtest(self) -> bool:
        """Run BacktestAgent."""
        allocations_path = Path(self.cfg["_project_root"]) / "data/allocations/allocations_from_predictions.parquet"
        if not allocations_path.exists():
            logger.error("BacktestAgent precheck failed: missing allocations_from_predictions.parquet")
            self._results["backtest"] = False
            return False
        try:
            from scripts.verify_portfolio_run import validate_portfolio_outputs
            failures = validate_portfolio_outputs(self.cfg)
            if failures:
                for failure in failures:
                    logger.error(f"Portfolio validation failed before backtest: {failure}")
                self._results["backtest"] = False
                return False
        except Exception as exc:
            logger.error(f"Portfolio validation precheck for backtest crashed: {exc}")
            self._results["backtest"] = False
            return False
        from agents.backtest_agent import BacktestAgent
        agent = BacktestAgent(self.cfg)
        success = agent.execute()
        if success:
            try:
                from scripts.verify_backtest_run import validate_backtest_outputs
                failures = validate_backtest_outputs(self.cfg)
                if failures:
                    for failure in failures:
                        logger.error(f"Backtest validation failed: {failure}")
                    success = False
            except Exception as exc:
                logger.error(f"Backtest validation crashed: {exc}")
                success = False
        self._results["backtest"] = success
        return success

    def run_full_pipeline(
        self,
        skip_universe: bool = False,
        skip_onchain: bool = False,
        snapshot_date: Optional[str] = None,
    ) -> Dict[str, bool]:
        """
        Run the complete pipeline from start to finish.
        Returns dict of stage -> success status.
        """
        start_time = time.time()
        logger.info("=" * 60)
        logger.info("CHF Full Pipeline Starting")
        logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
        logger.info("=" * 60)

        stages = [
            ("universe", lambda: self.run_universe(snapshot_date) if not skip_universe else True),
            ("market_data", self.run_market_data),
            ("onchain", lambda: self.run_onchain() if not skip_onchain else True),
            ("clean", self.run_clean),
            ("features", self.run_features),
            ("labels", self.run_labels),
            ("models", self.run_models),
            ("portfolio", self.run_portfolio),
            ("backtest", self.run_backtest),
        ]

        for stage_name, stage_fn in stages:
            logger.info(f"Running stage: {stage_name}")
            try:
                success = stage_fn()
                self._results[stage_name] = success
                status = "SUCCESS" if success else "FAILED"
                logger.info(f"Stage {stage_name}: {status}")
                if not success:
                    logger.error(f"Stopping pipeline after failed stage: {stage_name}")
                    break
            except Exception as e:
                logger.error(f"Stage {stage_name} crashed: {e}")
                self._results[stage_name] = False
                break

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"Pipeline complete in {elapsed:.1f}s")
        logger.info(f"Results: {self._results}")
        logger.info("=" * 60)

        return self._results

    def get_status(self) -> Dict[str, Any]:
        """Return current pipeline status."""
        return {
            "results": self._results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "all_success": all(self._results.values()) if self._results else False,
        }


def main():
    """CLI entry point for the pipeline runner."""
    setup_logging(level="INFO", json_output=False)

    parser = argparse.ArgumentParser(
        description="CHF Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline_runner.py --full          # Run full pipeline
  python pipeline_runner.py --stage universe  # Run single stage
  python pipeline_runner.py --stage market_data
  python pipeline_runner.py --stage features
  python pipeline_runner.py --stage models
  python pipeline_runner.py --stage backtest
        """,
    )
    parser.add_argument("--full", action="store_true", help="Run full pipeline")
    parser.add_argument(
        "--stage",
        choices=[
            "universe", "market_data", "onchain", "clean",
            "features", "labels", "models", "portfolio", "backtest"
        ],
        help="Run a single pipeline stage",
    )
    parser.add_argument(
        "--skip-universe", action="store_true",
        help="Skip universe update (use cached)"
    )
    parser.add_argument(
        "--skip-onchain", action="store_true",
        help="Skip on-chain data fetch (use cached)"
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Universe snapshot date (YYYY-MM format)"
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=7,
        help="Label horizon in days (default: 7)"
    )

    args = parser.parse_args()
    runner = PipelineRunner()

    if args.full:
        results = runner.run_full_pipeline(
            skip_universe=args.skip_universe,
            skip_onchain=args.skip_onchain,
            snapshot_date=args.snapshot_date,
        )
        success = all(results.values())
        sys.exit(0 if success else 1)
    elif args.stage:
        stage_map = {
            "universe": lambda: runner.run_universe(args.snapshot_date),
            "market_data": runner.run_market_data,
            "onchain": runner.run_onchain,
            "clean": runner.run_clean,
            "features": runner.run_features,
            "labels": runner.run_labels,
            "models": runner.run_models,
            "portfolio": lambda: runner.run_portfolio(horizon=args.horizon),
            "backtest": runner.run_backtest,
        }
        success = stage_map[args.stage]()
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
