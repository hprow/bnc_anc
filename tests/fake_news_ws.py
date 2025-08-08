import argparse
import asyncio
import json
import os

# ensure test mode
os.environ.setdefault("TEST_MODE", "1")

import websockets

from bnc_anc_pkg.handler import handle_payload
from bnc_anc_pkg.main import build_exchanges


async def _fake_server(websocket):
    title = await asyncio.get_event_loop().run_in_executor(None, input, "Enter news title: ")
    payload = {"title": title, "catalogId": 48}
    message = {"type": "DATA", "data": json.dumps(payload)}
    await websocket.send(json.dumps(message))


async def _fake_client(uri, exchanges):
    async with websockets.connect(uri) as ws:
        raw = await ws.recv()
        msg = json.loads(raw)
        payload = json.loads(msg.get("data", "{}"))
        await handle_payload(payload, exchanges)


async def run_fake_ws(exchanges):
    server = await websockets.serve(_fake_server, "127.0.0.1", 8765)
    try:
        await _fake_client("ws://127.0.0.1:8765", exchanges)
    finally:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exchange",
        dest="exchanges",
        action="append",
        default=[],
        help="Exchange to enable (can be used multiple times)",
    )
    args = parser.parse_args()
    exchanges = build_exchanges(args.exchanges or None)
    asyncio.run(run_fake_ws(exchanges))
