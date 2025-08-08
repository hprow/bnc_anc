import asyncio
import logging
from typing import List

from .config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    KC_KEY,
    KC_SECRET,
    KC_PASSPHRASE,
    KC_KEY_VERSION,
    TEST_MODE,
    TRADING_CONFIG,
)
from .exchanges.base import ExchangeClient
from .exchanges.noop import NoOpExchange
from .exchanges.kucoin import KuCoinFuturesClient
from .ws import run_ws
from .telegram import push_telegram
from dataclasses import asdict

log = logging.getLogger(__name__)


def build_exchanges() -> List[ExchangeClient]:
    exchanges: List[ExchangeClient] = []
    if TEST_MODE:
        exchanges.append(NoOpExchange())
        return exchanges
    if KC_KEY and KC_SECRET and KC_PASSPHRASE:
        exchanges.append(KuCoinFuturesClient(KC_KEY, KC_SECRET, KC_PASSPHRASE, KC_KEY_VERSION))
    return exchanges


async def main():
    if not TEST_MODE and not all((BINANCE_API_KEY, BINANCE_API_SECRET, KC_KEY, KC_SECRET, KC_PASSPHRASE)):
        raise SystemExit("Missing required environment variables.")
    exchanges = build_exchanges()
    cfg = {ex: {m: asdict(mc) for m, mc in markets.items()} for ex, markets in TRADING_CONFIG.items()}
    msg = f"Trading configuration: {cfg}"
    log.info(msg)
    push_telegram(msg)
    try:
        await run_ws(exchanges)
    finally:
        await asyncio.gather(*(ex.close() for ex in exchanges), return_exceptions=True)
