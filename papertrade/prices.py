"""Reference price fetching for paper trading.

Waterfall:
1. CoinMarketCap ``/v2/cryptocurrency/quotes/latest`` (needs ``CMC_API_KEY``)
2. CoinGecko top-N markets snapshot (keyless)

Both return USD prices keyed by upper-case symbol. Providers never fabricate
prices: symbols they cannot price are simply absent from the result, and the
engine decides how to handle gaps.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List

logger = logging.getLogger(__name__)


def fetch_prices_cmc(symbols: Iterable[str]) -> Dict[str, float]:
    """Latest USD quotes from CMC Pro. Raises on transport/auth errors."""
    from src.cmc.client import CMCClient, load_env

    load_env()
    client = CMCClient()
    wanted = sorted({s.upper() for s in symbols})
    if not wanted:
        return {}
    payload = client.get(
        "/v2/cryptocurrency/quotes/latest",
        params={"symbol": ",".join(wanted), "convert": "USD"},
    )
    out: Dict[str, float] = {}
    data = payload.get("data", {})
    for symbol, entries in data.items():
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            try:
                price = float(entry["quote"]["USD"]["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if price > 0 and symbol.upper() not in out:
                out[symbol.upper()] = price
    return out


def fetch_prices_coingecko(symbols: Iterable[str], top_n: int = 500) -> Dict[str, float]:
    """Keyless fallback: match symbols against CoinGecko's top-N by mcap."""
    from providers.coingecko import CoinGeckoProvider

    wanted = {s.upper() for s in symbols}
    provider = CoinGeckoProvider()
    rows = provider.get_top_coins_by_market_cap(top_n=top_n)
    out: Dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        price = row.get("price_usd", row.get("current_price"))
        if symbol in wanted and symbol not in out and price is not None and float(price) > 0:
            out[symbol] = float(price)
    return out


def fetch_prices(symbols: Iterable[str]) -> Dict[str, float]:
    """Fetch USD prices via the provider waterfall; best-effort merge."""
    wanted: List[str] = sorted({s.upper() for s in symbols})
    if not wanted:
        return {}
    prices: Dict[str, float] = {}
    cmc_prices: Dict[str, float] = {}
    try:
        cmc_prices = fetch_prices_cmc(wanted)
        prices.update(cmc_prices)
    except Exception as exc:  # noqa: BLE001 - provider failures must not kill the run
        logger.warning("papertrade prices: CMC failed (%s); falling back to CoinGecko", exc)
    try:
        gecko = fetch_prices_coingecko(wanted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("papertrade prices: CoinGecko failed (%s)", exc)
        gecko = {}
    for symbol, price in gecko.items():
        prices.setdefault(symbol, price)
    # Symbol collisions across providers (e.g. TON = Toncoin vs Tokamak
    # Network) can silently price the wrong asset. When two sources disagree
    # wildly, trust neither.
    for symbol in sorted(set(cmc_prices) & set(gecko)):
        a, b = cmc_prices[symbol], gecko[symbol]
        if max(a, b) / max(min(a, b), 1e-12) > 1.5:
            prices.pop(symbol, None)
            logger.warning(
                "papertrade prices: provider disagreement for %s (cmc=%s coingecko=%s); dropping",
                symbol, a, b,
            )
    still_missing = [s for s in wanted if s not in prices]
    if still_missing:
        logger.warning("papertrade prices: no price for %s", ",".join(still_missing))
    return prices
