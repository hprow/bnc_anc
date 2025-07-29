import os, asyncio, json, hmac, hashlib, time, uuid, re, base64
from typing import Optional, Dict, Any, Tuple, List, Set
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from datetime import datetime, timezone
import requests
import websockets
import aiohttp
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("BNC_WATCHER")

# ─────────────────────────────────────────────────────────
# CONFIG: ENV + TRADE PARAMS
# ─────────────────────────────────────────────────────────
BINANCE_API_KEY   = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET= os.getenv("BINANCE_API_SECRET")
TG_TOKEN          = os.getenv("TG_TOKEN")
TG_CHAT_ID        = os.getenv("TG_CHAT_ID")

KC_KEY            = os.getenv("KC_KEY")
KC_SECRET         = os.getenv("KC_SECRET")
KC_PASSPHRASE     = os.getenv("KC_PASSPHRASE")
KC_KEY_VERSION    = os.getenv("KC_KEY_VERSION", "3")

# LONG params
LONG_NOTIONAL     = float(os.getenv("LONG_NOTIONAL", "100"))
LONG_LEVERAGE     = int(os.getenv("LONG_LEVERAGE", "5"))
LONG_TP_PCT       = float(os.getenv("LONG_TP_PCT", "1.0"))
LONG_SL_PCT       = float(os.getenv("LONG_SL_PCT", "0.6"))

# SHORT params
SHORT_NOTIONAL    = float(os.getenv("SHORT_NOTIONAL", "100"))
SHORT_LEVERAGE    = int(os.getenv("SHORT_LEVERAGE", "5"))
SHORT_TP_PCT      = float(os.getenv("SHORT_TP_PCT", "1.0"))
SHORT_SL_PCT      = float(os.getenv("SHORT_SL_PCT", "0.6"))

# Stops reference & rounding gap
STOP_PRICE_TYPE   = os.getenv("STOP_PRICE_TYPE", "MP")  # MP (mark), TP (last), IP (index)
MIN_TICKS_GAP     = int(os.getenv("MIN_TICKS_GAP", "1"))
MARGIN_MODE       = os.getenv("MARGIN_MODE", "ISOLATED")

# Binance WS settings
TOPIC             = "com_announcement_en"
BASE_WS           = "wss://api.binance.com/sapi/wss"

# BNC News Categories
CATALOG_FILTER = {48, 161} # new listings & delistings


