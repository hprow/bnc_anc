import asyncio
import time
from datetime import datetime, timezone
from typing import List
import logging

from .config import CATALOG_FILTER, TRADING_CONFIG, DECISION_CONFIG, STOP_PRICE_TYPE
from .decision import decide_event_from_title
from .telegram import push_telegram
from .exchanges.base import ExchangeClient

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


async def handle_payload(payload: dict, exchanges: List[ExchangeClient]):
    recv_ts = _now()
    start_perf = time.perf_counter()
    title = payload.get("title", "") or ""
    category = payload.get("catalogId") or -1
    if category not in CATALOG_FILTER:
        msg = f"[received at: {recv_ts}] {title}: category {category} ignored"
        push_telegram(msg)
        return

    decision, bases = decide_event_from_title(title)
    if decision == "none" or not bases:
        msg = f"[received at: {recv_ts}] {title}: NO TRADING DECISION"
        push_telegram(msg)
        return

    rule = DECISION_CONFIG.get(decision)
    if not rule:
        msg = f"[received at: {recv_ts}] {title}: decision '{decision}' not configured"
        push_telegram(msg)
        return

    side = "buy" if rule.side == "long" else "sell"

    tasks = []
    for base in bases:
        for ex in exchanges:
            if ex.name not in rule.exchanges:
                continue
            cfg = TRADING_CONFIG.get(ex.name, {}).get(ex.market)
            if not cfg:
                continue
            pos_cfg = cfg.long if rule.side == "long" else cfg.short
            symbol = ex.symbol_from_base(base)
            tasks.append(
                (
                    ex,
                    symbol,
                    pos_cfg,
                    asyncio.create_task(
                        ex.trade(
                            symbol=symbol,
                            side=side,
                            notional=pos_cfg.notional,
                            tp_pct=pos_cfg.tp_pct,
                            sl_pct=pos_cfg.sl_pct,
                            leverage=pos_cfg.leverage or 1,
                        )
                    ),
                )
            )

    messages = []
    for ex, symbol, pcfg, task in tasks:
        try:
            await task
            elapsed = int((time.perf_counter() - start_perf) * 1000)
            messages.append(
                f"✅ {rule.side.upper()} {symbol} on {ex.name}\n"
                f"Nominal≈${pcfg.notional} Lev={pcfg.leverage}x TP={pcfg.tp_pct}% SL={pcfg.sl_pct}% Ref={STOP_PRICE_TYPE}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {_now()}\n"
                f"ExecTime: {elapsed}ms"
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start_perf) * 1000)
            messages.append(
                f"❌ {rule.side.upper()} {symbol} on {ex.name} failed\n"
                f"{e}\n"
                f"Nominal≈${pcfg.notional} Lev={pcfg.leverage}x TP={pcfg.tp_pct}% SL={pcfg.sl_pct}% Ref={STOP_PRICE_TYPE}\n"
                f"Title: {title}\n"
                f"ReceivedAt: {recv_ts}\n"
                f"TradedAt: {_now()}\n"
                f"ExecTime: {elapsed}ms"
            )
    for msg in messages:
        log.info(msg)
        push_telegram(msg)
