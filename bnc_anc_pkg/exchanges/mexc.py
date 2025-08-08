import asyncio
import time
import hmac
import hashlib
import aiohttp
from typing import Dict, Any, Optional
from .base import ExchangeClient

MEXC_BASE = "https://api.mexc.com"

class MexcSpotClient(ExchangeClient):
    name = "mexc"
    market = "spot"

    def __init__(self, key: str, secret: str):
        self.key = key
        self.secret = secret.encode()
        to = aiohttp.ClientTimeout(total=3.0, connect=0.5, sock_connect=0.5, sock_read=2.0)
        self.session = aiohttp.ClientSession(timeout=to)

    async def close(self):
        await self.session.close()

    def symbol_from_base(self, base: str) -> str:
        return f"{base.upper()}USDT"

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sig = hmac.new(self.secret, qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def _req(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        params = params or {}
        url = MEXC_BASE + path
        headers = {"X-MEXC-APIKEY": self.key}
        async with self.session.request(method, url, params=params, headers=headers) as r:
            txt = await r.text()
            if r.status < 200 or r.status >= 300:
                raise RuntimeError(f"{r.status} {r.reason}. Body={txt}")
            try:
                return await r.json()
            except Exception:
                return txt

    async def trade(self, *, symbol: str, side: str, notional: float, tp_pct: float, sl_pct: float, leverage: int):
        ts = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quoteOrderQty": str(notional),
            "timestamp": ts,
        }
        self._sign(params)
        order = await self._req("POST", "/api/v3/order", params)
        # in real impl we'd wait for fill and then place tp/sl orders using average price
        await asyncio.sleep(0)
        return order
