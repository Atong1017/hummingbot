import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from asyncio import Lock

from hummingbot.connector.exchange.coinstore import coinstore_constants as CONSTANTS, coinstore_utils as utils
from hummingbot.connector.exchange.coinstore.coinstore_auth import CoinstoreAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest, WSResponse
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
    log_entry = f"{current_time} - coinstore_api_user_stream_data_source - {text}"

    with open(LOG_FILE_PATH, "a") as test_file:
        test_file.write(log_entry + '\n')


class CoinstoreAPIUserStreamDataSource(UserStreamTrackerDataSource):

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        auth: CoinstoreAuth,
        trading_pairs: List[str],
        connector: 'CoinstoreExchange',
        api_factory: WebAssistantsFactory
    ):
        super().__init__()
        self._auth: CoinstoreAuth = auth
        self._trading_pairs = trading_pairs
        self._connector = connector
        self._api_factory = api_factory
        self._ws_assistant_lock = Lock()

    # 1 === coinstrore ===
    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        連接並認證交易所的私有 WebSocket，以便通過 WebSocket 介面與交易所進行即時通信
        """
        try:
            ws: WSAssistant = await self._get_ws_assistant()
            await ws.connect(
                ws_url=CONSTANTS.WSS_PRIVATE_URL,
                ping_timeout=CONSTANTS.WS_PING_TIMEOUT)
            channels = [f"{pair}@ticker" for pair in self._trading_pairs]  # 使用 trading_pairs 動態生成頻道清單
            payload = {
                "op": "SUB",
                "channel": channels,
                "id": 1
            }
            login_request: WSJSONRequest = WSJSONRequest(payload=payload)
            async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_SUBSCRIBE):
                await ws.send(login_request)
            response: WSResponse = await ws.receive()
            message = response.data

            if message.get('C') == 200 or 'instrumentId' in str(message):
                pass
            else:
                self.logger().error("Error authenticating the private websocket connection")
                raise IOError(f"Private websocket connection authentication failed ({message})")

            return ws
        except Exception as e:
            write_logs(f"_connected_websocket_assistant : Error => {e}")

    # 3 訂閱交易所的特定私有頻道
    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        try:
            channels = [f"{pair}{CONSTANTS.PRIVATE_ORDER_PROGRESS_CHANNEL_NAME}" for pair in self._trading_pairs]
            payload = {
                "op": "SUB",
                "channel": channels,
                "id": 1
            }
            subscribe_request: WSJSONRequest = WSJSONRequest(payload=payload)
            async with self._api_factory.throttler.execute_task(limit_id=CONSTANTS.WS_SUBSCRIBE):
                await websocket_assistant.send(subscribe_request)
            self.logger().info("Subscribed to private account and orders channels...")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            write_logs(f"_subscribe_channels : Error => {e}")
            self.logger().exception("Unexpected error occurred subscribing to order book trading and delta streams...")
            raise

    # 4 處理從WebSocket接收到的即時消息
    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        """
        處理從WebSocket接收到的即時消息，將其解壓、解析並根據消息內容進一步處理=
        :param websocket_assistant:
        :param queue:
        :return:
        """
        try:
            async for ws_response in websocket_assistant.iter_messages():
                data: Dict[str, Any] = ws_response.data
                decompressed_data = utils.decompress_ws_message(data)
                try:
                    if type(decompressed_data) == str:
                        json_data = json.loads(decompressed_data)
                    else:
                        json_data = decompressed_data
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    write_logs(f"_process_websocket_messages : json_data_Error => {e}")
                    self.logger().warning(f"Invalid event message received through the order book data source "
                                          f"connection ({decompressed_data})")
                    continue

                if json_data.get('C') == 200 or 'instrumentId' in str(json_data):
                    pass
                else:
                    raise ValueError(f"Error message received in the order book data source: {json_data}")

                await self._process_event_message(event_message=json_data, queue=queue)
        except Exception as e:
            write_logs(f"_process_websocket_messages : Error => {e}")

    # 5 === coinstore websockt:instrumentId表示通且有資訊 ===
    async def _process_event_message(self, event_message: Dict[str, Any], queue: asyncio.Queue):
        # 檢查event_message是否符合特定結構要求
        if len(event_message) > 0 and "instrumentId" in event_message:
            # 將event_message放入佇列
            queue.put_nowait(event_message)

    # 2 獲取 WebSocket WSAssistant
    async def _get_ws_assistant(self) -> WSAssistant:
        """
        確保該實例只被初始化一次並在後續使用中複用。這是一個典型的**懶載入模式（lazy initialization）**實現，用於優化資源管理和性能
        :return:
        """
        write_logs(f"_get_ws_assistant")
        async with self._ws_assistant_lock:
            if self._ws_assistant is None:
                try:
                    self._ws_assistant = await self._api_factory.get_ws_assistant()
                except Exception as e:
                    write_logs(f"_get_ws_assistant : except => {e}")
                    self.logger().exception(f"Error initializing WebSocket assistant: {e}")
                    raise
            return self._ws_assistant