# ─────────────────────────────────────────────────────────
# UTILS: Telegram (post-trade only)
# ─────────────────────────────────────────────────────────
def push_telegram(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=8)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────
# BINANCE WS URL (signed)
# ─────────────────────────────────────────────────────────
def build_ws_url() -> str:
    rnd  = uuid.uuid4().hex
    ts   = int(time.time() * 1000)
    qs   = f"random={rnd}&topic={TOPIC}&recvWindow=60000&timestamp={ts}"
    sig  = hmac.new(BINANCE_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{BASE_WS}?{qs}&signature={sig}"

# ─────────────────────────────────────────────────────────
# KUCOIN CLIENT: single-call entry + TP/SL (st-orders)
# ─────────────────────────────────────────────────────────
KC_BASE = "https://api-futures.kucoin.com"
ST_ORDERS_PATH  = "/api/v1/st-orders"
CONTRACT_DETAIL = "/api/v1/contracts/{symbol}"
MARK_PRICE_PATH = "/api/v1/mark-price/{symbol}/current"
TICKER_PATH     = "/api/v1/ticker"

def _round_down_to_tick(px: float, tick: float) -> str:
    if tick <= 0: return str(px)
    q = Decimal(str(tick)); d = Decimal(str(px))
    steps = (d / q).to_integral_value(rounding=ROUND_DOWN)
    return str(steps * q)

def _round_up_to_tick(px: float, tick: float) -> str:
    if tick <= 0: return str(px)
    q = Decimal(str(tick)); d = Decimal(str(px))
    steps = (d / q).to_integral_value(rounding=ROUND_UP)
    return str(steps * q)

def _calc_raw_tp_sl(ref_price: float, side: str, tp_pct: float, sl_pct: float) -> Tuple[float, float]:
    s = side.lower()
    if s == "buy":   # long: TP above, SL below
        return ref_price * (1 + tp_pct/100.0), ref_price * (1 - sl_pct/100.0)
    if s == "sell":  # short: TP below, SL above
        return ref_price * (1 - tp_pct/100.0), ref_price * (1 + sl_pct/100.0)
    raise ValueError("side must be 'buy' or 'sell'")

class KcFutREST:
    def __init__(self, key: str, secret: str, passphrase: str, key_version: str = "3"):
        self.key, self.secret, self.passphrase_plain = key, secret, passphrase
        self.key_version = str(key_version or "3").strip()
        to = aiohttp.ClientTimeout(total=2.2, connect=0.3, sock_connect=0.3, sock_read=1.0)
        self.session = aiohttp.ClientSession(
            timeout=to,
            connector=aiohttp.TCPConnector(limit=128, ttl_dns_cache=300, ssl=True, keepalive_timeout=30)
        )
    async def __aenter__(self): return self
    async def __aexit__(self, a,b,c): await self.close()
    async def close(self): await self.session.close()

    def _ts_ms(self) -> str: return str(int(time.time() * 1000))
    def _sign(self, method: str, endpoint: str, body_str: str) -> Dict[str, str]:
        ts = self._ts_ms()
        prehash = f"{ts}{method.upper()}{endpoint}{body_str}"
        sig = base64.b64encode(hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        kv = self.key_version
        if kv in ("2","3"):
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
            if r.status < 200 or r.status >= 300:
                raise RuntimeError(f"{r.status} {r.reason}. Body={txt}")
            try:
                return json.loads(txt)
            except Exception:
                return txt

    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        return (await self._req("GET", CONTRACT_DETAIL.replace("{symbol}", symbol)))["data"]
    async def get_mark_index(self, symbol: str) -> Tuple[float, float]:
        d = (await self._req("GET", MARK_PRICE_PATH.replace("{symbol}", symbol)))["data"]
        return float(d["value"]), float(d["indexPrice"])
    async def get_last_price(self, symbol: str) -> float:
        d = (await self._req("GET", TICKER_PATH, params={"symbol": symbol}))["data"]
        return float(d.get("price") or d.get("lastTradedPrice") or d.get("indexPrice"))

    async def place_market_with_attached_tpsl(
        self, *, symbol: str, side: str, notional_usdt: float,
        tp_pct: float, sl_pct: float, leverage: Optional[int] = None,
        margin_mode: str = "ISOLATED", stop_price_type: str = "MP", min_ticks_gap: int = 1
    ) -> Dict[str, Any]:
        spec = await self.get_contract(symbol)
        tick = float(spec.get("tickSize") or 0.01)
        mult = float(spec.get("multiplier") or 0.0)
        lotmin = int(spec.get("lotSize") or 1)

        mark, index = await self.get_mark_index(symbol)
        spt = stop_price_type.upper()
        ref = mark if spt == "MP" else (await self.get_last_price(symbol) if spt=="TP" else index)

        tp_raw, sl_raw = _calc_raw_tp_sl(ref, side, tp_pct, sl_pct)
        if side.lower() == "buy":
            tp_price = _round_up_to_tick(tp_raw, tick)
            sl_price = _round_down_to_tick(sl_raw, tick)
            if float(tp_price) <= ref: tp_price = _round_up_to_tick(ref + tick*min_ticks_gap, tick)
            if float(sl_price) >= ref: sl_price = _round_down_to_tick(ref - tick*min_ticks_gap, tick)
            up_price, down_price = tp_price, sl_price
        else:
            tp_price = _round_down_to_tick(tp_raw, tick)
            sl_price = _round_up_to_tick(sl_raw, tick)
            if float(tp_price) >= ref: tp_price = _round_down_to_tick(ref - tick*min_ticks_gap, tick)
            if float(sl_price) <= ref: sl_price = _round_up_to_tick(ref + tick*min_ticks_gap, tick)
            up_price, down_price = sl_price, tp_price

        base = {
            "clientOid": str(uuid.uuid4()), "side": side, "symbol": symbol, "type": "market",
            "marginMode": margin_mode, "stopPriceType": spt,
            "triggerStopUpPrice": up_price, "triggerStopDownPrice": down_price,
            "reduceOnly": False, "closeOrder": False
        }
        if leverage is not None: base["leverage"] = leverage

        # try notional first
        body_v = dict(base); body_v["valueQty"] = str(notional_usdt)
        try:
            return (await self._req("POST", ST_ORDERS_PATH, j=body_v))["data"]
        except Exception as e:
            emsg = str(e).lower()
            if not any(k in emsg for k in ("invalid","quantity","parameter","valueqty")):
                raise

        # fallback: convert notional -> lots (size)
        px = ref or await self.get_last_price(symbol)
        if mult <= 0: raise RuntimeError(f"Contract multiplier missing for {symbol}")
        lots = int((float(notional_usdt) / (px * mult)) // 1)
        if lots < lotmin:
            raise ValueError(f"Notional ${notional_usdt} -> {lots} lot(s) < min {lotmin} at px={px}, mult={mult}")
        body_s = dict(base); body_s["size"] = lots
        return (await self._req("POST", ST_ORDERS_PATH, j=body_s))["data"]

# ─────────────────────────────────────────────────────────
# TITLE → DECISION (FAST)
# ─────────────────────────────────────────────────────────
BTC_ALIAS = {"BTC": "XBT"}

def _bases_from_parentheses(title: str) -> Set[str]:
    # capture tickers in (...) excluding dates like (2025-07-04)
    out: Set[str] = set()
    for tok in re.findall(r"\(([A-Z0-9]{2,15})\)", title or ""):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok):  # date
            continue
        if tok.upper() in {"USDT","USDC","USD","FUTURES","ALPHA"}:
            # "ALPHA" here is generic word only when used as product, but keep rule minimal
            pass
        out.add(tok.upper())
    return out

def _bases_from_usdt_pairs(title: str) -> Set[str]:
    # Find ZRCUSDT, ESPORTSUSDT, etc. (base is group 1)
    return {m.upper() for m in re.findall(r"\b([A-Z0-9]{2,30})USDT\b", title or "")}

def _bases_from_delist(title: str) -> Set[str]:
    # After 'delist ' capture token list up to ' on ' (date) or end
    m = re.search(r"delist\s+(.+)", title or "", flags=re.I)
    if not m:
        return set()
    seg = m.group(1)
    seg = seg.split(" on ")[0]  # cut trailing date part if present
    # split by commas and 'and'
    parts = re.split(r"[,\s]+and\s+|,\s*|\s+", seg)
    out: Set[str] = set()
    for p in parts:
        tok = p.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,15}", tok):
            continue
        if tok in {"USDT","USDC","USD"}:
            continue
        out.add(tok)
    return out

def decide_trade_from_title(title: str) -> Tuple[str, List[str]]:
    """
    Returns: ("long"|"short"|"none", [bases])
    LONG triggers:
      - "... Will Add ... (TICKER) ..."
      - "... Will Be Available ... (TICKER) ..."
      - "... Will Launch ... <BASE>USDT ...", "Futures Will Launch ..."
      - "... Will List ... (TICKER) ..."
    SHORT triggers:
      - "... Will Delist ALPHA, BSW, ... (date)"
    """
    if not title:
        return "none", []

    tl = title.lower()

    # SHORT: delist
    if "will delist" in tl or re.search(r"\bdelist\b", tl):
        bases = _bases_from_delist(title)
        return ("short", sorted(bases)) if bases else ("none", [])

    # LONG: add/list/available/launch (Futures)
    if any(k in tl for k in ("will add", "will list", "will be available", "will launch", "futures will launch")):
        bases = set()
        bases |= _bases_from_parentheses(title)
        bases |= _bases_from_usdt_pairs(title)
        return ("long", sorted(bases)) if bases else ("none", [])

    return "none", []

def kucoin_symbol_from_base(base: str) -> str:
    base = base.upper()
    base = BTC_ALIAS.get(base, base)  # BTC -> XBT
    return f"{base}USDTM"

# ─────────────────────────────────────────────────────────
# BINANCE WS LOOP  (no pre-trade logs; only post-trade notifications)
# ─────────────────────────────────────────────────────────
async def handle_payload(payload: dict, rest: KcFutREST):
    recv_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    title = payload.get("title", "") or ""
    category = payload.get("catalogId") or -1
    if category not in CATALOG_FILTER:
        msg = f"[received at: {recv_ts}] {title}: category {category} ignored"
        log.info(msg)
        push_telegram(msg)
        return
    decision, bases = decide_trade_from_title(title)
    if decision == "none" or not bases:
        msg = f"[received at: {recv_ts}] {title}: NO TRADING DECISION"
        log.info(msg)
        push_telegram(msg)
        return

    # choose params
    if decision == "long":
        side, notional, lev, tp, sl = "buy", LONG_NOTIONAL, LONG_LEVERAGE, LONG_TP_PCT, LONG_SL_PCT
    else:
        side, notional, lev, tp, sl = "sell", SHORT_NOTIONAL, SHORT_LEVERAGE, SHORT_TP_PCT, SHORT_SL_PCT

    # trade each base independently (no pre-trade prints/logs)
    messages = []
    for base in bases:
        symbol = kucoin_symbol_from_base(base)
        try:
            resp = await rest.place_market_with_attached_tpsl(
                symbol=symbol, side=side, notional_usdt=notional,
                tp_pct=tp, sl_pct=sl, leverage=lev,
                margin_mode=MARGIN_MODE, stop_price_type=STOP_PRICE_TYPE, min_ticks_gap=MIN_TICKS_GAP
            )
            messages.append(
                f"✅ {decision.upper()} {symbol}\n"
                f"Size≈${notional} Lev={lev}x TP={tp}% SL={sl}% Ref={STOP_PRICE_TYPE}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            messages.append(
                f"❌ {decision.upper()} {symbol} failed\n"
                f"{e}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
            )
    for msg in messages:
        log.info(msg)
    for msg in messages:
        push_telegram(msg)

async def run_ws(rest: KcFutREST):
    while True:
        try:
            url = build_ws_url()
            async with websockets.connect(
                url,
                additional_headers={"X-MBX-APIKEY": BINANCE_API_KEY},
                ping_interval=25, ping_timeout=20,
            ) as ws:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") != "DATA":
                            continue
                        payload = json.loads(msg["data"])
                        # handle asynchronously so we don't block the WS loop
                        asyncio.create_task(handle_payload(payload, rest))
                    except Exception:
                        # swallow parsing exceptions; keep loop hot
                        pass
        except Exception:
            await asyncio.sleep(1)

# ─────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────
async def main():
    # minimal env check (no prints)
    if not all((BINANCE_API_KEY, BINANCE_API_SECRET, TG_TOKEN, TG_CHAT_ID, KC_KEY, KC_SECRET, KC_PASSPHRASE)):
        raise SystemExit("Missing required environment variables.")
    async with KcFutREST(KC_KEY, KC_SECRET, KC_PASSPHRASE, KC_KEY_VERSION) as rest:
        await run_ws(rest)

if __name__ == "__main__":
    asyncio.run(main())
