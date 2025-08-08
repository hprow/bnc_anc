import asyncio
from datetime import datetime, timezone
from typing import List
import logging

from .config import (
    CATALOG_FILTER,
    LONG_NOTIONAL,
    LONG_LEVERAGE,
    LONG_TP_PCT,
    LONG_SL_PCT,
    SHORT_NOTIONAL,
    SHORT_LEVERAGE,
    SHORT_TP_PCT,
    SHORT_SL_PCT,
    STOP_PRICE_TYPE,
)
from .decision import decide_trade_from_title
from .telegram import push_telegram
from .exchanges.base import ExchangeClient

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def handle_payload(payload: dict, exchanges: List[ExchangeClient]):
    recv_ts = _now()
    title = payload.get("title", "") or ""
    category = payload.get("catalogId") or -1
    if category not in CATALOG_FILTER:
        msg = f"[received at: {recv_ts}] {title}: category {category} ignored"
        push_telegram(msg)
        return

    decision, bases = decide_trade_from_title(title)
    if decision == "none" or not bases:
        msg = f"[received at: {recv_ts}] {title}: NO TRADING DECISION"
        push_telegram(msg)
        return

    if decision == "long":
        side, notional, lev, tp, sl = "buy", LONG_NOTIONAL, LONG_LEVERAGE, LONG_TP_PCT, LONG_SL_PCT
    else:
        side, notional, lev, tp, sl = "sell", SHORT_NOTIONAL, SHORT_LEVERAGE, SHORT_TP_PCT, SHORT_SL_PCT

    tasks = []
    for base in bases:
        for ex in exchanges:
            symbol = ex.symbol_from_base(base)
            tasks.append((ex, symbol, asyncio.create_task(ex.trade(symbol=symbol, side=side, notional=notional, tp_pct=tp, sl_pct=sl, leverage=lev))))

    messages = []
    for ex, symbol, task in tasks:
        try:
            await task
            messages.append(
                f"✅ {decision.upper()} {symbol} on {ex.name}\n"
                f"Size≈${notional} Lev={lev}x TP={tp}% SL={sl}% Ref={STOP_PRICE_TYPE}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {_now()}"
            )
        except Exception as e:
            messages.append(
                f"❌ {decision.upper()} {symbol} on {ex.name} failed\n"
                f"{e}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {_now()}"
            )
    for msg in messages:
        log.info(msg)
        push_telegram(msg)
