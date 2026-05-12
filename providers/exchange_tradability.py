from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from configs.logging_config import get_logger


logger = get_logger("providers.exchange_tradability")


class ExchangeTradabilityProvider:
    """Loads Coinbase/Kraken markets once, caches them, and checks USD spot pairs."""

    def __init__(
        self,
        cache_dir: Path | str,
        live_api_enabled: bool,
        force_refresh: bool = False,
        exchanges: Optional[Iterable[str]] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) / "tradability"
        self.live_api_enabled = live_api_enabled
        self.force_refresh = force_refresh
        self.exchanges = list(exchanges or ["coinbase", "kraken"])
        self._markets: Dict[str, Dict[str, dict]] = {}

    def _cache_path(self, exchange: str) -> Path:
        return self.cache_dir / f"{exchange}_markets.json"

    def load_markets(self, exchange: str) -> Dict[str, dict]:
        if exchange in self._markets:
            return self._markets[exchange]
        path = self._cache_path(exchange)
        if path.exists() and not self.force_refresh:
            with open(path, "r") as f:
                markets = json.load(f)
            self._markets[exchange] = markets
            return markets
        if not self.live_api_enabled:
            raise FileNotFoundError(
                f"Missing tradability cache for {exchange} and live_api_enabled=false"
            )

        import ccxt

        exchange_cls = getattr(ccxt, exchange)
        ex = exchange_cls({"enableRateLimit": True})
        markets = ex.load_markets()
        serializable = {symbol: {"symbol": symbol} for symbol in markets.keys()}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(serializable, f, indent=2, sort_keys=True)
        self._markets[exchange] = serializable
        return serializable

    def check_symbol(self, symbol: str) -> Tuple[bool, str, str]:
        candidates = [f"{symbol.upper()}/USD", f"{symbol.upper()}/USDC"]
        for exchange in self.exchanges:
            try:
                markets = self.load_markets(exchange)
            except Exception as exc:
                logger.warning("Failed to load %s markets: %s", exchange, exc)
                continue
            for candidate in candidates:
                if candidate in markets:
                    return True, exchange, candidate
        return False, "", ""
