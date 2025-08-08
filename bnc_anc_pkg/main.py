import asyncio
import logging
from typing import List, Optional

from .config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    KC_KEY,
    KC_SECRET,
    KC_PASSPHRASE,
    KC_KEY_VERSION,
    MEXC_KEY,
    MEXC_SECRET,
    TEST_MODE,
    TRADING_CONFIG,
    DECISION_CONFIG,
)
from .exchanges.base import ExchangeClient
from .exchanges.noop import NoOpExchange
from .exchanges.kucoin import KuCoinFuturesClient
from .exchanges.mexc import MexcSpotClient
from .ws import run_ws
from .telegram import push_telegram
from dataclasses import asdict

log = logging.getLogger(__name__)


def build_exchanges(enabled: Optional[List[str]] = None) -> List[ExchangeClient]:
    exchanges: List[ExchangeClient] = []
    if enabled is None:
        names: List[str] = []
        for rule in DECISION_CONFIG.values():
            names.extend(rule.exchanges)
        if not names and TEST_MODE:
            names = ["noop"]
        enabled = sorted(set(names))
    for name in enabled:
        if name == "noop":
            exchanges.append(NoOpExchange())
        elif name == "kucoin":
            if KC_KEY and KC_SECRET and KC_PASSPHRASE:
                exchanges.append(
                    KuCoinFuturesClient(KC_KEY, KC_SECRET, KC_PASSPHRASE, KC_KEY_VERSION)
                )
        elif name == "mexc":
            if MEXC_KEY and MEXC_SECRET:
                exchanges.append(MexcSpotClient(MEXC_KEY, MEXC_SECRET))
        else:
            raise ValueError(f"Unknown exchange: {name}")
    return exchanges


async def main():
    if not TEST_MODE and not all((BINANCE_API_KEY, BINANCE_API_SECRET, KC_KEY, KC_SECRET, KC_PASSPHRASE)):
        raise SystemExit("Missing required environment variables.")
    exchanges = build_exchanges()
    cfg = {ex: {m: asdict(mc) for m, mc in markets.items()} for ex, markets in TRADING_CONFIG.items()}
    msg = f"Trading configuration: {cfg}; decisions: {DECISION_CONFIG}"
    log.info(msg)
    push_telegram(msg)
    try:
        await run_ws(exchanges)
    finally:
        await asyncio.gather(*(ex.close() for ex in exchanges), return_exceptions=True)
