import os

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
KC_KEY = os.getenv("KC_KEY")
KC_SECRET = os.getenv("KC_SECRET")
KC_PASSPHRASE = os.getenv("KC_PASSPHRASE")
KC_KEY_VERSION = os.getenv("KC_KEY_VERSION", "3")

LONG_NOTIONAL = float(os.getenv("LONG_NOTIONAL", "100"))
LONG_LEVERAGE = int(os.getenv("LONG_LEVERAGE", "5"))
LONG_TP_PCT = float(os.getenv("LONG_TP_PCT", "1.0"))
LONG_SL_PCT = float(os.getenv("LONG_SL_PCT", "0.6"))

SHORT_NOTIONAL = float(os.getenv("SHORT_NOTIONAL", "100"))
SHORT_LEVERAGE = int(os.getenv("SHORT_LEVERAGE", "5"))
SHORT_TP_PCT = float(os.getenv("SHORT_TP_PCT", "1.0"))
SHORT_SL_PCT = float(os.getenv("SHORT_SL_PCT", "0.6"))

STOP_PRICE_TYPE = os.getenv("STOP_PRICE_TYPE", "MP")
MIN_TICKS_GAP = int(os.getenv("MIN_TICKS_GAP", "1"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "ISOLATED")

TOPIC = "com_announcement_en"
BASE_WS = "wss://api.binance.com/sapi/wss"
CATALOG_FILTER = {48, 161}

TEST_MODE = os.getenv("TEST_MODE", "0").lower() in {"1", "true", "yes"}
