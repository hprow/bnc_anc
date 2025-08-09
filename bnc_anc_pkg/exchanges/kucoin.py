import json, hmac, hashlib, time, uuid, base64, asyncio, contextlib
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import aiohttp
from urllib.parse import urlencode
from .base import ExchangeClient
from ..decision import BTC_ALIAS

KC_BASE = "https://api-futures.kucoin.com"
ST_ORDERS_PATH = "/api/v1/st-orders"
ORDERS_PATH = "/api/v1/orders"
CONTRACT_DETAIL = "/api/v1/contracts/{symbol}"
MARK_PRICE_PATH = "/api/v1/mark-price/{symbol}/current"
TICKER_PATH = "/api/v1/ticker"
POSITIONS_PATH = "/api/v1/positions"
CANCEL_ORDER_PATH = "/api/v1/orders/{order_id}"
BULLET_PRIVATE = "/api/v1/bullet-private"


def _round_down_to_tick(px: float, tick: float) -> str:
    if tick <= 0:
        return str(px)
    q = Decimal(str(tick))
    d = Decimal(str(px))
    steps = (d / q).to_integral_value(rounding=ROUND_DOWN)
    return str(steps * q)


def _round_up_to_tick(px: float, tick: float) -> str:
    if tick <= 0:
        return str(px)
    q = Decimal(str(tick))
    d = Decimal(str(px))
    steps = (d / q).to_integral_value(rounding=ROUND_UP)
    return str(steps * q)


def _calc_raw_tp_sl(ref_price: float, side: str, tp_pct: float, sl_pct: float) -> Tuple[float, float]:
    s = side.lower()
    if s == "buy":
        return ref_price * (1 + tp_pct / 100.0), ref_price * (1 - sl_pct / 100.0)
    if s == "sell":
        return ref_price * (1 - tp_pct / 100.0), ref_price * (1 + sl_pct / 100.0)
    raise ValueError("side must be 'buy' or 'sell'")


