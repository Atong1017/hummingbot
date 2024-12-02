import asyncio
import math
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import logging
from bidict import bidict
import json, requests

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.coinstore import (
    coinstore_constants as CONSTANTS,
    coinstore_utils,
    coinstore_web_utils as web_utils,
)
from hummingbot.connector.exchange.coinstore.coinstore_api_order_book_data_source import CoinstoreAPIOrderBookDataSource
from hummingbot.connector.exchange.coinstore.coinstore_api_user_stream_data_source import \
    CoinstoreAPIUserStreamDataSource
from hummingbot.connector.exchange.coinstore.coinstore_auth import CoinstoreAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter

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
    log_entry = f"{current_time} - coinstore_exchange - {text}"

    with open(LOG_FILE_PATH, "a") as test_file:
        test_file.write(log_entry + '\n')


class CoinstoreExchange(ExchangePyBase):
    """
   CoinstoreExchange connects with Coinstore exchange and provides order book pricing, user account tracking and
    trading functionality.
    """
    API_CALL_TIMEOUT = 10.0
    POLL_INTERVAL = 1.0
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0
    UPDATE_TRADE_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils

    def __init__(self,
                 client_config_map: "ClientConfigAdapter",
                 coinstore_api_key: str,
                 coinstore_secret_key: str,
                 trading_pairs: Optional[List[str]] = None,
                 trading_required: bool = True,
                 ):
        """
        :param coinstore_api_key: The API key to connect to private Coinstore APIs.
        :param coinstore_secret_key: The API secret.
        :param trading_pairs: The market trading pairs which to track order book data.
        :param trading_required: Whether actual trading is needed.
        """
        self._api_key: str = coinstore_api_key
        self._secret_key: str = coinstore_secret_key
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs

        super().__init__(client_config_map)
        self.real_time_balance_update = False

        # === coinstore 預設預設值 ===
        self.cached_maker_fee_pct = Decimal("0.001")  # 默認 maker 手續費率 0.1%
        self.cached_taker_fee_pct = Decimal("0.002")  # 默認 taker 手續費率 0.2%

    @property
    def authenticator(self):
        csat = CoinstoreAuth(
            api_key=self._api_key,
            secret_key=self._secret_key,
            time_provider=self._time_synchronizer)

        return csat

    @property
    def name(self) -> str:
        return "coinstore"

    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self):
        return CONSTANTS.DEFAULT_DOMAIN

    @property
    def client_order_id_max_length(self):
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self):
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self):
        return CONSTANTS.GET_TRADING_RULES_PATH_URL

    @property
    def trading_pairs_request_path(self):
        return CONSTANTS.GET_LAST_TRADING_PRICES_PATH_URL

    @property
    def check_network_request_path(self):
        return CONSTANTS.CHECK_NETWORK_PATH_URL

    @property
    def trading_pairs(self):
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def supported_order_types(self) -> List[OrderType]:
        """
        :return a list of OrderType supported by this connector.
        Note that Market order type is no longer required and will not be used.
        """
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER]

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        error_description = str(request_exception)
        is_time_synchronizer_related = ("Header X-CS-EXPIRES" in error_description
                                        and ("30007" in error_description or "30008" in error_description))

        # write_logs('_is_request_exception_related_to_time_synchronizer=>' + str(is_time_synchronizer_related))
        return is_time_synchronizer_related

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        # TODO: implement this method correctly for the connector
        # The default implementation was added when the functionality to detect not found orders was introduced in the
        # ExchangePyBase class. Also fix the unit test test_lost_order_removed_if_not_found_during_order_status_update
        # when replacing the dummy implementation
        return False

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        # TODO: implement this method correctly for the connector
        # The default implementation was added when the functionality to detect not found orders was introduced in the
        # ExchangePyBase class. Also fix the unit test test_cancel_order_not_found_in_the_exchange when replacing the
        # dummy implementation
        return False

    # login 3 =>
    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        try:
            cwaf = web_utils.build_api_factory(
                throttler=self._throttler,
                time_synchronizer=self._time_synchronizer,
                auth=self._auth)

            return cwaf
        except Exception as e:
            write_logs(f"_create_web_assistants_factory : Error => {e}")

    # === 用來處理交易所的訂單簿資料來源 ===
    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        """
        訂單簿資料來源負責從交易所獲取訂單簿快照和更新資訊，為交易策略提供即時的市場深度資料。
        :return:
        """
        try:
            trading_pairs = [a.replace('-', '').replace('_', '').upper() for a in self._trading_pairs]
            capiobs = CoinstoreAPIOrderBookDataSource(
                trading_pairs=trading_pairs,
                connector=self,
                api_factory=self._web_assistants_factory)

            return capiobs
        except Exception as e:
            write_logs(f"_create_order_book_data_source : Error => {e}")

    # 1 === 處理與用戶相關的即時資料流 ===
    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        """
        這個資料來源負責與交易所的用戶流 WebSocket 連接，監控並收集與當前用戶帳戶相關的事件，供交易策略和風險管理模組使用。
        :return:
        """
        try:
            trading_pairs = [a.replace('-', '').replace('_', '').upper() for a in self._trading_pairs]

            cusds = CoinstoreAPIUserStreamDataSource(
                auth=self._auth,
                trading_pairs=trading_pairs,
                connector=self,
                api_factory=self._web_assistants_factory, )

            return cusds
        except Exception as e:
            write_logs(f"_create_user_stream_data_source : Error => {e}")

    # ============== coinstore 手續費 ===============
    async def _get_fee(self,
                       base_currency: str,
                       quote_currency: str,
                       order_type: OrderType,
                       order_side: TradeType,
                       amount: Decimal,
                       price: Decimal = Decimal("NaN"),
                       is_maker: Optional[bool] = None) -> AddedToCostTradeFee:
        """
            Get trading fee from Coinstore API using parameters compatible with the original method.
            """
        api_params = {
            "symbol": f"{base_currency}{quote_currency}",
        }
        url = '?'
        for key, value in api_params.items():
            url = url + str(key) + '=' + str(value) + '&'

        params_url = url[0:-1]
        try:
            response = await self._api_get(
                path_url=CONSTANTS.GET_TRADE_DETAIL_PATH_URL + params_url,
                params=params_url[1:],
                is_auth_required=True
            )
            trades = response.get("data", [])

            if not trades:
                self.logger().warning("No recent trades found. Using default fee rates.")
                return

            maker_fees = []
            taker_fees = []

            for trade in trades:
                fee_rate = Decimal(trade["acturalFeeRate"])
                match_role = trade["matchRole"]

                if match_role == 1:  # Taker
                    taker_fees.append(fee_rate)
                elif match_role == 2:  # Maker
                    maker_fees.append(fee_rate)

            if maker_fees:
                self.cached_maker_fee_pct = sum(maker_fees) / len(maker_fees)
            if taker_fees:
                self.cached_taker_fee_pct = sum(taker_fees) / len(taker_fees)

        except Exception as e:
            write_logs(f"_get_fee : Error => {e}")
            self.logger().exception("Failed to fetch recent fee rates. Using cached or default values.")

        # Use the `is_maker` flag to determine the fee type
        if is_maker is None:
            raise ValueError("is_maker must be specified when calling _get_fee.")

        fee_pct = self.cached_maker_fee_pct if is_maker else self.cached_taker_fee_pct
        gf = AddedToCostTradeFee(percent=fee_pct)

        return gf

    # ========== coinstroe ==========
    async def _place_order(self,
                           trading_pair: str,
                           amount: Decimal,
                           trade_type: TradeType,
                           order_type: OrderType,
                           price: Decimal,
                           **kwargs) -> Tuple[str, float]:
        """
        Places an order on the exchange.
        :param trading_pair: The trading pair (e.g., "BTC-USDT").
        :param amount: The order amount.
        :param trade_type: TradeType (BUY or SELL).
        :param order_type: OrderType (MARKET, LIMIT, LIMIT_MAKER).
        :param price: The price for the order (ignored for MARKET).
        :return: A tuple containing the exchange order ID and the timestamp.
        """
        try:
            # Map Hummingbot's OrderType to exchange API's ordType
            if order_type == OrderType.MARKET:
                ord_type = "MARKET"
                ord_price = None  # Market orders do not require a price
            elif order_type == OrderType.LIMIT:
                ord_type = "LIMIT"
                ord_price = f"{price:f}"
            elif order_type == OrderType.LIMIT_MAKER:
                ord_type = "POST_ONLY"
                ord_price = f"{price:f}"
            else:
                raise ValueError(f"Unsupported order type: {order_type}")

            # Construct API parameters
            api_params = {
                # "symbol": await self.exchange_symbol_associated_to_pair(trading_pair),
                "symbol": trading_pair,
                "side": trade_type.name.upper(),  # BUY or SELL
                "ordType": ord_type,
                "ordQty": f"{amount:f}"
            }

            # Add price only if required by the order type
            if ord_price is not None:
                api_params["ordPrice"] = ord_price

            # Send the API request to place the order
            order_result = await self._api_post(
                path_url=CONSTANTS.CREATE_ORDER_PATH_URL,
                data=api_params,
                is_auth_required=True
            )
            # Extract order ID from the response
            exchange_order_id = str(order_result["data"]["ordId"])

            # Return the order ID and the current timestamp
            return exchange_order_id, self.current_timestamp
        except Exception as e:
            write_logs(f"_place_order : Error => {e}")

    # ========== coinstroe ==========
    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        try:
            symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
            api_params = {
                "symbol": symbol,
                "ordId": order_id,
            }
            cancel_result = await self._api_post(
                path_url=CONSTANTS.CANCEL_ORDER_PATH_URL,
                data=api_params,
                is_auth_required=True)
            if cancel_result.get("data", {}).get("state") == "CANCELED":
                return True
            return False
        except Exception as e:
            write_logs(f"_place_cancel : Error => {e}")

    # 3 ========== coinstroe 沒有交易規則，自建預設規則。 ========== 1
    async def _format_trading_rules(self, symbols_details: Dict[str, Any]) -> List[TradingRule]:
        if not symbols_details['data']:
            url = "https://api.coinstore.com/api/v2/public/config/spot/symbols"
            payload = json.dumps({})
            payload = payload.encode("utf-8")
            headers = {}
            exchange_info = requests.request("POST", url, headers=headers, data=payload).text
            symbols_details = json.loads(exchange_info)

        result = []
        try:
            # 遍歷返回的交易對詳情
            for rule in symbols_details.get("data", []):  # 直接獲取 data 列表
                try:
                    if rule['openTrade']:
                        # BTCUSDT => BTC-USDT
                        trading_pair = await self.trading_pair_associated_to_exchange_symbol(rule["symbolCode"].upper())
                    else:
                        continue
                    min_order_size = Decimal(rule.get("minLmtSz", "0.00001"))  # "minLmtSz" 是最低限價訂單大小
                    min_order_value = Decimal(rule.get("minMktVa", "0.1"))  # "minMktVa" 是最低市價訂單價值
                    min_base_amount_increment = Decimal(rule.get("minLmtPr", "0.00000001"))  # "minLmtPr" 是最低限價訂單價格
                    min_price_increment = Decimal(rule.get("minLmtPr", "0.00000001"))  # "minLmtPr" 是最低限價訂單價格

                    # 構造 TradingRule 物件
                    result.append(TradingRule(
                        trading_pair=trading_pair,
                        min_order_size=min_order_size,
                        min_order_value=min_order_value,
                        min_base_amount_increment=min_base_amount_increment,
                        min_price_increment=min_price_increment
                    ))
                except Exception as e:
                    write_logs(f"_format_trading_rules : Error => {e}")
                    self.logger().exception(f"Error parsing the trading pair rule {rule}. Skipping.")

        except Exception as e:
            write_logs(f"_format_trading_rules : Error2 => {e}")
            self.logger().warning("Symbols details do not contain valid trading rules.")

        return result

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange
        """
        pass

    # ============== coinstore balance ==============
    async def _update_balances(self):
        """
        Calls REST API to update total and available balances.
        """
        local_asset_names = set(self._account_balances.keys())
        remote_asset_names = set()
        payload = {}
        account_info = await self._api_post(
            path_url=CONSTANTS.GET_ACCOUNT_SUMMARY_PATH_URL,
            data=payload,
            is_auth_required=True
        )

        account_balances_temp = {}
        for account in account_info["data"]:
            asset_name = account.get("currency")  # 獲取資產名稱
            balance = account.get("balance", 0)  # 獲取資產餘額
            account_type = account.get("typeName")  # 獲取帳戶類型

            if not asset_name:  # 確保資產名稱存在
                continue

            if account_type == "AVAILABLE":
                self._account_available_balances[asset_name] = Decimal(balance)
                try:
                    self._account_balances[asset_name] += Decimal(balance)
                    account_balances_temp[asset_name] += Decimal(balance)
                except:
                    self._account_balances[asset_name] = Decimal(balance)
                    account_balances_temp[asset_name] = Decimal(balance)

            elif account_type == "FROZEN":
                try:
                    account_balances_temp[asset_name] += Decimal(balance)
                except:
                    account_balances_temp[asset_name] = Decimal(balance)

            self._account_balances[asset_name] = account_balances_temp[asset_name]
            remote_asset_names.add(asset_name)
        asset_names_to_remove = local_asset_names.difference(remote_asset_names)

        for asset_name in asset_names_to_remove:
            del self._account_available_balances[asset_name]
            del self._account_balances[asset_name]

    # ========== coinstore 獲取單一訂單資訊 InFlightOrder -> Dict ===========
    async def _request_order_update(self, order: InFlightOrder) -> Dict[str, Any]:
        # tracked_order=data
        # 獲取訂單資訊 GET /api/v2/trade/order/orderInfo?ordId=123456789
        params_url = f"?ordId={order.exchange_order_id}"
        try:
            result = await self._api_get(
                path_url=CONSTANTS.GET_ORDER_DETAIL_PATH_URL + params_url,
                params=params_url[1:],
                is_auth_required=True)
            return result
        except Exception as e:
            write_logs(f"_request_order_update : Error => {e}")

    # ======== coinstore 獲取用戶最新成交 =========
    async def _request_order_fills(self, order: InFlightOrder) -> Dict[str, Any]:
        symbol = await self.exchange_symbol_associated_to_pair(order.trading_pair)
        api_params = {
            "symbol": symbol,
        }
        url = '?'
        for key, value in api_params.items():
            url = url + str(key) + '=' + str(value) + '&'
        params_url = url[0:-1]

        try:
            result = await self._api_get(
                path_url=CONSTANTS.GET_TRADE_DETAIL_PATH_URL + params_url,
                params=params_url[1:],
                is_auth_required=True)

            return result
        except Exception as e:
            write_logs(f"_request_order_fills : Error => {e}")

    # ===== coinstore 更新所有交易訂單 =====未實測
    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        # write_logs(f'_all_trade_updates_for_order')
        trade_updates = []
        try:
            if order.exchange_order_id is not None:
                # 獲取用戶最新成交
                all_fills_response = await self._request_order_fills(order=order)  # Dict
                # 訂單更新
                updates = self._create_order_fill_updates(order=order, fill_update=all_fills_response)
                # write_logs(f"_all_trade_updates_for_order : updates => {updates}")
                trade_updates.extend(updates)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            # write_logs(f"_request_order_fills : Error => {ex}")
            is_error_caused_by_unexistent_order = '"code":50005' in str(ex)
            if not is_error_caused_by_unexistent_order:
                raise

        return trade_updates

    # === coinstore ===未實測
    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        # write_logs(f'_request_order_status')
        updated_order_data = await self._request_order_update(order=tracked_order)  # =>InFlightOrder => respone(dict)
        order_update = self._create_order_update(order=tracked_order, order_update=updated_order_data)
        # write_logs(f"_request_order_status : order_update => {order_update}")
        return order_update

    # === coinstore 訂單更新===
    async def _create_order_fill_updates(self, order: InFlightOrder, fill_update: Dict[str, Any]) -> List[TradeUpdate]:
        """
        將交易所返回的成交記錄資料（fill_update，通常是 JSON 格式的字典）轉換為 Hummingbot 的標準 TradeUpdate 物件清單
        :param order:
        :param fill_update:
        :return:
        """
        # 獲取當前訂單:baseCurrency, quoteCurrency
        order_url = "/api/v2/trade/order/active"
        try:
            orders_info = await self._api_get(path_url=order_url, params='', is_auth_required=True)  # Dict
        except Exception as e:
            write_logs(f"_create_order_fill_updates : orders_info_Error => {e}")
            return []  # 或者根據情況返回空清單或做其他處理

        currency_mapping = {}
        matches_url = '/trade/match/accountMatches'
        for order_info in orders_info.get('data', []):
            base_currency = order_info.get('baseCurrency')
            quote_currency = order_info.get('quoteCurrency')
            symbol = base_currency + quote_currency

            # 獲取用戶最新成交:其中包含baseCurrencyId, quoteCurrencyId
            try:
                account_matches = await self._api_get(
                    path_url=matches_url + f'?symbol={symbol}',
                    params=f'symbol={symbol}',
                    is_auth_required=True
                )
            except Exception as e:
                write_logs(f"_create_order_fill_updates : account_matches_Error => {e}")
                continue

            if account_matches.get('data'):
                base_currency_id = account_matches['data'][0].get('baseCurrencyId')
                quote_currency_id = account_matches['data'][0].get('quoteCurrencyId')
            else:
                # 處理沒有成交資料的情況，或者給予一個預設值
                base_currency_id = quote_currency_id = None

            # 將幣種 ID 映射到名稱
            if base_currency_id and base_currency:
                currency_mapping[base_currency_id] = base_currency
            if quote_currency_id and quote_currency:
                currency_mapping[quote_currency_id] = quote_currency

        updates = []
        fills_data = fill_update["data"]
        for fill_data in fills_data:
            base_currency = currency_mapping.get(fill_data["baseCurrencyId"], "UNKNOWN")
            quote_currency = currency_mapping.get(fill_data["quoteCurrencyId"], "UNKNOWN")
            fee_currency = currency_mapping.get(fill_data["feeCurrencyId"], "UNKNOWN")

            try:
                fee = TradeFeeBase.new_spot_fee(
                    fee_schema=self.trade_fee_schema(),
                    trade_type=order.trade_type,
                    percent_token=fee_currency,
                    flat_fees=[TokenAmount(amount=Decimal(fill_data["fee"]), token=fee_currency)]
                )
                # 確保 execQty 不為零
                fill_price = Decimal(0)
                if Decimal(fill_data["execQty"]) != 0:
                    fill_price = Decimal(fill_data["execAmt"]) / Decimal(fill_data["execQty"])

                trade_update = TradeUpdate(
                    trade_id=str(fill_data["tradeId"]),
                    client_order_id=order.client_order_id,
                    exchange_order_id=str(fill_data["orderId"]),
                    trading_pair=f"{base_currency}-{quote_currency}",
                    fee=fee,
                    fill_base_amount=Decimal(fill_data["execQty"]),
                    fill_quote_amount=Decimal(fill_data["execAmt"]),
                    fill_price=fill_price,
                    fill_timestamp=int(fill_data["matchTime"]),
                )
                updates.append(trade_update)
            except Exception as e:
                write_logs(f"_create_order_fill_updates : updates_Error => {e}")

        # write_logs(f"_create_order_fill_updates : updates => {updates}")
        return updates

    # === coinstore ===
    def _create_order_update(self, order: InFlightOrder, order_update: Dict[str, Any]) -> OrderUpdate:
        order_data = order_update["data"]
        new_state = CONSTANTS.ORDER_STATE[order_data["ordStatus"]]
        try:
            update = OrderUpdate(
                client_order_id=order.client_order_id,
                exchange_order_id=str(order_data["ordId"]),
                trading_pair=order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=new_state,
            )

            return update
        except Exception as e:
            write_logs(f"_create_order_update : Error => {e}")

    """    """

    # ==== coinstore 查單沒有websocket版，改用rest api定時抓 ====
    async def _user_stream_event_listener(self):
        """
        Periodically checks active orders and updates their status by calling the Coinstore API.
        """
        while True:
            try:
                # 獲取當前所有活躍訂單的資料
                active_orders_response = await self._api_get(
                    path_url=CONSTANTS.GET_ORDER_DETAIL_PATH_URL,  # 替換為對應的 API 路徑
                    params={},  # 你可以在這裡傳遞必要的參數
                    is_auth_required=True
                )
                # 遍歷每一個訂單並更新
                for order_data in active_orders_response.get('data', []):
                    try:
                        # 提取每個訂單的資訊
                        client_order_id = order_data["clOrdId"]
                        exchange_order_id = str(order_data["ordId"])  # 'ordId' 是訂單 ID，轉換為字串
                        trading_pair = order_data["symbol"]
                        order_type = OrderType[order_data["ordType"]]  # 根據 'ordType' 來設置 order_type
                        trade_type = TradeType[order_data["side"]]  # 根據 'side' 來設置 trade_type
                        amount = Decimal(order_data["ordQty"])  # 'ordQty' 是數量，轉換為 Decimal
                        creation_timestamp = order_data["timestamp"]  # 'timestamp' 是創建時間
                        # 'ordPrice' 是價格，轉換為 Decimal
                        price = Decimal(order_data["ordPrice"]) if order_data["ordPrice"] else None
                        initial_state = OrderState[order_data["ordStatus"]]  # 根據 'ordStatus' 來設置 initial_state

                        # 創建 InFlightOrder 物件
                        in_flight_order = InFlightOrder(
                            client_order_id=client_order_id,
                            trading_pair=trading_pair,
                            order_type=order_type,
                            trade_type=trade_type,
                            amount=amount,
                            creation_timestamp=creation_timestamp,
                            price=price,
                            exchange_order_id=exchange_order_id,
                            initial_state=initial_state
                        )

                        # 訂單更新狀態
                        order_update = await self._request_order_status(in_flight_order)

                        if order_update:
                            # 根據 API 返回的訂單狀態更新
                            new_state = order_update["ordStatus"]
                            order_update = OrderUpdate(
                                client_order_id=client_order_id,
                                exchange_order_id=exchange_order_id,
                                trading_pair=trading_pair,
                                update_timestamp=int(time.time() * 1000),  # 使用當前時間戳記
                                new_state=new_state
                            )
                            # 處理訂單狀態更新
                            a = self._order_tracker.process_order_update(order_update=order_update)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        write_logs(f"_user_stream_event_listener : Error1 => {e}")
                        self.logger().exception(f"Error processing order update for order {order_data}. Skipping.")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                write_logs(f"_user_stream_event_listener : Error2 => {e}")
                self.logger().exception("Error in user stream listener loop.")

            # 每次輪詢後設置延遲（每秒最多40個請求）
            await asyncio.sleep(0.25)  # 設置延遲為0.25秒

    # 2 === coinstore -> hummingbot 幣對比照 ===
    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        try:
            # exchange_info只推get值，post無資料，自建幣對
            url = "https://api.coinstore.com/api/v2/public/config/spot/symbols"
            payload = json.dumps({})
            payload = payload.encode("utf-8")
            headers = {}
            exchange_info = requests.request("POST", url, headers=headers, data=payload).text
            exchange_info = json.loads(exchange_info)

            mapping = bidict()
            for symbol_data in filter(coinstore_utils.is_exchange_information_valid, exchange_info["data"]):
                symbol = symbol_data["symbolCode"].upper()
                mapping[symbol] = combine_to_hb_trading_pair(
                    base=symbol_data["tradeCurrencyCode"].upper(),
                    quote=symbol_data["quoteCurrencyCode"].upper())

            self._set_trading_pair_symbol_map(mapping)

        except Exception as e:
            write_logs(f"_initialize_trading_pair_symbols_from_exchange_info : Error => {e}")

    # === coinstore 最新成交價 ===
    async def _get_last_traded_price(self, trading_pair: str) -> float:
        try:
            resp_json = await self._api_get(
                path_url=CONSTANTS.GET_PRICE_PATH_URL,
                params={}
            )
            for res in resp_json['data']:
                if res['symbol'] == trading_pair.replace("-", ""):
                    write_logs(f"_get_last_traded_price : res => {res}")
                    return float(res['price'])
        except Exception as e:
            write_logs(f"_get_last_traded_price : Error => {e}")

