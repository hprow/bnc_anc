import asyncio
import time
import hmac
import hashlib
import aiohttp
from typing import Dict, Any, Optional
from .base import ExchangeClient

MEXC_BASE = "https://api.mexc.com"


def _calc_tp_sl(ref_price: float, side: str, tp_pct: float, sl_pct: float) -> tuple[float, float]:
    s = side.lower()
    if s == "buy":
        return ref_price * (1 + tp_pct / 100.0), ref_price * (1 - sl_pct / 100.0)
    if s == "sell":
        return ref_price * (1 - tp_pct / 100.0), ref_price * (1 + sl_pct / 100.0)
    raise ValueError("side must be 'buy' or 'sell'")


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
        oid = order.get("orderId")
        qty = float(order.get("executedQty") or 0)
        price = float(
            order.get("avgPrice")
            or (float(order.get("cummulativeQuoteQty") or 0) / qty if qty else 0)
        )
        if order.get("status") != "FILLED" or not price:
            for _ in range(10):
                await asyncio.sleep(0.1)
                qs = {"symbol": symbol, "orderId": oid, "timestamp": int(time.time() * 1000)}
                self._sign(qs)
                info = await self._req("GET", "/api/v3/order", qs)
                if info.get("status") == "FILLED":
                    qty = float(info.get("executedQty") or 0)
                    price = float(
                        info.get("avgPrice")
                        or (
                            float(info.get("cummulativeQuoteQty") or 0) / qty if qty else 0
                        )
                    )
                    break
            if not price:
                raise RuntimeError("entry price not found for order")

        tp_price, sl_price = _calc_tp_sl(price, side, tp_pct, sl_pct)
        opp_side = "SELL" if side.lower() == "buy" else "BUY"
        qty_str = str(qty)

        tp_params = {
            "symbol": symbol,
            "side": opp_side,
            "type": "TAKE_PROFIT_LIMIT",
            "quantity": qty_str,
            "price": f"{tp_price}",
            "stopPrice": f"{tp_price}",
            "timeInForce": "GTC",
            "timestamp": int(time.time() * 1000),
        }
        self._sign(tp_params)
        await self._req("POST", "/api/v3/order", tp_params)

        sl_params = {
            "symbol": symbol,
            "side": opp_side,
            "type": "STOP_LOSS_LIMIT",
            "quantity": qty_str,
            "price": f"{sl_price}",
            "stopPrice": f"{sl_price}",
            "timeInForce": "GTC",
            "timestamp": int(time.time() * 1000),
        }
        self._sign(sl_params)
        await self._req("POST", "/api/v3/order", sl_params)
        return {"tp": tp_price, "sl": sl_price}
