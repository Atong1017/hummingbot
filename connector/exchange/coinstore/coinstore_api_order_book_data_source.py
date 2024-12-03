import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import time
from decimal import Decimal

from hummingbot.connector.exchange.coinstore import (
    coinstore_constants as CONSTANTS,
    coinstore_utils as utils,
    coinstore_web_utils as web_utils,
)
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book_message import OrderBookMessage, OrderBookMessageType
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.coinstore.coinstore_exchange import CoinstoreExchange

import os
from datetime import datetime


# logging無法使用，暫用
def write_logs(text):
    # Set up the logger with a directory in Windows (e.g., C:\hummingbot_logs)
    LOG_DIR = "/mnt/c/hummingbot_logs"
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG_FILE_PATH = os.path.join(LOG_DIR, "coinstore_connector.log")

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format the log entry
    log_entry = f"{current_time} - coinstore_api_order_book_data_source - {text}"

    with open(LOG_FILE_PATH, "a") as test_file:
        test_file.write(log_entry + '\n')


class CoinstoreAPIOrderBookDataSource(OrderBookTrackerDataSource):

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 trading_pairs: List[str],
                 connector: 'CoinstoreExchange',
                 api_factory: WebAssistantsFactory):
        super().__init__(trading_pairs)
        self._connector: CoinstoreExchange = connector
        self._api_factory = api_factory

    # 10 => 交易對的最新交易價格 -> REST API
    async def get_last_traded_prices(self,
                                     trading_pairs: List[str],
                                     domain: Optional[str] = None) -> Dict[str, float]:
        try:
            # 例:{'BTC-USDT': 98765.43}
            return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)
        except Exception as e:
            write_logs(f"get_last_traded_prices : Error => {e}")

    async def listen_for_order_book_diffs(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Coinstore only sends full snapshots, they never send diff messages. That is why this method is overwritten to
        do nothing.

        :param ev_loop: the event loop the method will run in
        :param output: a queue to add the created diff messages
        """
        pass

    # 3 監聽並處理訂單簿快照更新 -> WS
    async def listen_for_order_book_snapshots(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):

        message_queue = self._message_queue[self._diff_messages_queue_key]
        # 持續監聽，直到任務被取消或異常終止
        while True:
            try:
                snapshot_event = await message_queue.get()  # 等待並從佇列中獲取一條訂單簿快照事件
                # 將原始快照消息 (raw_message) 解析為標準化的 Hummingbot 訂單簿消息
                await self._parse_order_book_snapshot_message(raw_message=snapshot_event, message_queue=output)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                write_logs(f"listen_for_order_book_snapshots : Error => {e}")

    # 8 =>  解析訂單簿快照消息
    async def _parse_order_book_snapshot_message(self, raw_message: Dict, message_queue: asyncio.Queue):
        """
        解析訂單簿快照消息, 將從交易所接收到的原始快照資料轉化為 Hummingbot 內部使用的標準化 OrderBookMessage 格式,
        然後將處理後的快照消息放入一個指定的訊息佇列 (message_queue) 中，供後續模組使用

        :param raw_message: The raw JSON message received from the WebSocket.
        :param message_queue: Queue to push parsed messages.
        """
        if "b" in raw_message and "a" in raw_message:
            # Extract and normalize depth data
            bids = raw_message["b"]  # Example: [["price", "quantity"], ...]
            asks = raw_message["a"]  # Example: [["price", "quantity"], ...]

            trading_pair = raw_message['symbol']
            update_id = int(time.time())

            # 構建訂單簿消息內容
            order_book_message_content = {
                "trading_pair": trading_pair,  # 'BTCUSDT'
                "update_id": update_id,
                "bids": [{"price": float(bid[0]), "quantity": float(bid[1])} for bid in bids],
                "asks": [{"price": float(ask[0]), "quantity": float(ask[1])} for ask in asks],
            }
            # 創建標準化訂單簿消息
            diff_message: OrderBookMessage = OrderBookMessage(
                OrderBookMessageType.SNAPSHOT,
                order_book_message_content,
                int(time.time()))
            # 用於存儲處理完成的標準化快照消息
            message_queue.put_nowait(diff_message)

    # 1 獲取訂單簿快照返回:OrderBookMessage ***對象*** -> REST API
    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        # trading_pair => 'BTC-USDT'
        try:
            snapshot_response: Dict[str, Any] = await self._request_order_book_snapshot(trading_pair)
            snapshot_data: Dict[str, Any] = snapshot_response["data"]
            snapshot_timestamp: int = int(time.time() * 1000)
            update_id: int = int(time.time() * 1000)

            order_book_message_content = {
                "trading_pair": trading_pair,  # 'BTCUSDT'
                "update_id": update_id,
                "bids": [(bid[0], bid[1]) for bid in snapshot_data["b"]],
                "asks": [(ask[0], ask[1]) for ask in snapshot_data["a"]],
            }

            snapshot_msg: OrderBookMessage = OrderBookMessage(
                OrderBookMessageType.SNAPSHOT,
                order_book_message_content,
                snapshot_timestamp)

            return snapshot_msg
        except Exception as e:
            write_logs(f"_order_book_snapshot : Error => {e}")

    # 2 通過 REST API 獲取交易對的完整訂單簿快照 -> REST API
    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Retrieves a copy of the full order book from the exchange, for a particular trading pair.

        :param trading_pair: the trading pair for which the order book will be retrieved

        :return: the response from the exchange (JSON dictionary)
        """
        # /api/v1/market/depth/BTCUSDT
        endpoint = CONSTANTS.GET_ORDER_BOOK_PATH_URL + f'/{trading_pair.replace("-", "").upper()}'
        params = {
            'depth': 10
        }
        url = web_utils.public_rest_url(path_url=endpoint)
        try:
            # 驗證 REST Assistant
            rest_assistant = await self._api_factory.get_rest_assistant()
            data = await rest_assistant.execute_request(
                url=url,
                params=params,
                method=RESTMethod.GET,
                throttler_limit_id=CONSTANTS.GET_ORDER_BOOK_PATH_URL,
            )

            return data
        except Exception as e:
            write_logs(f"_request_order_book_snapshot : Error => {e}")

    # === ****** coinstore 還末測試到 ===
    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        """
        解析 Coinstore 的逐筆交易資料，將其轉換為 Hummingbot 的 OrderBookMessage 並加入訊息佇列。
        """
        trade_updates = raw_message.get("data", [])  # 獲取交易資料清單
        for trade_data in trade_updates:
            try:
                # 從 symbol 欄位獲取交易對，轉換為標準化格式: "BTCUSDT" => "BTC-USDT"
                trading_pair = trade_data["symbol"]
                # 構造消息內容
                message_content = {
                    "trade_id": int(trade_data["tradeId"]),  # 交易 ID
                    "trading_pair": trading_pair.upper(),  # 標準化交易對
                    "trade_type": float(TradeType.BUY.value)
                        if trade_data["takerSide"].lower() in {"buy", "bull"}
                        else float(TradeType.SELL.value),  # 根據 takerSide 確定買賣方向
                    "amount": Decimal(trade_data["volume"]),  # 交易量
                    "price": Decimal(trade_data["price"])  # 成交價格
                }
                # 構造 OrderBookMessage 物件
                trade_message: Optional[OrderBookMessage] = OrderBookMessage(
                    message_type=OrderBookMessageType.TRADE,  # 消息類型為 TRADE
                    content=message_content,  # 消息內容
                    timestamp=int(trade_data["time"])  # 時間戳記（單位秒）
                )
                # 將消息推送到訊息佇列
                message_queue.put_nowait(trade_message)
            except Exception as e:
                write_logs(f"_parse_trade_message : Error => {e}")

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        # Coinstore never sends diff messages. This method will never be called
        pass

    # 5 通過 WebSocket 訂閱交易所的公共資料頻道，以接收交易對相關的即時資料
    async def _subscribe_channels(self, ws: WSAssistant):
        """
                成交頻道（Trades Channel）: 接收即時成交資料。
                訂單簿頻道（Order Book Depth Channel）: 接收即時訂單簿增量更新。
                :param ws:
                :return:
                """
        try:
            # 例:['BTCUSDT@ticker']
            channels = [f"{pair}{CONSTANTS.PUBLIC_TRADE_CHANNEL_NAME}" for pair in self._trading_pairs]
            payload = {
                "op": "SUB",
                "channel": channels,  # 替換為實際訂閱參數
                "id": 1
            }
            subscribe_trade_request: WSJSONRequest = WSJSONRequest(payload=payload)
            # 例:['BTCUSDT@depth@50']
            channels = [f"{pair}{CONSTANTS.PUBLIC_DEPTH_CHANNEL_NAME}" for pair in self._trading_pairs]
            payload = {
                "op": "SUB",
                "channel": channels,  # 替換為實際訂閱參數
                "id": 1
            }
            subscribe_orderbook_request: WSJSONRequest = WSJSONRequest(payload=payload)
            async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_SUBSCRIBE):
                await ws.send(subscribe_trade_request)

            async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_SUBSCRIBE):
                await ws.send(subscribe_orderbook_request)
            self.logger().info("Subscribed to public order book and trade channels...")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            write_logs(f"_subscribe_channels : Error => {e}")
            raise

    # 6 處理從 WebSocket 接收的即時消息
    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        async for ws_response in websocket_assistant.iter_messages():
            data: Dict[str, Any] = ws_response.data
            try:
                decompressed_data = utils.decompress_ws_message(data)
                if type(decompressed_data) == str:
                    json_data = json.loads(decompressed_data)
                else:
                    json_data = decompressed_data
            except Exception as e:
                write_logs(f"_process_websocket_messages : json_data_Error => {e}")
                continue

            if json_data.get('C') == 200 or 'instrumentId' in str(json_data):
                pass
            else:
                raise ValueError(f"Error message received in the order book data source: {json_data}")

            try:
                channel: str = self._channel_originating_message(event_message=json_data)
            except Exception as e:
                write_logs(f'_process_websocket_messages : channel_Error => {e}')
                continue

            if channel in [self._diff_messages_queue_key, self._trade_messages_queue_key]:
                self._message_queue[channel].put_nowait(json_data)

    # # 7 - 9 判斷頻道
    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        """
                從接收到的 WebSocket 消息中提取頻道資訊，並根據不同的頻道類型返回對應的佇列識別字。
                :param event_message:
                :return:
                """
        channel = ""
        if "instrumentId" in event_message:
            event_channel = event_message["T"]
            if 'ticker' in event_channel:
                channel = self._trade_messages_queue_key  # 按 Symbol 的 Ticker信息
            if 'depth' == event_channel:
                channel = self._diff_messages_queue_key  # 深度資訊頻道

        return channel

    # 4 建立 WebSocket 連接，並返回一個 WSAssistant, 用於與交易所進行即時通信  ○○○○○○。
    async def _connected_websocket_assistant(self) -> WSAssistant:
        try:
            ws: WSAssistant = await self._api_factory.get_ws_assistant()
            async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_CONNECT):
                await ws.connect(
                    ws_url=CONSTANTS.WSS_PUBLIC_URL,
                    ping_timeout=CONSTANTS.WS_PING_TIMEOUT)
            return ws
        except Exception as e:
            write_logs(f'_connected_websocket_assistant : ws_Error => {e}')

