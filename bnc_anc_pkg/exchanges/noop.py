import asyncio
from .base import ExchangeClient

class NoOpExchange(ExchangeClient):
    name = "noop"
    market = "futures"

    def symbol_from_base(self, base: str) -> str:
        return base

    async def trade(self, *, symbol: str, side: str, notional: float, tp_pct: float, sl_pct: float, leverage: int):
        await asyncio.sleep(0)
        return {"symbol": symbol, "side": side, "notional": notional}
