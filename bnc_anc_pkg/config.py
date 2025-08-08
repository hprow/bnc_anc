import os
import json
from dataclasses import dataclass
from typing import Optional, Dict, List

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
KC_KEY = os.getenv("KC_KEY")
KC_SECRET = os.getenv("KC_SECRET")
KC_PASSPHRASE = os.getenv("KC_PASSPHRASE")
KC_KEY_VERSION = os.getenv("KC_KEY_VERSION", "3")
MEXC_KEY = os.getenv("MEXC_KEY")
MEXC_SECRET = os.getenv("MEXC_SECRET")

CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")
try:
    with open(CONFIG_FILE) as f:
        _conf = json.load(f)
except FileNotFoundError:
    _conf = {}

STOP_PRICE_TYPE = _conf.get("stop_price_type", "MP")
MIN_TICKS_GAP = int(_conf.get("min_ticks_gap", 1))
MARGIN_MODE = _conf.get("margin_mode", "ISOLATED")

TOPIC = "com_announcement_en"
BASE_WS = "wss://api.binance.com/sapi/wss"
CATALOG_FILTER = {48, 161}

TEST_MODE = bool(_conf.get("test_mode", False))


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


@dataclass
class DecisionConfig:
    side: str
    exchanges: List[str]


def _market_cfg(data: Dict[str, Dict[str, float]]) -> MarketConfig:
    return MarketConfig(
        long=PositionConfig(**data["long"]),
        short=PositionConfig(**data["short"]),
    )


TRADING_CONFIG: Dict[str, Dict[str, MarketConfig]] = {}
for ex_name, ex_cfg in _conf.get("trading", {}).items():
    TRADING_CONFIG[ex_name] = {m: _market_cfg(cfg) for m, cfg in ex_cfg.items()}


DECISION_CONFIG: Dict[str, DecisionConfig] = {
    k: DecisionConfig(**v) for k, v in _conf.get("decision", {}).items()
}
