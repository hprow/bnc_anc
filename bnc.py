
import asyncio, os, json, hmac, hashlib, time, uuid, urllib.parse
import websockets, requests

### ─────────── 1.  ⚙️  CONFIG  ───────────
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

TOPIC      = "com_announcement_en"
CATALOG_FILTER = {48, 161}

BASE_WS = "wss://api.binance.com/sapi/wss"

def build_ws_url():
    rnd  = uuid.uuid4().hex
    ts   = int(time.time()*1000)
    qs   = f"random={rnd}&topic={TOPIC}&recvWindow=60000&timestamp={ts}"
    sig  = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{BASE_WS}?{qs}&signature={sig}"

def push_telegram(msg:str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg, "disable_web_page_preview": True})
    resp.raise_for_status()

async def run():
    while True:
        try:
            url = build_ws_url()
            async with websockets.connect(
                    url,
                    additional_headers={"X-MBX-APIKEY": API_KEY},
                    ping_interval=25, ping_timeout=20) as ws:
                push_telegram("🟢 connected")
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "DATA":
                        continue
                    payload = json.loads(msg["data"])
                    if payload.get("catalogId") not in CATALOG_FILTER:
                        continue
                    title = payload.get("title", "")
                    link  = f"https://www.binance.com/en/support/announcement/{payload.get('id','')}"
                    push_telegram(f"📢 *{title}*\n{link}")
        except Exception as e:
            push_telegram(f"⚠️  {e} – reconnecting ...")
            await asyncio.sleep(1)

if __name__ == "__main__":
    if not all((API_KEY, API_SECRET, TG_TOKEN, TG_CHAT_ID)):
        raise SystemExit("Set BINANCE_API_KEY, BINANCE_API_SECRET, TG_TOKEN, TG_CHAT_ID!")
    asyncio.run(run())
