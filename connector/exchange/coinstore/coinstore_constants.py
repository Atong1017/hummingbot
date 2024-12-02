# A single source of truth for constant variables related to the exchange

from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

EXCHANGE_NAME = "coinstore"
REST_URL = "https://api.coinstore.com"
WSS_PUBLIC_URL = "wss://ws.coinstore.com/s/ws"
WSS_PRIVATE_URL = "wss://ws.coinstore.com/s/ws"
WS_PING_TIMEOUT = 30 * 0.8

DEFAULT_DOMAIN = ""
MAX_ORDER_ID_LEN = 32
HBOT_ORDER_ID_PREFIX = ""
BROKER_ID = "hummingbotfound"

PUBLIC_TRADE_CHANNEL_NAME = "@ticker"
PUBLIC_DEPTH_CHANNEL_NAME = "@depth@50"
PRIVATE_ORDER_PROGRESS_CHANNEL_NAME = "@trade"

# REST API ENDPOINTS
CHECK_NETWORK_PATH_URL = "/api/system/service"  # ?
GET_PRICE_PATH_URL = "/api/v1/ticker/price"  # 獲取所有交易對最新價格
GET_LAST_TRADING_PRICES_PATH_URL = "/api/v1/market/tickers"  # 市場所有交易對的Ticker
GET_ORDER_BOOK_PATH_URL = "/api/v1/market/depth"  # 獲取交易對完整的深度
GET_TRADE_DETAIL_PATH_URL = "/api/trade/match/accountMatches"  # 獲取用戶最新成交
GET_ACTIVE_ORDERS_PATH_URL = "/api/v2/trade/order/active"  # 獲取當前訂單 v2 版本

# ==== POST ====
GET_TRADING_RULES_PATH_URL = '/api/v2/public/config/spot/symbols'  # 現貨幣種幣對資訊
CREATE_ORDER_PATH_URL = "/api/trade/order/place"  # 創建訂單
CANCEL_ORDER_PATH_URL = "/api/trade/order/cancel"  # 取消委託單
GET_ACCOUNT_SUMMARY_PATH_URL = "/api/spot/accountList"
GET_ORDER_DETAIL_PATH_URL = "/api/v2/trade/order/orderInfo"  # 獲取訂單資訊V2

SERVER_TIME_PATH = "/api/system/time"

# WS API ENDPOINTS
WS_CONNECT = "WSConnect"
WS_SUBSCRIBE = "WSSubscribe"

# Coinstore has a per method API limit
RATE_LIMITS = [
    RateLimit(limit_id=CHECK_NETWORK_PATH_URL, limit=10, time_interval=1),
    RateLimit(limit_id=GET_TRADING_RULES_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=GET_LAST_TRADING_PRICES_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=GET_PRICE_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=GET_ORDER_BOOK_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=CREATE_ORDER_PATH_URL, limit=150, time_interval=5),
    RateLimit(limit_id=CANCEL_ORDER_PATH_URL, limit=150, time_interval=5),
    RateLimit(limit_id=GET_ACCOUNT_SUMMARY_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=GET_ORDER_DETAIL_PATH_URL, limit=150, time_interval=5),
    RateLimit(limit_id=GET_TRADE_DETAIL_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=SERVER_TIME_PATH, limit=10, time_interval=1),
    RateLimit(limit_id=WS_CONNECT, limit=30, time_interval=60),
    RateLimit(limit_id=WS_SUBSCRIBE, limit=100, time_interval=10),
]

ORDER_STATE = {
    "NOT_FOUND": OrderState.FAILED,
    "SUBMITTING": OrderState.PENDING_CREATE,
    "SUBMITTED": OrderState.OPEN,
    "PARTIAL_FILLED": OrderState.PARTIALLY_FILLED,
    "CANCELED": OrderState.CANCELED,
    "FILLED": OrderState.FILLED,
}

