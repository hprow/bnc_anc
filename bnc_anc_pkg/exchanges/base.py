from abc import ABC, abstractmethod

class ExchangeClient(ABC):
    name: str = "abstract"
    market: str = "spot"

    @abstractmethod
    def symbol_from_base(self, base: str) -> str:
        ...

    @abstractmethod
    async def trade(self, *, symbol: str, side: str, notional: float, tp_pct: float, sl_pct: float, leverage: int):
        ...

    async def close(self):
        pass
