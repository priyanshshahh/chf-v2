"""Internal paper-trading simulator broker.

Maintains a virtual cash + positions book, fills rebalances at the provided
reference prices with a configurable proportional cost (default 20 bps to
match BacktestAgent), and persists state as JSON so books survive restarts.

This broker never contacts any exchange. It is the CHF default execution
venue for forward (out-of-sample) validation of research strategies.

Short / margin / perp support (OPT-IN)
--------------------------------------
By default a book is long-only spot and behaves byte-for-byte as before. A
book may opt in to shorting by constructing the broker with
``allow_short=True`` (and a ``margin_requirement``, e.g. 0.5). Then:

* Spot positions may be negative (short). Shorting credits cash on the sale
  and is marked ``qty * price`` (a fall in price raises equity), symmetric to
  the long case.
* ``instrument_type="perp"`` positions are linear perpetual futures: opening
  one moves **no principal cash** (only the fee); their value is the
  cash-settled mark-to-market P&L ``qty * (mark_price - entry_price)``, and
  they accrue funding each mark.

Funding sign convention (linear perp, OKX/standard)
---------------------------------------------------
When ``funding_rate > 0`` longs pay shorts. The cash flow *received* by a perp
position each funding period is ``-qty * price * funding_rate``:

    * short perp (qty < 0), funding_rate > 0  ->  receives funding (cash in)
    * long  perp (qty > 0), funding_rate > 0  ->  pays funding     (cash out)
    * signs reverse when funding_rate < 0.

Margin rule
-----------
``margin_used = gross_short_notional * margin_requirement`` where
``gross_short_notional`` is the summed absolute notional of every short leg
(short spot + short perp). A rebalance that would leave
``equity < margin_used`` raises ``margin_violation``; a rebalance that would
leave ``equity < 0`` raises ``negative_equity``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .broker import BrokerSnapshot, ExecutionBroker, Fill

STATE_FILENAME = "state.json"
DEFAULT_COST_BPS = 20.0
# Trades below this notional are skipped to avoid dust churn.
MIN_TRADE_NOTIONAL_USD = 1.0

SPOT = "spot"
PERP = "perp"


def _side_for(old_qty: float, delta_qty: float) -> str:
    """Map a position delta to a trade side (buy/sell/short/cover).

    Long-only books (old_qty >= 0) only ever produce "buy"/"sell", identical
    to the historical behaviour.
    """
    if delta_qty > 0:
        return "cover" if old_qty < -1e-12 else "buy"
    return "short" if old_qty < 1e-12 else "sell"


def _update_perp(
    old_qty: float, entry: float, delta_qty: float, price: float
) -> Tuple[float, float, float]:
    """Return (new_qty, new_entry, realized_pnl) for a perp fill.

    Adding in the current direction updates the weighted-average entry;
    reducing/closing/flipping realizes P&L on the closed portion into cash and
    leaves (or resets) the entry price. Keeps ``nav`` continuous across fills.
    """
    new_qty = old_qty + delta_qty
    if abs(old_qty) < 1e-15:
        return new_qty, price, 0.0
    if (old_qty > 0) == (delta_qty > 0):
        # increasing magnitude in the same direction -> average in
        new_entry = (entry * old_qty + price * delta_qty) / new_qty
        return new_qty, new_entry, 0.0
    # opposite direction: reduce / close / flip
    if abs(delta_qty) <= abs(old_qty) + 1e-15:
        closed = -delta_qty  # signed portion of the old position being closed
        realized = closed * (price - entry)
        new_entry = entry if abs(new_qty) > 1e-15 else price
        return new_qty, new_entry, realized
    # flip: realize the entire old position, open the residual at ``price``
    realized = old_qty * (price - entry)
    return new_qty, price, realized


class InternalSimulatorBroker(ExecutionBroker):
    def __init__(
        self,
        book_dir: Path,
        starting_cash: float = 100_000.0,
        cost_bps: float = DEFAULT_COST_BPS,
        allow_short: bool = False,
        margin_requirement: Optional[float] = None,
    ) -> None:
        self.book_dir = Path(book_dir)
        self.book_dir.mkdir(parents=True, exist_ok=True)
        self.cost_bps = float(cost_bps)
        self.allow_short = bool(allow_short)
        # Default to 0.5 (2x) when shorting is enabled but unspecified.
        self.margin_requirement = (
            float(margin_requirement)
            if margin_requirement is not None
            else (0.5 if allow_short else 0.0)
        )
        self._state_path = self.book_dir / STATE_FILENAME
        if self._state_path.exists():
            state = json.loads(self._state_path.read_text())
            self._cash = float(state["cash"])
            self._positions = {str(k): float(v) for k, v in state.get("positions", {}).items()}
            self._last_rebalance = state.get("last_rebalance")
            # perps: symbol -> {"qty": float, "entry": float}; absent for old books
            self._perps: Dict[str, Dict[str, float]] = {
                str(k): {"qty": float(v["qty"]), "entry": float(v["entry"])}
                for k, v in (state.get("perps") or {}).items()
            }
            self._last_funding_date = state.get("last_funding_date")
        else:
            self._cash = float(starting_cash)
            self._positions = {}
            self._last_rebalance = None
            self._perps = {}
            self._last_funding_date = None
            self._save()

    # -- persistence -------------------------------------------------------

    def _save(self) -> None:
        payload: Dict[str, object] = {
            "cash": self._cash,
            "positions": self._positions,
            "last_rebalance": self._last_rebalance,
        }
        # Only emit the short/perp keys when they carry content, so long-only
        # books keep byte-for-byte identical state.json.
        if self._perps:
            payload["perps"] = self._perps
        if self._last_funding_date is not None:
            payload["last_funding_date"] = self._last_funding_date
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self._state_path)

    # -- ExecutionBroker ---------------------------------------------------

    def get_cash(self) -> float:
        return float(self._cash)

    def get_positions(self) -> Dict[str, float]:
        return dict(self._positions)

    def get_perp_positions(self) -> Dict[str, float]:
        return {s: p["qty"] for s, p in self._perps.items()}

    def priceable_symbols(self) -> set:
        """Underlying symbols that must be marked (spot holdings + perp legs)."""
        return set(self._positions) | set(self._perps)

    def instrument_type_of(self, symbol: str) -> str:
        return PERP if symbol in self._perps else SPOT

    @property
    def last_rebalance(self) -> Optional[str]:
        return self._last_rebalance

    @property
    def last_funding_date(self) -> Optional[str]:
        return self._last_funding_date

    def _price(self, prices: Dict[str, float], symbol: str) -> float:
        price = prices.get(symbol)
        if price is None or price <= 0:
            raise ValueError(f"missing_price:{symbol}")
        return float(price)

    def mark_to_market(self, prices: Dict[str, float], trade_date: str) -> BrokerSnapshot:
        positions_value = 0.0
        gross_short = 0.0
        for symbol, qty in self._positions.items():
            price = self._price(prices, symbol)
            positions_value += qty * price
            if qty < 0:
                gross_short += abs(qty) * price
        perp_pnl = 0.0
        for symbol, pos in self._perps.items():
            price = self._price(prices, symbol)
            qty = pos["qty"]
            perp_pnl += qty * (price - pos["entry"])
            if qty < 0:
                gross_short += abs(qty) * price
        positions_value += perp_pnl
        return BrokerSnapshot(
            date=trade_date,
            cash=self._cash,
            positions=dict(self._positions),
            positions_value=positions_value,
            perp_positions=self.get_perp_positions(),
            perp_pnl=perp_pnl,
            gross_short_notional=gross_short,
            margin_used=gross_short * self.margin_requirement,
        )

    # -- funding accrual (perp books) --------------------------------------

    def accrue_perp_funding(
        self, funding_by_symbol: Dict[str, float], prices: Dict[str, float], trade_date: str
    ) -> float:
        """Accrue one day of perp funding into cash (idempotent per date).

        ``funding_by_symbol`` maps symbol -> that day's funding rate (sum of
        the day's funding intervals). Symbols without a rate are skipped
        (never fabricate funding). Returns the net funding cash flow.
        """
        if not self._perps:
            return 0.0
        if self._last_funding_date is not None and trade_date <= self._last_funding_date:
            return 0.0  # already accrued for this (or a later) date
        total = 0.0
        for symbol, pos in self._perps.items():
            rate = funding_by_symbol.get(symbol)
            price = prices.get(symbol)
            if rate is None or price is None or price <= 0:
                continue
            # short perp (qty<0) receives when rate>0; long pays.
            cash_flow = -pos["qty"] * float(price) * float(rate)
            self._cash += cash_flow
            total += cash_flow
        self._last_funding_date = trade_date
        self._save()
        return float(total)

    # -- rebalance ---------------------------------------------------------

    def submit_target_weights(
        self,
        target_weights: Dict[str, float],
        prices: Dict[str, float],
        trade_date: str,
    ) -> List[Fill]:
        """Long-only spot rebalance (unchanged public contract).

        Positive weights only; symbols absent are liquidated; weights must sum
        to <= 1.0. Kept exactly compatible with the 10 live books.
        """
        weights = {s: float(w) for s, w in target_weights.items() if float(w) > 0.0}
        total = sum(weights.values())
        if total > 1.0 + 1e-6:
            raise ValueError(f"weights_sum_gt_1:{total:.6f}")
        missing = [s for s in weights if prices.get(s) is None or prices[s] <= 0]
        if missing:
            raise ValueError(f"missing_price:{','.join(sorted(missing))}")
        legs = [(s, SPOT, w) for s, w in weights.items()]
        return self._apply_legs(legs, prices, trade_date)

    def submit_target_legs(
        self,
        legs: Sequence[Tuple[str, str, float]],
        prices: Dict[str, float],
        trade_date: str,
    ) -> List[Fill]:
        """Signed / multi-instrument rebalance (short-capable books).

        ``legs`` is a sequence of ``(symbol, instrument_type, weight)`` with
        *signed* weight (fraction of NAV; negative = short). Requires the book
        to have been opened with ``allow_short=True``.
        """
        if not self.allow_short:
            raise ValueError("short_legs_require_allow_short")
        cleaned = [
            (str(s).upper(), str(it).lower(), float(w))
            for s, it, w in legs
            if abs(float(w)) > 0.0 and str(s).upper() != "CASH"
        ]
        for _, it, _ in cleaned:
            if it not in (SPOT, PERP):
                raise ValueError(f"bad_instrument_type:{it}")
        return self._apply_legs(cleaned, prices, trade_date)

    def _apply_legs(
        self,
        legs: Sequence[Tuple[str, str, float]],
        prices: Dict[str, float],
        trade_date: str,
    ) -> List[Fill]:
        snapshot = self.mark_to_market(prices, trade_date)
        nav = snapshot.nav
        if nav <= 0:
            raise ValueError(f"non_positive_equity:{nav:.2f}")

        # target notional per (symbol, instrument_type)
        targets: Dict[Tuple[str, str], float] = {}
        for symbol, itype, weight in legs:
            targets[(symbol, itype)] = targets.get((symbol, itype), 0.0) + nav * weight

        # current notionals for everything held or targeted
        keys = set(targets)
        keys |= {(s, SPOT) for s in self._positions}
        keys |= {(s, PERP) for s in self._perps}

        deltas: Dict[Tuple[str, str], float] = {}
        for symbol, itype in keys:
            price = self._price(prices, symbol)
            if itype == SPOT:
                current = self._positions.get(symbol, 0.0) * price
            else:
                pos = self._perps.get(symbol)
                current = (pos["qty"] * price) if pos else 0.0
            deltas[(symbol, itype)] = targets.get((symbol, itype), 0.0) - current

        fills: List[Fill] = []
        # Reductions/sells first (most negative delta) so cash funds the buys.
        for (symbol, itype) in sorted(deltas, key=lambda k: (deltas[k], k[0], k[1])):
            delta_notional = deltas[(symbol, itype)]
            if abs(delta_notional) < MIN_TRADE_NOTIONAL_USD:
                continue
            price = float(prices[symbol])
            delta_qty = delta_notional / price
            cost = abs(delta_notional) * self.cost_bps / 10_000.0
            if itype == SPOT:
                old_qty = self._positions.get(symbol, 0.0)
                # spot: principal moves cash (buy -> out, sell/short -> in)
                self._cash -= delta_notional + cost
                new_qty = old_qty + delta_qty
                if abs(new_qty) < 1e-12:
                    self._positions.pop(symbol, None)
                else:
                    self._positions[symbol] = new_qty
            else:
                pos = self._perps.get(symbol, {"qty": 0.0, "entry": price})
                old_qty = pos["qty"]
                new_qty, new_entry, realized = _update_perp(
                    old_qty, pos["entry"], delta_qty, price
                )
                # perp: no principal cash flow; realize closed P&L, pay the fee
                self._cash += realized - cost
                if abs(new_qty) < 1e-12:
                    self._perps.pop(symbol, None)
                else:
                    self._perps[symbol] = {"qty": new_qty, "entry": new_entry}
            fills.append(
                Fill(
                    date=trade_date,
                    symbol=symbol,
                    side=_side_for(old_qty, delta_qty),
                    qty=delta_qty,
                    price=price,
                    notional=delta_notional,
                    cost_usd=cost,
                    instrument_type=itype,
                )
            )

        # -- solvency / margin guards --
        if not self.allow_short:
            if self._cash < -1e-6:
                raise ValueError(f"negative_cash_after_rebalance:{self._cash:.2f}")
        else:
            post = self.mark_to_market(prices, trade_date)
            if post.nav < -1e-6:
                raise ValueError(f"negative_equity:{post.nav:.2f}")
            if post.nav + 1e-6 < post.margin_used:
                raise ValueError(
                    f"margin_violation:equity={post.nav:.2f}<margin={post.margin_used:.2f}"
                )
        self._last_rebalance = trade_date
        self._save()
        return fills
