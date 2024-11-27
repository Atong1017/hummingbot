# A single source of truth for constant variables related to the exchange

from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

EXCHANGE_NAME = "coinstore"
REST_URL = "https://api.coinstore.com"
WSS_PUBLIC_URL = "wss://ws.coinstore.com/s/ws"
WSS_PRIVATE_URL = "wss://ws.coinstore.com/s/ws"
WS_PING_TIMEOUT = 20 * 0.8

DEFAULT_DOMAIN = ""
MAX_ORDER_ID_LEN = 32
HBOT_ORDER_ID_PREFIX = ""
BROKER_ID = "hummingbotfound"

PUBLIC_TRADE_CHANNEL_NAME = "@ticker"
PUBLIC_DEPTH_CHANNEL_NAME = "@depth@50"
PRIVATE_ORDER_PROGRESS_CHANNEL_NAME = "@trade"

# REST API ENDPOINTS
CHECK_NETWORK_PATH_URL = "/api/system/service"  # ?
GET_TRADING_RULES_PATH_URL = '/v2/public/config/spot/symbols'  # 现货币种币对信息
# GET_TRADING_RULES_PATH_URL = "/api/v1/ticker/price"  # 获取所有交易对最新价格
GET_LAST_TRADING_PRICES_PATH_URL = "/api/v1/market/tickers"  # 市场所有交易对的Ticker
GET_ORDER_BOOK_PATH_URL = "/api/v1/market/depth"  # 获取交易对完整的深度
CREATE_ORDER_PATH_URL = "/api/trade/order/place"  # 创建订单
CANCEL_ORDER_PATH_URL = "/api/trade/order/cancel"  # 取消委托单
GET_ACCOUNT_SUMMARY_PATH_URL = "/api/spot/accountList"
GET_ORDER_DETAIL_PATH_URL = "/api/trade/order/orderInfo"  # 获取订单信息
GET_TRADE_DETAIL_PATH_URL = "/api/trade/match/accountMatches"  # 获取用户最新成交
SERVER_TIME_PATH = "/api/system/time"

# WS API ENDPOINTS
WS_CONNECT = "WSConnect"
WS_SUBSCRIBE = "WSSubscribe"

# Coinstore has a per method API limit
RATE_LIMITS = [
    RateLimit(limit_id=CHECK_NETWORK_PATH_URL, limit=10, time_interval=1),
    RateLimit(limit_id=GET_TRADING_RULES_PATH_URL, limit=30, time_interval=5),
    RateLimit(limit_id=GET_LAST_TRADING_PRICES_PATH_URL, limit=30, time_interval=5),
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
    "1": OrderState.FAILED,
    "2": OrderState.OPEN,
    "3": OrderState.FAILED,
    "4": OrderState.OPEN,
    "5": OrderState.PARTIALLY_FILLED,
    "6": OrderState.FILLED,
    "7": OrderState.PENDING_CANCEL,
    "8": OrderState.CANCELED,
}