class KuCoinFuturesClient(ExchangeClient):
    name = "kucoin"
    market = "futures"

    def __init__(self, key: str, secret: str, passphrase: str, key_version: str = "3"):
        self.key, self.secret, self.passphrase_plain = key, secret, passphrase
        self.key_version = str(key_version or "3").strip()
        to = aiohttp.ClientTimeout(total=2.2, connect=0.3, sock_connect=0.3, sock_read=1.0)
        self.session = aiohttp.ClientSession(
            timeout=to,
            connector=aiohttp.TCPConnector(limit=128, ttl_dns_cache=300, ssl=True, keepalive_timeout=30),
        )
        # websocket session for private order updates
        self.ws_session = aiohttp.ClientSession()
        self._ws_task: Optional[asyncio.Task] = None
        # map order_id -> sibling order_id for tp/sl pairs
        self._order_pairs: Dict[str, str] = {}

    async def close(self):
        await self.session.close()
        await self.ws_session.close()
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(Exception):
                await self._ws_task

    def symbol_from_base(self, base: str) -> str:
        base = base.upper()
        base = BTC_ALIAS.get(base, base)
        return f"{base}USDTM"

    def _ts_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, method: str, endpoint: str, body_str: str) -> Dict[str, str]:
        ts = self._ts_ms()
        prehash = f"{ts}{method.upper()}{endpoint}{body_str}"
        sig = base64.b64encode(hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        kv = self.key_version
        if kv in ("2", "3"):
            psp = base64.b64encode(hmac.new(self.secret.encode(), self.passphrase_plain.encode(), hashlib.sha256).digest()).decode()
        else:
            psp = self.passphrase_plain
        return {
            "KC-API-KEY": self.key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": psp,
            "KC-API-KEY-VERSION": kv,
            "Content-Type": "application/json",
        }

    async def _req(self, method: str, path: str, j: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, str]] = None) -> Any:
        body = json.dumps(j, separators=(",", ":")) if j else ""
        endpoint = path
        if params:
            qs = urlencode(params)
            endpoint = f"{path}?{qs}"
        headers = self._sign(method, endpoint, body)
        url = KC_BASE + path
        async with self.session.request(method, url, data=body if j else None, headers=headers, params=params) as r:
            txt = await r.text()
            if r.status < 200 or r.status >= 300:
                raise RuntimeError(f"{r.status} {r.reason}. Body={txt}")
            try:
                data = json.loads(txt)
            except Exception:
                return txt
            if isinstance(data, dict):
                code = data.get("code")
                if code is not None and str(code) != "200000":
                    msg = data.get("msg") or data.get("message")
                    raise RuntimeError(f"KuCoin API error {code}: {msg}")
            return data

    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        return (await self._req("GET", CONTRACT_DETAIL.replace("{symbol}", symbol)))["data"]

    async def get_mark_index(self, symbol: str) -> Tuple[float, float]:
        d = (await self._req("GET", MARK_PRICE_PATH.replace("{symbol}", symbol)))["data"]
        return float(d["value"]), float(d["indexPrice"])

    async def get_last_price(self, symbol: str) -> float:
        d = (await self._req("GET", TICKER_PATH, params={"symbol": symbol}))["data"]
        return float(d.get("price") or d.get("lastTradedPrice") or d.get("indexPrice"))

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        d = (await self._req("GET", POSITIONS_PATH, params={"symbol": symbol}))["data"]
        if isinstance(d, list):
            return d[0] if d else {}
        return d or {}

    async def cancel_order(self, order_id: str):
        path = CANCEL_ORDER_PATH.replace("{order_id}", order_id)
        await self._req("DELETE", path)

    async def _get_ws_url(self) -> Tuple[str, float]:
        """Get websocket URL and ping interval."""
        data = await self._req("POST", BULLET_PRIVATE)
        srv = data["data"]["instanceServers"][0]
        token = data["data"]["token"]
        endpoint = srv["endpoint"]
        ping_interval = float(srv.get("pingInterval", 20000)) / 1000.0
        return f"{endpoint}?token={token}", ping_interval

    async def _ensure_ws(self):
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._watch_orders_ws())

    async def _watch_orders_ws(self):
        """Background websocket listener to cancel sibling orders."""
        while True:
            try:
                url, ping_interval = await self._get_ws_url()
                async with self.ws_session.ws_connect(url, heartbeat=ping_interval) as ws:
                    sub = {
                        "id": str(uuid.uuid4()),
                        "type": "subscribe",
                        "topic": "/contractMarket/tradeOrders",
                        "privateChannel": True,
                    }
                    await ws.send_json(sub)
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except Exception:
                            continue
                        if data.get("topic") != "/contractMarket/tradeOrders":
                            continue
                        od = data.get("data") or {}
                        oid = od.get("orderId")
                        status = (od.get("status") or "").lower()
                        if not oid or status not in {"done", "filled", "match", "cancelled", "canceled"}:
                            continue
                        other = self._order_pairs.pop(oid, None)
                        if other:
                            await self.cancel_order(other)
                            self._order_pairs.pop(other, None)
            except Exception:
                await asyncio.sleep(1)

    async def trade(self, *, symbol: str, side: str, notional: float, tp_pct: float, sl_pct: float, leverage: int):
        spec = await self.get_contract(symbol)
        tick = float(spec.get("tickSize") or 0.01)
        base = {
            "clientOid": str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": "market",
            "marginMode": "ISOLATED",
            "reduceOnly": False,
            "closeOrder": False,
            "leverage": leverage,
        }
        body_v = dict(base)
        body_v["valueQty"] = str(notional)
        await self._req("POST", ST_ORDERS_PATH, j=body_v)

        # determine entry price from open position before placing tp/sl
        ref_price = 0.0
        await asyncio.sleep(0.2)
        for _ in range(60):
            pos = await self.get_position(symbol)
            ref_price = float(pos.get("avgEntryPrice") or pos.get("entryPrice") or 0)
            if ref_price:
                break
            await asyncio.sleep(0.5)
        if not ref_price:
            raise RuntimeError("entry price not found for position")
        tp_raw, sl_raw = _calc_raw_tp_sl(ref_price, side, tp_pct, sl_pct)
        if side.lower() == "buy":
            tp_price = _round_up_to_tick(tp_raw, tick)
            sl_price = _round_down_to_tick(sl_raw, tick)
            tpsl_side = "sell"
        else:
            tp_price = _round_down_to_tick(tp_raw, tick)
            sl_price = _round_up_to_tick(sl_raw, tick)
            tpsl_side = "buy"

        # determine position size for closing orders
        qty = float(pos.get("currentQty") or pos.get("pos") or pos.get("size") or 0)
        qty = abs(qty)
        if not qty:
            raise RuntimeError("position size not found")

        tp_req = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": tpsl_side,
            "type": "limit",
            "price": tp_price,
            "size": str(qty),
            "closeOrder": True,
            "reduceOnly": True,
        }

        sl_req = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": tpsl_side,
            "type": "limit",
            "price": sl_price,
            "size": str(qty),
            "closeOrder": True,
            "reduceOnly": True,
        }
        await self._ensure_ws()
        tp_res = await self._req("POST", ORDERS_PATH, j=tp_req)
        sl_res = await self._req("POST", ORDERS_PATH, j=sl_req)

        tp_id = tp_res.get("data", {}).get("orderId")
        sl_id = sl_res.get("data", {}).get("orderId")
        if tp_id and sl_id:
            self._order_pairs[tp_id] = sl_id
            self._order_pairs[sl_id] = tp_id

        return {"tp": tp_price, "sl": sl_price}
