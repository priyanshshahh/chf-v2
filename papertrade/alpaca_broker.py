"""Alpaca *paper* trading adapter (optional).

Thin ExecutionBroker implementation over alpaca-py's TradingClient, hard-wired
to the paper base URL. It never trades real money: ``paper=True`` and
``https://paper-api.alpaca.markets`` are non-configurable.

alpaca-py is an optional dependency: it is imported lazily inside methods, and
missing package/keys raise :class:`AlpacaUnavailableError` with install
instructions instead of ImportError at import time.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from .broker import BrokerSnapshot, ExecutionBroker, Fill

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
UNAVAILABLE_MSG = (
    "pip install alpaca-py and set ALPACA_API_KEY/ALPACA_SECRET_KEY in .env "
    "for paper trading"
)
# Trades below this notional are skipped to avoid dust churn.
MIN_TRADE_NOTIONAL_USD = 1.0


class AlpacaUnavailableError(RuntimeError):
    """alpaca-py is not installed or API keys are not configured."""


def _to_alpaca_symbol(symbol: str) -> str:
    """Map a plain crypto symbol (BTC) to Alpaca's pair form (BTC/USD)."""
    symbol = symbol.upper()
    return symbol if "/" in symbol else f"{symbol}/USD"


def _from_alpaca_symbol(symbol: str) -> str:
    """Map Alpaca's BTC/USD or BTCUSD back to the plain symbol (BTC)."""
    symbol = symbol.upper()
    if "/" in symbol:
        return symbol.split("/", 1)[0]
    if symbol.endswith("USD"):
        return symbol[:-3]
    return symbol


class AlpacaPaperBroker(ExecutionBroker):
    """Paper-only Alpaca broker. Never touches the live trading endpoint."""

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self._client = None

    # -- plumbing ------------------------------------------------------------

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key or not self.secret_key:
            raise AlpacaUnavailableError(UNAVAILABLE_MSG)
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as exc:
            raise AlpacaUnavailableError(UNAVAILABLE_MSG) from exc
        # paper=True + explicit paper URL: real trading is not supported here.
        self._client = TradingClient(
            self.api_key,
            self.secret_key,
            paper=True,
            url_override=PAPER_BASE_URL,
        )
        return self._client

    # -- ExecutionBroker -------------------------------------------------------

    def get_cash(self) -> float:
        account = self._get_client().get_account()
        return float(account.cash)

    def get_positions(self) -> Dict[str, float]:
        positions = self._get_client().get_all_positions()
        out: Dict[str, float] = {}
        for pos in positions:
            qty = float(pos.qty)
            if abs(qty) > 0:
                out[_from_alpaca_symbol(str(pos.symbol))] = qty
        return out

    def mark_to_market(self, prices: Dict[str, float], trade_date: str) -> BrokerSnapshot:
        cash = self.get_cash()
        positions = self.get_positions()
        positions_value = 0.0
        for symbol, qty in positions.items():
            price = prices.get(symbol)
            if price is None or price <= 0:
                raise ValueError(f"missing_price:{symbol}")
            positions_value += qty * float(price)
        return BrokerSnapshot(
            date=trade_date,
            cash=cash,
            positions=positions,
            positions_value=positions_value,
        )

    def submit_target_weights(
        self,
        target_weights: Dict[str, float],
        prices: Dict[str, float],
        trade_date: str,
    ) -> List[Fill]:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        weights = {s.upper(): float(w) for s, w in target_weights.items() if float(w) > 0.0}
        total = sum(weights.values())
        if total > 1.0 + 1e-6:
            raise ValueError(f"weights_sum_gt_1:{total:.6f}")
        missing = [s for s in weights if prices.get(s) is None or prices[s] <= 0]
        if missing:
            raise ValueError(f"missing_price:{','.join(sorted(missing))}")

        client = self._get_client()
        snapshot = self.mark_to_market(prices, trade_date)
        nav = snapshot.nav
        positions = snapshot.positions

        deltas: Dict[str, float] = {}
        for symbol in sorted(set(positions) | set(weights)):
            price = prices.get(symbol)
            if price is None or price <= 0:
                raise ValueError(f"missing_price:{symbol}")
            current_notional = positions.get(symbol, 0.0) * float(price)
            deltas[symbol] = nav * weights.get(symbol, 0.0) - current_notional

        fills: List[Fill] = []
        # Sells first (most negative delta first) so cash funds the buys.
        for symbol in sorted(deltas, key=lambda s: deltas[s]):
            delta_notional = deltas[symbol]
            if abs(delta_notional) < MIN_TRADE_NOTIONAL_USD:
                continue
            side = OrderSide.BUY if delta_notional > 0 else OrderSide.SELL
            order = MarketOrderRequest(
                symbol=_to_alpaca_symbol(symbol),
                notional=round(abs(delta_notional), 2),
                side=side,
                time_in_force=TimeInForce.GTC,
            )
            try:
                client.submit_order(order)
            except Exception as exc:  # noqa: BLE001 - surface but don't mask book state
                logger.warning("alpaca paper order failed for %s: %s", symbol, exc)
                continue
            price = float(prices[symbol])
            fills.append(
                Fill(
                    date=trade_date,
                    symbol=symbol,
                    side="buy" if delta_notional > 0 else "sell",
                    qty=delta_notional / price,
                    price=price,
                    notional=delta_notional,
                    cost_usd=0.0,  # Alpaca crypto paper fills carry no explicit fee here
                )
            )
        return fills
