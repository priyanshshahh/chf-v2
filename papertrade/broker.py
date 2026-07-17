"""Execution broker interface for CHF paper trading.

All brokers are *virtual or paper only*. Nothing in this package may place
real-money orders. The interface is deliberately small so the internal
simulator and the Alpaca paper adapter are interchangeable in the engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Fill:
    """A single executed (virtual) trade.

    ``side`` covers the long-only cases ("buy"/"sell") and the short/margin
    cases ("short" = open/increase a short, "cover" = reduce/close a short).
    ``instrument_type`` is "spot" (default, backward-compatible with the 10
    long-only books) or "perp" (a linear perpetual future — cash-settled
    mark-to-market P&L, accrues funding).
    """

    date: str  # YYYY-MM-DD
    symbol: str
    side: str  # "buy" | "sell" | "short" | "cover"
    qty: float
    price: float
    notional: float
    cost_usd: float
    reason: str = "rebalance"
    instrument_type: str = "spot"


@dataclass
class BrokerSnapshot:
    """Mark-to-market snapshot of a book.

    ``positions_value`` folds in perp mark-to-market P&L so the identity
    ``nav = cash + positions_value`` holds for both long-only spot books and
    short/margin books. ``perp_pnl``/``gross_short_notional``/``margin_used``
    default to 0.0 and stay 0.0 for the long-only books (backward-compatible).
    """

    date: str  # YYYY-MM-DD
    cash: float
    positions: Dict[str, float] = field(default_factory=dict)
    positions_value: float = 0.0
    perp_positions: Dict[str, float] = field(default_factory=dict)
    perp_pnl: float = 0.0
    gross_short_notional: float = 0.0
    margin_used: float = 0.0

    @property
    def nav(self) -> float:
        return float(self.cash + self.positions_value)


class ExecutionBroker(ABC):
    """Minimal broker contract used by the paper-trading engine."""

    @abstractmethod
    def get_cash(self) -> float:
        """Current settled cash in USD."""

    @abstractmethod
    def get_positions(self) -> Dict[str, float]:
        """Current holdings as symbol -> quantity."""

    @abstractmethod
    def submit_target_weights(
        self,
        target_weights: Dict[str, float],
        prices: Dict[str, float],
        trade_date: str,
    ) -> List[Fill]:
        """Rebalance the book toward target weights at the given prices.

        Weights are fractions of NAV; symbols absent from ``target_weights``
        are liquidated. Returns the fills executed (may be empty).
        """

    @abstractmethod
    def mark_to_market(self, prices: Dict[str, float], trade_date: str) -> BrokerSnapshot:
        """Value the book at the given prices without trading."""
