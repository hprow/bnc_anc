import os
from dataclasses import dataclass
from typing import Optional, Dict

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
KC_KEY = os.getenv("KC_KEY")
KC_SECRET = os.getenv("KC_SECRET")
KC_PASSPHRASE = os.getenv("KC_PASSPHRASE")
KC_KEY_VERSION = os.getenv("KC_KEY_VERSION", "3")

STOP_PRICE_TYPE = os.getenv("STOP_PRICE_TYPE", "MP")
MIN_TICKS_GAP = int(os.getenv("MIN_TICKS_GAP", "1"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "ISOLATED")

TOPIC = "com_announcement_en"
BASE_WS = "wss://api.binance.com/sapi/wss"
CATALOG_FILTER = {48, 161}

TEST_MODE = os.getenv("TEST_MODE", "0").lower() in {"1", "true", "yes"}


@dataclass
class PositionConfig:
    notional: float
    leverage: Optional[int]
    tp_pct: float
    sl_pct: float


@dataclass
class MarketConfig:
    long: PositionConfig
    short: PositionConfig


def _pos_cfg(prefix: str) -> PositionConfig:
    notional = float(os.getenv(f"{prefix}_NOTIONAL", "100"))
    lev = os.getenv(f"{prefix}_LEVERAGE", "5")
    leverage = int(lev) if lev else None
    tp = float(os.getenv(f"{prefix}_TP_PCT", "1.0"))
    sl = float(os.getenv(f"{prefix}_SL_PCT", "0.6"))
    return PositionConfig(notional, leverage, tp, sl)


TRADING_CONFIG: Dict[str, Dict[str, MarketConfig]] = {
    "kucoin": {
        "futures": MarketConfig(
            long=_pos_cfg("KC_FUT_LONG"),
            short=_pos_cfg("KC_FUT_SHORT"),
        )
    },
    "noop": {
        "futures": MarketConfig(
            long=_pos_cfg("NOOP_FUT_LONG"),
            short=_pos_cfg("NOOP_FUT_SHORT"),
        )
    },
}
