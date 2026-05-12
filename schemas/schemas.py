"""
CHF Schema Definitions
Pydantic models for all major data structures in the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────
# Universe Schemas
# ─────────────────────────────────────────────

class AssetMetadata(BaseModel):
    """Metadata for a single crypto asset."""
    snapshot_date: datetime
    provider: str
    provider_asset_id: str
    coin_id: str
    symbol: str
    name: str
    market_cap_rank: int
    market_cap_usd: float
    volume_24h_usd: float
    price_usd: float
    is_stablecoin: bool = False
    is_wrapped: bool = False
    is_bridged: bool = False
    is_lst: bool = False
    is_synthetic_pegged: bool = False
    is_mature_365d: bool = False
    is_exchange_tradable: bool = False
    exchange: str = ""
    exchange_symbol: str = ""
    has_onchain_coverage: bool = False
    onchain_coverage_source: str = ""
    is_eligible: bool = False
    exclusion_reason: str = ""
    raw_category_tags: List[str] = Field(default_factory=list)
    snapshot_id: str
    source: str
    created_at_utc: datetime


class UniverseSnapshot(BaseModel):
    """A monthly universe snapshot."""
    snapshot_id: str
    snapshot_date: datetime
    assets: List[AssetMetadata]
    config_hash: str
    run_id: str
    created_at: datetime


# ─────────────────────────────────────────────
# Market Data Schemas
# ─────────────────────────────────────────────

class OHLCVBar(BaseModel):
    """A single OHLCV bar."""
    exchange: str
    exchange_symbol: str
    symbol: str
    date_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    fetched_at_utc: datetime
    snapshot_id: str
    is_forward_filled: bool = False
    is_incomplete_dropped: bool = False


class MarketDataQA(BaseModel):
    """QA report for market data."""
    symbol: str
    total_bars: int
    missing_bars: int
    duplicate_bars: int
    gap_count: int
    first_date: Optional[datetime]
    last_date: Optional[datetime]
    coverage_pct: float
    has_incomplete_candles: bool
    utc_validated: bool
    run_id: str
    checked_at: datetime


# ─────────────────────────────────────────────
# On-Chain Data Schemas
# ─────────────────────────────────────────────

class OnChainMetric(BaseModel):
    """A single on-chain metric observation."""
    symbol: str
    date_ts: datetime
    metric_name: str
    metric_value: Optional[float]
    source: str
    network: Optional[str] = None
    retrieved_at: datetime
    snapshot_id: str
    is_proxy: bool = False
    proxy_definition: Optional[str] = None


class OnChainCoverage(BaseModel):
    """Coverage report for on-chain data."""
    symbol: str
    metric_name: str
    available_days: int
    total_days: int
    coverage_pct: float
    source: str
    run_id: str


# ─────────────────────────────────────────────
# Feature Store Schemas
# ─────────────────────────────────────────────

class FeatureDefinition(BaseModel):
    """Machine-readable definition of a feature."""
    feature_name: str
    family: str  # market, on_chain, cross_sectional
    formula: str
    parameters: Dict[str, float] = Field(default_factory=dict)
    data_sources: List[str] = Field(default_factory=list)
    is_proxy: bool = False
    proxy_notes: Optional[str] = None
    version: str = "v1"
    created_at: datetime


class FeatureRow(BaseModel):
    """A row in the feature store."""
    symbol: str
    date_ts: datetime
    feature_name: str
    feature_value: Optional[float]
    feature_version: str
    snapshot_id: str
    run_id: str


# ─────────────────────────────────────────────
# Label Schemas
# ─────────────────────────────────────────────

class LabelRow(BaseModel):
    """A forward-return label."""
    symbol: str
    date_ts: datetime
    horizon_days: int
    label_value: Optional[float]
    label_type: str = "log_return"
    is_complete: bool
    snapshot_id: str
    run_id: str


# ─────────────────────────────────────────────
# Prediction Schemas
# ─────────────────────────────────────────────

class PredictionRow(BaseModel):
    """A model prediction."""
    symbol: str
    date_ts: datetime
    horizon_days: int
    predicted_return: float
    model_name: str
    model_version: str
    feature_version: str
    snapshot_id: str
    run_id: str
    mlflow_run_id: Optional[str] = None


# ─────────────────────────────────────────────
# Allocation Schemas
# ─────────────────────────────────────────────

class AllocationRow(BaseModel):
    """A portfolio weight allocation."""
    symbol: str
    date_ts: datetime
    weight: float
    rank: int
    signal_score: float
    strategy: str
    top_k: int
    run_id: str
    snapshot_id: str

    @field_validator("weight")
    @classmethod
    def weight_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"Weight must be in [0, 1], got {v}")
        return v


class TransactionLog(BaseModel):
    """A single portfolio transaction."""
    date_ts: datetime
    symbol: str
    action: str  # BUY, SELL, HOLD
    weight_before: float
    weight_after: float
    turnover: float
    cost_bps: float
    run_id: str


# ─────────────────────────────────────────────
# Backtest Schemas
# ─────────────────────────────────────────────

class BacktestSummary(BaseModel):
    """Summary statistics for a backtest run."""
    run_id: str
    strategy: str
    top_k: int
    cost_bps: float
    rebalance_freq: str
    start_date: datetime
    end_date: datetime
    cagr: float
    annualized_vol: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    avg_turnover: float
    total_return: float
    benchmark: str
    benchmark_cagr: float
    benchmark_sharpe: float
    snapshot_id: str
    created_at: datetime


# ─────────────────────────────────────────────
# Agent Run Registry
# ─────────────────────────────────────────────

class AgentRunRecord(BaseModel):
    """Registry entry for an agent run."""
    run_id: str
    agent_name: str
    status: str  # PENDING, RUNNING, SUCCESS, FAILED
    started_at: datetime
    completed_at: Optional[datetime] = None
    config_hash: str
    snapshot_id: Optional[str] = None
    error_message: Optional[str] = None
    output_paths: Dict[str, str] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)
