#!/usr/bin/env python3
"""
binance_announce_to_kucoin.py
- Listens to Binance Announcements WS
- Decides (via user-stub) whether to open LONG/SHORT on KuCoin Futures
- Places a market entry + attached TP/SL in a single KuCoin request (/api/v1/st-orders)
"""

import asyncio
import os
import json
import hmac
import hashlib
import time
import uuid
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import requests           # sync Telegram push (simple)
import websockets         # WS 15.x
import aiohttp            # KuCoin REST

# ─────────────────────────────────────────────────────────
# 0) LOGGING
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("AnnounceWS")

# ─────────────────────────────────────────────────────────
# 1) BINANCE CONFIG
# ─────────────────────────────────────────────────────────
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

TOPIC = "com_announcement_en"
CATALOG_FILTER = {48, 161}  # 48 New Listing, 161 Delisting
BASE_WS = "wss://api.binance.com/sapi/wss"

def build_ws_url() -> str:
    rnd = uuid.uuid4().hex
    ts = int(time.time() * 1000)
    qs = f"random={rnd}&topic={TOPIC}&recvWindow=60000&timestamp={ts}"
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{BASE_WS}?{qs}&signature={sig}"

def push_telegram(msg: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": TG_CHAT_ID, "text": msg, "disable_web_page_preview": True, "parse_mode": "Markdown"},
            timeout=8,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning("Telegram push failed: %s", e)

# ─────────────────────────────────────────────────────────
# 2) KUCOIN CLIENT (single-call entry + TP/SL via /api/v1/st-orders)
# ─────────────────────────────────────────────────────────
KC_BASE = "https://api-futures.kucoin.com"
ST_ORDERS_PATH     = "/api/v1/st-orders"
CONTRACT_DETAIL    = "/api/v1/contracts/{symbol}"
MARK_PRICE_PATH    = "/api/v1/mark-price/{symbol}/current"
TICKER_PATH        = "/api/v1/ticker"

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()

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
    if s == "buy":    # long: TP above, SL below
        return ref_price * (1 + tp_pct/100.0), ref_price * (1 - sl_pct/100.0)
    if s == "sell":   # short: TP below, SL above
        return ref_price * (1 - tp_pct/100.0), ref_price * (1 + sl_pct/100.0)
    raise ValueError("side must be 'buy' or 'sell'")

import base64

class KcFutREST:
    """Reusable KuCoin Futures REST client (supports key v1/v2/v3)."""

    def __init__(self, key: str, secret: str, passphrase: str, key_version: str = "3"):
        self.key = key
        self.secret = secret
        self.passphrase_plain = passphrase
        self.key_version = str(key_version or "3").strip()
        timeout = aiohttp.ClientTimeout(total=2.5, connect=0.35, sock_connect=0.35, sock_read=1.2)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=aiohttp.TCPConnector(limit=128, ttl_dns_cache=300, ssl=True, keepalive_timeout=30),
        )

    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
    async def close(self):
        await self.session.close()

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
        headers = self._sign(method, path, body)
        url = KC_BASE + path
        async with self.session.request(method, url, data=body if j else None, headers=headers, params=params) as r:
            txt = await r.text()
            if r.status == 429:
                reset_ms = int(r.headers.get("gw-ratelimit-reset", "250"))
                await asyncio.sleep(reset_ms/1000.0)
                raise RuntimeError(f"429 Rate limited. URL={url} Body={txt}")
            if r.status < 200 or r.status >= 300:
                raise RuntimeError(f"{r.status} {r.reason}. URL={url} Body={txt}")
            try:
                return json.loads(txt)
            except Exception:
                return txt

    async def warm(self, symbol_hint: str = "ETHUSDTM"):
        """Optional warm-up to establish TLS/DNS before first trade."""
        try:
            await self.get_mark_index(symbol_hint)
        except Exception:
            pass

    # lightweight refs
    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        res = await self._req("GET", CONTRACT_DETAIL.replace("{symbol}", symbol))
        return res["data"]

    async def get_mark_index(self, symbol: str) -> Tuple[float, float]:
        res = await self._req("GET", MARK_PRICE_PATH.replace("{symbol}", symbol))
        d = res["data"]
        return float(d["value"]), float(d["indexPrice"])

    async def get_last_price(self, symbol: str) -> float:
        res = await self._req("GET", TICKER_PATH, params={"symbol": symbol})
        d = res["data"]
        return float(d.get("price") or d.get("lastTradedPrice") or d.get("indexPrice"))

    async def place_market_with_attached_tpsl(
        self,
        *,
        symbol: str,
        side: str,                         # "buy" or "sell"
        notional_usdt: float,              # trade size (not margin)
        tp_pct: float,
        sl_pct: float,
        leverage: Optional[int] = None,
        margin_mode: str = "ISOLATED",
        stop_price_type: str = "MP",       # "MP" mark, "TP" last, "IP" index
        min_ticks_gap: int = 1,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/st-orders — Market entry with TP/SL attached.
        - Correct Up/Down mapping per side.
        - Rounds in the right direction and enforces min tick gap.
        - Tries 'valueQty'; falls back to integer 'size' if notional < 1 lot.
        """
        spec = await self.get_contract(symbol)
        tick = float(spec.get("tickSize") or 0.01)
        mult = float(spec.get("multiplier") or 0.0)
        lotmin = int(spec.get("lotSize") or 1)

        mark, index = await self.get_mark_index(symbol)
        spt = stop_price_type.upper()
        ref = mark
        if spt == "TP":
            ref = await self.get_last_price(symbol)
        elif spt == "IP":
            ref = index

        tp_raw, sl_raw = _calc_raw_tp_sl(ref, side, tp_pct, sl_pct)
        side_l = side.lower()
        if side_l == "buy":
            tp_price = _round_up_to_tick(tp_raw, tick)
            sl_price = _round_down_to_tick(sl_raw, tick)
            if float(tp_price) <= ref:
                tp_price = _round_up_to_tick(ref + tick * min_ticks_gap, tick)
            if float(sl_price) >= ref:
                sl_price = _round_down_to_tick(ref - tick * min_ticks_gap, tick)
            up_price, down_price = tp_price, sl_price  # Long: Up=TP, Down=SL
        else:
            tp_price = _round_down_to_tick(tp_raw, tick)
            sl_price = _round_up_to_tick(sl_raw, tick)
            if float(tp_price) >= ref:
                tp_price = _round_down_to_tick(ref - tick * min_ticks_gap, tick)
            if float(sl_price) <= ref:
                sl_price = _round_up_to_tick(ref + tick * min_ticks_gap, tick)
            up_price, down_price = sl_price, tp_price  # Short: Up=SL, Down=TP

        base = {
            "clientOid": str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": "market",
            "marginMode": margin_mode,
            "stopPriceType": spt,
            "triggerStopUpPrice": up_price,
            "triggerStopDownPrice": down_price,
            "reduceOnly": False,
            "closeOrder": False,
        }
        if leverage is not None:
            base["leverage"] = leverage

        # Try by notional first
        body_v = dict(base)
        body_v["valueQty"] = str(notional_usdt)
        try:
            res = await self._req("POST", ST_ORDERS_PATH, j=body_v)
            return res["data"]
        except RuntimeError as e:
            emsg = str(e).lower()
            if not any(k in emsg for k in ("invalid", "quantity", "parameter", "valueqty")):
                raise  # non-quantity error

        # Fallback: convert notional → integer lots
        px = ref or await self.get_last_price(symbol)
        if mult <= 0:
            raise RuntimeError(f"Contract multiplier missing for {symbol}")
        lots = int((float(notional_usdt) / (px * mult)) // 1)
        if lots < lotmin:
            raise ValueError(
                f"Notional ${notional_usdt} maps to {lots} lot(s) < min {lotmin} "
                f"for {symbol} (px={px}, multiplier={mult})"
            )
        body_s = dict(base)
        body_s["size"] = lots
        res2 = await self._req("POST", ST_ORDERS_PATH, j=body_s)
        return res2["data"]

# ─────────────────────────────────────────────────────────
# 3) SYMBOL & DECISION HELPERS
# ─────────────────────────────────────────────────────────
RX_BASE = re.compile(r"\b([A-Z0-9]{2,12})\b")

def extract_base_from_title(title: str) -> Optional[str]:
    """
    Naive extractor for a base coin ticker in the title.
    Adjust this to your title formats if needed.
    """
    m = re.search(r"\bdelist(?:ing)?\s+of\s+([A-Z0-9]{2,12})\b", title or "", flags=re.I)
    if m:
        return m.group(1).upper()
    # fallback: first token-like uppercase word (very naive)
    m2 = RX_BASE.search(title or "")
    return m2.group(1).upper() if m2 else None

BTC_ALIAS = {"BTC": "XBT"}  # KuCoin uses XBTUSDTM for BTC

def guess_kucoin_symbol_from_title(title: str) -> Optional[str]:
    base = extract_base_from_title(title)
    if not base:
        return None
    base = BTC_ALIAS.get(base, base)
    return f"{base}USDTM"


def decide_trade_from_title(title: str) -> str:
    """
    TODO: Put your strategy logic here.
    Return one of: "short", "long", "none".
    """
    # Example (disabled): if "Delisting" then short; if "New Cryptocurrency Listing" then long
    # if "delist" in title.lower(): return "short"
    # if "listing" in title.lower(): return "long"
    return "none"

# ─────────────────────────────────────────────────────────
# 4) TRADE PARAMS (env-overridable)
# ─────────────────────────────────────────────────────────
# Defaults — you can override via environment variables:
SHORT_NOTIONAL = float(os.getenv("SHORT_NOTIONAL", "200"))
SHORT_LEVERAGE = int(os.getenv("SHORT_LEVERAGE", "5"))
SHORT_TP_PCT   = float(os.getenv("SHORT_TP_PCT", "1.0"))
SHORT_SL_PCT   = float(os.getenv("SHORT_SL_PCT", "0.6"))

LONG_NOTIONAL  = float(os.getenv("LONG_NOTIONAL", "200"))
LONG_LEVERAGE  = int(os.getenv("LONG_LEVERAGE", "5"))
LONG_TP_PCT    = float(os.getenv("LONG_TP_PCT", "1.0"))
LONG_SL_PCT    = float(os.getenv("LONG_SL_PCT", "0.6"))

STOP_PRICE_TYPE = os.getenv("STOP_PRICE_TYPE", "MP")   # MP/TP/IP
MIN_TICKS_GAP   = int(os.getenv("MIN_TICKS_GAP", "1"))
MARGIN_MODE     = os.getenv("MARGIN_MODE", "ISOLATED")
KC_KEY_VERSION  = os.getenv("KC_KEY_VERSION", "3")

KC_KEY          = os.getenv("KC_KEY")
KC_SECRET       = os.getenv("KC_SECRET")
KC_PASSPHRASE   = os.getenv("KC_PASSPHRASE")

# ─────────────────────────────────────────────────────────
# 5) MAIN WS LOOP (uses a shared KuCoin client)
# ─────────────────────────────────────────────────────────
async def handle_announcement(payload: dict, rest: KcFutREST):
    """Process a single Binance announcement payload."""
    title = payload.get("title", "")
    catalog_id = payload.get("catalogId")

    # Log timestamp
    recv_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if catalog_id not in CATALOG_FILTER:
        return

    # Decide whether to trade
    decision = decide_trade_from_title(title)
    if decision not in ("short", "long"):
        return

    symbol = guess_kucoin_symbol_from_title(title)
    if not symbol:
        log.warning("Could not extract base symbol from title; skip: %s", title)
        return

    # Choose params based on direction
    if decision == "short":
        side, notional, lev, tp, sl = "sell", SHORT_NOTIONAL, SHORT_LEVERAGE, SHORT_TP_PCT, SHORT_SL_PCT
    else:
        side, notional, lev, tp, sl = "buy",  LONG_NOTIONAL,  LONG_LEVERAGE,  LONG_TP_PCT,  LONG_SL_PCT

    # Place market + TP/SL in one call
    try:
        resp = await rest.place_market_with_attached_tpsl(
            symbol=symbol,
            side=side,
            notional_usdt=notional,
            tp_pct=tp,
            sl_pct=sl,
            leverage=lev,
            margin_mode=MARGIN_MODE,
            stop_price_type=STOP_PRICE_TYPE,
            min_ticks_gap=MIN_TICKS_GAP,
        )
        log.info("KuCoin st-order OK: %s", resp)
        push_telegram(f"✅ KuCoin {decision.upper()} {symbol}\n"
                      f"Size≈${notional}, Lev={lev}x, TP={tp}%, SL={sl}%\n"
                      f"Ref={STOP_PRICE_TYPE}")
    except Exception as e:
        log.warning("KuCoin order failed: %s", e)
        push_telegram(f"❌ KuCoin {decision.upper()} failed for {symbol}\n{e}")

async def run_ws(rest: KcFutREST):
    """Binance announcements loop (auto-reconnect) using a shared KuCoin client."""
    while True:
        try:
            url = build_ws_url()
            async with websockets.connect(
                url,
                additional_headers={"X-MBX-APIKEY": API_KEY},
                ping_interval=25,
                ping_timeout=20,
            ) as ws:
                log.info("Connected to Binance Announcements WebSocket")
                push_telegram("🟢 Binance Announcements connected")
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "DATA":
                        continue
                    payload = json.loads(msg["data"])
                    asyncio.create_task(handle_announcement(payload, rest))
        except Exception as e:
            log.warning("%s – reconnecting in 1 s", e)
            push_telegram(f"⚠️  {e} – reconnecting ...")
            await asyncio.sleep(1)

# ─────────────────────────────────────────────────────────
# 6) ENTRYPOINT
# ─────────────────────────────────────────────────────────
async def main():
    # Basic env checks
    missing = [k for k, v in {
        "BINANCE_API_KEY": API_KEY,
        "BINANCE_API_SECRET": API_SECRET,
        "TG_TOKEN": TG_TOKEN,
        "TG_CHAT_ID": TG_CHAT_ID,
        "KC_KEY": KC_KEY,
        "KC_SECRET": KC_SECRET,
        "KC_PASSPHRASE": KC_PASSPHRASE,
    }.items() if not v]
    if missing:
        raise SystemExit("Missing env vars: " + ", ".join(missing))

    # Create a single KuCoin client and reuse it for all trades
    async with KcFutREST(KC_KEY, KC_SECRET, KC_PASSPHRASE, KC_KEY_VERSION) as rest:
        await rest.warm("ETHUSDTM")  # optional warm-up
        await run_ws(rest)

if __name__ == "__main__":
    asyncio.run(main())
