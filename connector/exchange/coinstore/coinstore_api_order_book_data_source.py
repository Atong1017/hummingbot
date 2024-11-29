import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import websockets
import time
# import sys
#
# hummingbot_path = r"D:\Python\hummingbot"  # 替換為你本地的 hummingbot 資料夾路徑
# sys.path.append(hummingbot_path)

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
import requests

if TYPE_CHECKING:
    from hummingbot.connector.exchange.coinstore.coinstore_exchange import CoinstoreExchange


class CoinstoreAPIOrderBookDataSource(OrderBookTrackerDataSource):

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 trading_pairs: List[str],
                 connector: 'CoinstoreExchange',
                 api_factory: WebAssistantsFactory):
        super().__init__(trading_pairs)
        self._connector: CoinstoreExchange = connector
        self._api_factory = api_factory

        self.ws_url = "wss://ws.coinstore.com/s/ws"
        self.channel = "4@depth@20"  # 替换为实际需要的交易对和深度档位
        self._message_queue = asyncio.Queue()

    # === coinstore 最新價格 ===
    async def get_last_traded_prices(self,
                                     trading_pairs: List[str],
                                     domain: Optional[str] = None) -> Dict[str, float]:
        url_price = 'https://api.coinstore.com/api/v1/ticker/price'
        response_ticker = requests.request("GET", url_price).text
        data_ticker = json.loads(response_ticker)['data']

        results = {}
        for trading_pair in trading_pairs:
            symbol = trading_pair.replace("-", "").replace("_", "").upper()
            price = [a for a in data_ticker if a['symbol'] == symbol]
            if price:
                results[trading_pair] = float(price[0]["price"])  # 获取第一个匹配项的价格
            else:
                results[trading_pair] = None  # 如果没有找到价格，设置为 None

        return results

    async def listen_for_order_book_diffs(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Coinstore only sends full snapshots, they never send diff messages. That is why this method is overwritten to
        do nothing.

        :param ev_loop: the event loop the method will run in
        :param output: a queue to add the created diff messages
        """
        pass

    # === coinstore ===
    async def listen_for_order_book_snapshots(self, ev_loop: asyncio.AbstractEventLoop, output: asyncio.Queue):
        """
        Listen for order book snapshots and updates from Coinstore.

        :param ev_loop: Event loop.
        :param output: Queue to push parsed messages.
        """
        async with websockets.connect(self.ws_url) as ws:
            # Subscribe to the depth channel
            subscribe_message = {
                "op": "SUB",
                "channel": [self.channel],
                "id": 1
            }
            await ws.send(json.dumps(subscribe_message))

            while True:
                try:
                    # Receive and process messages
                    raw_message = await ws.recv()
                    message = json.loads(raw_message)
                    await self._parse_order_book_snapshot_message(message, output)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.exception(f"Error processing message: {str(e)}")
                    continue

    # === coinstore ===
    async def _parse_order_book_snapshot_message(self, raw_message: Dict, message_queue: asyncio.Queue):
        """
        Parse the received order book snapshot message and add it to the output queue.

        :param raw_message: The raw JSON message received from the WebSocket.
        :param message_queue: Queue to push parsed messages.
        """
        if "b" in raw_message and "a" in raw_message:
            # Extract and normalize depth data
            bids = raw_message["b"]  # Example: [["price", "quantity"], ...]
            asks = raw_message["a"]  # Example: [["price", "quantity"], ...]

            parsed_data = {
                "bids": [{"price": float(bid[0]), "quantity": float(bid[1])} for bid in bids],
                "asks": [{"price": float(ask[0]), "quantity": float(ask[1])} for ask in asks],
                "instrument_id": raw_message.get("instrumentId"),
                "symbol": raw_message.get("symbol"),
                "level": raw_message.get("level"),
            }
            await message_queue.put(parsed_data)

    # === coinstroe ===
    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        snapshot_response: Dict[str, Any] = await self._request_order_book_snapshot(trading_pair)
        snapshot_data: Dict[str, Any] = snapshot_response["data"]

        snapshot_timestamp: int = int(time.time() * 1000)
        update_id: int = int(time.time() * 1000)

        order_book_message_content = {
            "trading_pair": trading_pair,
            "update_id": update_id,
            "bids": [(bid[0], bid[1]) for bid in snapshot_data["b"]],
            "asks": [(ask[0], ask[1]) for ask in snapshot_data["a"]],
        }

        snapshot_msg: OrderBookMessage = OrderBookMessage(
            OrderBookMessageType.SNAPSHOT,
            order_book_message_content,
            snapshot_timestamp)

        return snapshot_msg

    # === coinstrore ===
    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Retrieves a copy of the full order book from the exchange, for a particular trading pair.

        :param trading_pair: the trading pair for which the order book will be retrieved

        :return: the response from the exchange (JSON dictionary)
        """
        # 例:trading_pair='BTCUSDT'

        # /api/v1/market/depth/BTCUSDT
        endpoint = CONSTANTS.GET_ORDER_BOOK_PATH_URL + trading_pair.upper()
        params = {
            'depth': 10
        }

        # https://api.coinstore.com/api/v1/market/depth/BTCUSDT
        url = web_utils.public_rest_url(path_url=endpoint)

        data = requests.get(url, params=params).json()

        return data

    # === 還未更改 ===
    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        trade_updates = raw_message["data"]

        for trade_data in trade_updates:
            trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=trade_data["symbol"])
            message_content = {
                "trade_id": int(trade_data["s_t"]),
                "trading_pair": trading_pair.replace("-", "").replace("_", "").upper(),
                "trade_type": float(TradeType.BUY.value) if trade_data["side"] == "buy" else float(
                    TradeType.SELL.value),
                "amount": trade_data["size"],
                "price": trade_data["price"]
            }
            trade_message: Optional[OrderBookMessage] = OrderBookMessage(
                message_type=OrderBookMessageType.TRADE,
                content=message_content,
                timestamp=int(trade_data["s_t"]))

            message_queue.put_nowait(trade_message)

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        # Coinstore never sends diff messages. This method will never be called
        pass

    # === coinstre ===
    async def _subscribe_channels(self, ws: WSAssistant):
        try:
            symbols = [await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                       for trading_pair in self._trading_pairs]
            channels = [f"{pair}{CONSTANTS.PUBLIC_TRADE_CHANNEL_NAME}" for pair in symbols]
            payload = {
                "op": "SUB",
                "channel": channels,  # 替换为实际订阅参数
                "id": 1
            }
            subscribe_trade_request: WSJSONRequest = WSJSONRequest(payload=payload)

            channels = [f"{pair}{CONSTANTS.PUBLIC_DEPTH_CHANNEL_NAME}" for pair in symbols]
            payload = {
                "op": "SUB",
                "channel": channels,  # 替换为实际订阅参数
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
        except Exception:
            self.logger().exception("Unexpected error occurred subscribing to order book trading and delta streams...")
            raise

    # ===
    async def _process_websocket_messages(self, websocket_assistant: WSAssistant):
        async for ws_response in websocket_assistant.iter_messages():
            data: Dict[str, Any] = ws_response.data
            decompressed_data = utils.decompress_ws_message(data)

            try:
                if type(decompressed_data) == str:
                    json_data = json.loads(decompressed_data)
                else:
                    json_data = decompressed_data
            except Exception:

                continue

            # ================================================
            # ================================================
            if json_data.get("M") != "established":
                raise ValueError(f"Error message received in the order book data source: {json_data}")

            channel: str = self._channel_originating_message(event_message=json_data)
            if channel in [self._diff_messages_queue_key, self._trade_messages_queue_key]:

                # ==========================
                # ========這裡有錯==========
                # ==========================
                self._message_queue[channel].put_nowait(json_data)

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        channel = ""
        if "data" in event_message:
            event_channel = event_message["table"]
            if event_channel == CONSTANTS.PUBLIC_TRADE_CHANNEL_NAME:
                channel = self._trade_messages_queue_key
            if event_channel == CONSTANTS.PUBLIC_DEPTH_CHANNEL_NAME:
                channel = self._diff_messages_queue_key

        return channel

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_CONNECT):
            await ws.connect(
                ws_url=CONSTANTS.WSS_PUBLIC_URL,
                ping_timeout=CONSTANTS.WS_PING_TIMEOUT)
        return ws


