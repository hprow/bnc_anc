import json
import asyncio
import websockets
from typing import List
from .config import BINANCE_API_KEY, BINANCE_API_SECRET, BASE_WS, TOPIC
from .handler import handle_payload

def build_ws_url(secret: str) -> str:
    import uuid, time, hmac, hashlib
    rnd = uuid.uuid4().hex
    ts = int(time.time() * 1000)
    qs = f"random={rnd}&topic={TOPIC}&recvWindow=60000&timestamp={ts}"
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{BASE_WS}?{qs}&signature={sig}"


async def run_ws(exchanges: List[object]):
    while True:
        try:
            url = build_ws_url(BINANCE_API_SECRET)
            async with websockets.connect(url, additional_headers={"X-MBX-APIKEY": BINANCE_API_KEY}, ping_interval=25, ping_timeout=20) as ws:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("type") != "DATA":
                            continue
                        payload = json.loads(msg["data"])
                        asyncio.create_task(handle_payload(payload, exchanges))
                    except Exception:
                        pass
        except Exception:
            await asyncio.sleep(1)
