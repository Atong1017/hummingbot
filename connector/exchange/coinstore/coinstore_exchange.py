import asyncio
import math
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import logging
from bidict import bidict

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.coinstore import (
    coinstore_constants as CONSTANTS,
    coinstore_utils,
    coinstore_web_utils as web_utils,
)
from hummingbot.connector.exchange.coinstore.coinstore_api_order_book_data_source import CoinstoreAPIOrderBookDataSource
from hummingbot.connector.exchange.coinstore.coinstore_api_user_stream_data_source import CoinstoreAPIUserStreamDataSource
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

        # === coinstore 預設默認值 ===
        self.cached_maker_fee_pct = Decimal("0.001")  # 默认 maker 手续费率 0.1%
        self.cached_taker_fee_pct = Decimal("0.002")  # 默认 taker 手续费率 0.2%

    @property
    def authenticator(self):
        return CoinstoreAuth(
            api_key=self._api_key,
            secret_key=self._secret_key,
            time_provider=self._time_synchronizer)

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
        return CONSTANTS.GET_TRADING_RULES_PATH_URL

    @property
    def check_network_request_path(self):
        return int(time.time() * 1000)

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

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler,
            time_synchronizer=self._time_synchronizer,
            auth=self._auth)

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return CoinstoreAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory)

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        return CoinstoreAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
        )

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

            self.logger().info(
                f"Updated fee rates: Maker {self.cached_maker_fee_pct}, Taker {self.cached_taker_fee_pct}")

        except Exception:
            self.logger().exception("Failed to fetch recent fee rates. Using cached or default values.")

        is_maker = order_type is OrderType.LIMIT_MAKER
        fee_pct = self.cached_maker_fee_pct if is_maker else self.cached_taker_fee_pct
        return AddedToCostTradeFee(percent=fee_pct)
        # return AddedToCostTradeFee(percent=self.estimate_fee_pct(is_maker))

    # ========== coinstroe ==========
    async def _place_order(self,
                           trading_pair: str,
                           amount: Decimal,
                           trade_type: TradeType,
                           order_type: OrderType,
                           price: Decimal,
                           **kwargs) -> Tuple[str, float]:

        api_params = {"symbol": await self.exchange_symbol_associated_to_pair(trading_pair),
                      "side": trade_type.name.upper(),
                      "ordType": "LIMIT",
                      "ordQty": f"{amount:f}",
                      "ordPrice": f"{price:f}",
                      }
        order_result = await self._api_post(
            path_url=CONSTANTS.CREATE_ORDER_PATH_URL,
            data=api_params,
            is_auth_required=True)
        exchange_order_id = str(order_result["data"]["ordId"])

        return exchange_order_id, self.current_timestamp

    # ========== coinstroe ==========
    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
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

    # ========== coinstroe 沒有交易規則，自建默認規則。需再測試 ==========
    async def _format_trading_rules(self, symbols_details: Dict[str, Any]) -> List[TradingRule]:
        result = []

        try:
            # 遍历返回的交易对详情
            for rule in symbols_details.get("data", []):  # 直接获取 data 列表
                try:
                    trading_pair = await self.trading_pair_associated_to_exchange_symbol(rule["symbol"])

                    # 从参数提取规则
                    min_order_size = Decimal(rule.get("baseMinSize", "0.00001"))
                    min_order_value = Decimal(rule.get("minFunds", "0.1"))
                    min_base_amount_increment = Decimal(rule.get("baseIncrement", "0.00000001"))
                    min_price_increment = Decimal(rule.get("priceIncrement", "0.1"))

                    # 构造 TradingRule 对象
                    result.append(TradingRule(
                        trading_pair=trading_pair,
                        min_order_size=min_order_size,
                        min_order_value=min_order_value,
                        min_base_amount_increment=min_base_amount_increment,
                        min_price_increment=min_price_increment
                    ))
                except Exception:
                    self.logger().exception(f"Error parsing the trading pair rule {rule}. Skipping.")
        except KeyError:
            self.logger().warning("Symbols details do not contain valid trading rules.")

        # 如果没有获取到任何规则，添加日志提示
        if not result:
            self.logger().warning("No trading rules available from the exchange. Returning empty rules list.")
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
                    account_balances_temp[asset_name] += Decimal(balance)
                except:
                    account_balances_temp[asset_name] = Decimal(balance)

            elif account_type == "FROZEN":
                try:
                    account_balances_temp[asset_name] += Decimal(balance)
                except:
                    account_balances_temp[asset_name] = Decimal(balance)

            # try:
            #     self._account_balances[asset_name] += Decimal(balance)
            # except:
            #     self._account_balances[asset_name] = Decimal(balance)
            self._account_balances[asset_name] = account_balances_temp[asset_name]
            remote_asset_names.add(asset_name)

        asset_names_to_remove = local_asset_names.difference(remote_asset_names)
        for asset_name in asset_names_to_remove:
            del self._account_available_balances[asset_name]
            del self._account_balances[asset_name]

    # ========== coinstore 获取订单信息 ===========
    async def _request_order_update(self, order: InFlightOrder) -> Dict[str, Any]:
        params_url = f"?ordId={order.exchange_order_id}"
        return await self._api_get(
            path_url=CONSTANTS.GET_ORDER_DETAIL_PATH_URL + params_url,
            params=params_url[1:],
            is_auth_required=True)

    # ======== coinstore 获取用户最新成交 =========
    async def _request_order_fills(self, order: InFlightOrder) -> Dict[str, Any]:
        api_params = {
            "symbol": await self.exchange_symbol_associated_to_pair(order.trading_pair),
            # "side": 1 if order_side == TradeType.BUY else -1,
            # "pageNum": 1,
            # "pageSize": 20,
        }

        url = '?'
        for key, value in api_params.items():
            url = url + str(key) + '=' + str(value) + '&'
        params_url = url[0:-1]

        return await self._api_get(
            path_url=CONSTANTS.GET_TRADE_DETAIL_PATH_URL + params_url,
            params=params_url[1:],
            is_auth_required=True)

        # return await self._api_request(
        #     method=RESTMethod.GET,
        #     path_url=CONSTANTS.GET_TRADE_DETAIL_PATH_URL,
        #     params={
        #         "symbol": await self.exchange_symbol_associated_to_pair(order.trading_pair),
        #         "order_id": await order.get_exchange_order_id()},
        #     is_auth_required=True)

    # ===== coinstore ??? =====
    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []

        try:
            if order.exchange_order_id is not None:
                # 获取用户最新成交
                all_fills_response = await self._request_order_fills(order=order)
                # 訂單更新
                updates = self._create_order_fill_updates(order=order, fill_update=all_fills_response)
                trade_updates.extend(updates)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            is_error_caused_by_unexistent_order = '"code":50005' in str(ex)
            if not is_error_caused_by_unexistent_order:
                raise

        return trade_updates

    # === coinstore ???? ===
    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        updated_order_data = await self._request_order_update(order=tracked_order)  # 获取订单信息

        order_update = self._create_order_update(order=tracked_order, order_update=updated_order_data)
        return order_update

    # === coinstore 訂單更新===
    async def _create_order_fill_updates(self, order: InFlightOrder, fill_update: Dict[str, Any]) -> List[TradeUpdate]:
        # 获取当前订单:baseCurrency, quoteCurrency
        order_url = "/api/v2/trade/order/active"
        try:
            orders_info = await self._api_get(path_url=order_url, params='', is_auth_required=True)
        except Exception as e:
            # 處理錯誤
            print(f"Error fetching orders info: {e}")
            return []  # 或者根據情況返回空列表或做其他處理

        currency_mapping = {}
        matches_url = '/trade/match/accountMatches'
        for order_info in orders_info.get('data', []):
            base_currency = order_info.get('baseCurrency')
            quote_currency = order_info.get('quoteCurrency')
            symbol = base_currency + quote_currency

            # 获取用户最新成交:其中包含baseCurrencyId, quoteCurrencyId
            try:
                account_matches = await self._api_get(
                    path_url=matches_url + f'?symbol={symbol}',
                    params=f'symbol={symbol}',
                    is_auth_required=True
                )
            except Exception as e:
                print(f"Error fetching account matches: {e}")
                continue
            if account_matches.get('data'):
                base_currency_id = account_matches['data'][0].get('baseCurrencyId')
                quote_currency_id = account_matches['data'][0].get('quoteCurrencyId')
            else:
                # 處理沒有成交數據的情況，或者給予一個默認值
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

            fee = TradeFeeBase.new_spot_fee(
                fee_schema=self.trade_fee_schema(),
                trade_type=order.trade_type,
                percent_token=fee_currency,
                flat_fees=[TokenAmount(amount=Decimal(fill_data["fee"]), token=fee_currency)]
            )

            # 确保 execQty 不为零
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

        return updates

    # === coinstore ??? ===
    def _create_order_update(self, order: InFlightOrder, order_update: Dict[str, Any]) -> OrderUpdate:
        # order_update 订单信息 /api/v2/trade/order/orderInfo
        order_data = order_update["data"]
        new_state = CONSTANTS.ORDER_STATE[order_data["status"]]
        update = OrderUpdate(
            client_order_id=order.client_order_id,
            exchange_order_id=str(order_data["ordId"]),
            trading_pair=order.trading_pair,
            update_timestamp=self.current_timestamp,
            new_state=new_state,
        )
        return update

    """    """
    async def _user_stream_event_listener(self):
        async for event_message in self._iter_user_event_queue():
            try:
                event_type = event_message.get("table")
                execution_data = event_message.get("data", [])

                # Refer to https://developer-pro.coinstore.com/en/spot/#private-order-progress
                if event_type == CONSTANTS.PRIVATE_ORDER_PROGRESS_CHANNEL_NAME:
                    for each_event in execution_data:
                        try:
                            client_order_id: Optional[str] = each_event.get("client_order_id")
                            fillable_order = self._order_tracker.all_fillable_orders.get(client_order_id)
                            updatable_order = self._order_tracker.all_updatable_orders.get(client_order_id)

                            new_state = CONSTANTS.ORDER_STATE[each_event["state"]]
                            event_timestamp = int(each_event["ms_t"]) # * 1e-3

                            if fillable_order is not None:
                                is_fill_candidate_by_state = new_state in [OrderState.PARTIALLY_FILLED,
                                                                           OrderState.FILLED]
                                is_fill_candidate_by_amount = fillable_order.executed_amount_base < Decimal(
                                    each_event["filled_size"])
                                if is_fill_candidate_by_state and is_fill_candidate_by_amount:
                                    try:
                                        trade_fills: Dict[str, Any] = await self._request_order_fills(fillable_order)
                                        trade_updates = self._create_order_fill_updates(
                                            order=fillable_order,
                                            fill_update=trade_fills)
                                        for trade_update in trade_updates:
                                            self._order_tracker.process_trade_update(trade_update)
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception:
                                        self.logger().exception("Unexpected error requesting order fills for "
                                                                f"{fillable_order.client_order_id}")
                            if updatable_order is not None:
                                order_update = OrderUpdate(
                                    trading_pair=updatable_order.trading_pair,
                                    update_timestamp=event_timestamp,
                                    new_state=new_state,
                                    client_order_id=client_order_id,
                                    exchange_order_id=each_event["order_id"],
                                )
                                self._order_tracker.process_order_update(order_update=order_update)

                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self.logger().exception("Unexpected error in user stream listener loop.")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception("Unexpected error in user stream listener loop.")

    # === ??? ===
    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        mapping = bidict()
        for symbol_data in filter(coinstore_utils.is_exchange_information_valid, exchange_info["data"]):
            mapping[symbol_data["symbolCode"]] = combine_to_hb_trading_pair(base=symbol_data["tradeCurrencyCode"].upper(),
                                                                            quote=symbol_data["quoteCurrencyCode"].upper())
        self._set_trading_pair_symbol_map(mapping)

    # def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
    #     mapping = bidict()
    #     for symbol_data in filter(coinstore_utils.is_exchange_information_valid, exchange_info["data"]["symbols"]):
    #         mapping[symbol_data["symbol"]] = combine_to_hb_trading_pair(base=symbol_data["base_currency"],
    #                                                                     quote=symbol_data["quote_currency"])
    #     self._set_trading_pair_symbol_map(mapping)

    # === coinstore 最新成交價 ===
    async def _get_last_traded_price(self, trading_pair: str) -> float:
        self.logger().error(f"Error initializing trading pairs: {str(e)}")
        params = {
            "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        }

        resp_json = await self._api_get(
            path_url=CONSTANTS.GET_LAST_TRADING_PRICES_PATH_URL,
            params=params
        )

        return float(resp_json["data"][0]["close"])
