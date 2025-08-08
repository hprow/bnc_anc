import json
import asyncio
import websockets
import logging
from typing import List
from .config import BINANCE_API_KEY, BINANCE_API_SECRET, BASE_WS, TOPIC
from .handler import handle_payload
from .telegram import push_telegram

log = logging.getLogger(__name__)

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
            msg = "Connecting to Binance news WS"
            log.info(msg)
            push_telegram(msg)
            async with websockets.connect(
                url, additional_headers={"X-MBX-APIKEY": BINANCE_API_KEY}, ping_interval=25, ping_timeout=20
            ) as ws:
                msg = "Connected to Binance news WS, listening..."
                log.info(msg)
                push_telegram(msg)
                async for raw in ws:
                    try:
                        msg_data = json.loads(raw)
                        if msg_data.get("type") != "DATA":
                            continue
                        payload = json.loads(msg_data["data"])
                        asyncio.create_task(handle_payload(payload, exchanges))
                    except Exception:
                        pass
            msg = "Disconnected from Binance news WS"
            log.warning(msg)
            push_telegram(msg)
            msg = "Reconnecting to Binance news WS in 1s"
            log.info(msg)
            push_telegram(msg)
            await asyncio.sleep(1)
        except Exception:
            msg = "Reconnecting to Binance news WS in 1s"
            log.warning(msg)
            push_telegram(msg)
            await asyncio.sleep(1)
