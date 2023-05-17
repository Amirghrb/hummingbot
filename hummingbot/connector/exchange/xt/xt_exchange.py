import asyncio
from decimal import Decimal
from math import pow
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from time import time
from bidict import bidict

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.xt import xt_constants as CONSTANTS, xt_utils, xt_web_utils as web_utils
from hummingbot.connector.exchange.xt.xt_api_order_book_data_source import XtAPIOrderBookDataSource
from hummingbot.connector.exchange.xt.xt_api_user_stream_data_source import XtAPIUserStreamDataSource
from hummingbot.connector.exchange.xt.xt_auth import XtAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import TradeFillOrderDetails, combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import DeductedFromReturnsTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.event.events import MarketEvent, OrderFilledEvent
from hummingbot.core.utils.async_utils import safe_gather
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class XtExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils
   
    def __init__(self,
                 client_config_map: "ClientConfigAdapter",
                 xt_api_key: str,
                 xt_api_secret: str,
                 trading_pairs: Optional[List[str]] = None,
                 trading_required: bool = True,
                 domain: str = CONSTANTS.DEFAULT_DOMAIN,
                 ):
        self.api_key = xt_api_key
        self.secret_key = xt_api_secret
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        self._last_trades_poll_xt_timestamp = 1.0
        super().__init__(client_config_map)

    @staticmethod
    def xt_order_type(order_type: OrderType) -> str:
        return order_type.name.upper()

    @staticmethod
    def to_hb_order_type(xt_type: str) -> OrderType:
        return OrderType[xt_type]

    @property
    def authenticator(self):
        return XtAuth(
            api_key=self.api_key,
            secret_key=self.secret_key,
            time_provider=self._time_synchronizer)

    @property
    def name(self) -> str:
        if self._domain == "com":
            return "xt"
        else:
            return f"xt_{self._domain}"

    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self):
        return self._domain

    @property
    def client_order_id_max_length(self):
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self):
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def trading_pairs_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def check_network_request_path(self):
        return CONSTANTS.SERVER_TIME_PATH_URL

    @property
    def trading_pairs(self):
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def supported_order_types(self):
        self.logger().info("in xt supported_order_types")
        return [OrderType.LIMIT]

    async def get_all_pairs_prices(self) -> List[Dict[str, str]]:
        self.logger().info("in xt get_all_pairs_prices")
        pairs_prices = await self._api_get(path_url=CONSTANTS.TICKER_BOOK_PATH_URL)
        return pairs_prices

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        error_description = str(request_exception)
        self.logger().info("in xt _is_request_exception_related_to_time_synchronizer ")
        is_time_synchronizer_related = ("-1021" in error_description
                                        and "Timestamp for this request" in error_description)
        return is_time_synchronizer_related

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        return str(CONSTANTS.ORDER_NOT_EXIST_ERROR_CODE) in str(
            status_update_exception
        ) and CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(status_update_exception)

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        return str(CONSTANTS.UNKNOWN_ORDER_ERROR_CODE) in str(
            cancelation_exception
        ) and CONSTANTS.UNKNOWN_ORDER_MESSAGE in str(cancelation_exception)

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        self.logger().info("in xt _create_web_assistants_factory")
        return web_utils.build_api_factory(
            throttler=self._throttler,
            time_synchronizer=self._time_synchronizer,
            domain=self._domain,
            auth=self._auth)

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        self.logger().info("in xt orderbook")
        return XtAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            domain=self.domain,
            api_factory=self._web_assistants_factory)

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        self.logger().info("in xt  _create_user_stream_data_source")        
        return XtAPIUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self.domain,
        )

    def _get_fee(self,
                 base_currency: str,
                 quote_currency: str,
                 order_type: OrderType,
                 order_side: TradeType,
                 amount: Decimal,
                 price: Decimal = s_decimal_NaN,
                 is_maker: Optional[bool] = None) -> TradeFeeBase:
        # self.logger().info(f"_get_fee")
        is_maker = order_type is OrderType.LIMIT_MAKER
        return DeductedFromReturnsTradeFee(percent=self.estimate_fee_pct(is_maker))

    async def _place_order(self,
                           order_id: str,
                           trading_pair: str,
                           amount: Decimal,
                           trade_type: TradeType,
                           order_type: OrderType,
                           price: Decimal,
                           bizType:str="SPOT",
                           **kwargs) -> Tuple[str, float]:


        self.logger().info(f"_place_order :\n {amount}\n ")

        order_result = None
        amount_str = f"{amount:f}"
        price_str = f"{price:f}"
        self.logger().info(f"_place_order :\n {amount_str}\n ")

        type_str = XtExchange.xt_order_type(order_type)
        side_str = CONSTANTS.SIDE_BUY if trade_type is TradeType.BUY else CONSTANTS.SIDE_SELL
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        api_params = {"symbol": symbol,
                      "side": side_str,
                      "quantity": amount_str,
                      "type": type_str,
                      "ClientOrderId": order_id,
                      "price": price_str,
                      "bizType":bizType
                      }
        if order_type == OrderType.LIMIT:
            api_params["timeInForce"] = CONSTANTS.TIME_IN_FORCE_GTC

        self.logger().info(f"in place order S1 : \n {api_params}")
        try:
            order_result = await self._api_post(
                path_url=CONSTANTS.ORDER_PATH_URL,
                data=api_params,
                is_auth_required=True)
            self.logger().info(f"in place order : \n { order_result}")
            if(order_result.get("rc")==0):
                o_id = str(order_result.get("result")["orderId"])
                transact_time = time()
            else:
                msg=order_result.get("mc")
                self.logger().error(f"API Error : \n {msg}")
                raise Exception(msg)
                
        except IOError as e:
            error_description = str(e)
            is_server_overloaded = ("status is 503" in error_description
                                    and "Unknown error, please check your request or try again later." in error_description)
            if is_server_overloaded:
                o_id = "UNKNOWN"
                transact_time = self._time_synchronizer.time()
            else:
                raise
        return o_id, transact_time

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        api_params = {
            "orderId": tracked_order.exchange_order_id,
        }
        cancel_result = await self._api_delete(
            path_url=CONSTANTS.ORDER_PATH_URL,
            params=api_params,
            is_auth_required=True)
        self.logger().info(f"cancel_result -> {order_id}")
        self.logger().info(f"cancel_result -> {cancel_result}")
        if cancel_result.get("rc") == 0:
            return True
        return False

    async def _format_trading_rules(self, exchange_info_dict: Dict[str, Any]) -> List[TradingRule]:
        """
        Example:
        {
            "symbol": "ETHBTC",
            "baseAssetPrecision": 8,
            "quotePrecision": 8,
            "orderTypes": ["LIMIT", "MARKET"],
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.00000100",
                    "maxPrice": "100000.00000000",
                    "tickSize": "0.00000100"
                }, {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.00100000",
                    "maxQty": "100000.00000000",
                    "stepSize": "0.00100000"
                }, {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": "0.00100000"
                }
            ]
        }



                "filters": [                       
          {
            "filter": "PROTECTION_LIMIT",
            "buyMaxDeviation": "0.8"
            "sellMaxDeviation": "0.8"
          },
          {
            "filter": "PROTECTION_MARKET",
            "maxDeviation": "0.1"
          },
          {
            "filter": "PROTECTION_ONLINE",
            "durationSeconds": "300",
            "maxPriceMultiple": "5"
          },
          {
            "filter": "PRICE",
            "min": null,
            "max": null,
            "tickSize": null
          },
          {
            "filter": "QUANTITY",
            "min": null,
            "max": null,
            "tickSize": null
          },
          {
            "filter": "QUOTE_QTY",
            "min": null
          },
       ]
      }
    ]
        """
        trading_pair_rules = exchange_info_dict.get("result", {})
        trading_pair_rules = trading_pair_rules.get("symbols")
        retval = []
        for rule in filter(xt_utils.is_exchange_information_valid, trading_pair_rules):
                trading_pair = await self.trading_pair_associated_to_exchange_symbol(symbol=rule.get("symbol"))
                pricePrecision =rule.get("pricePrecision")
                quantityPrecision =rule.get("quantityPrecision")
                filters = rule.get("filters")
                min_notional_filter={}
                lot_size_filter={}
                price_filter={}
                # self.logger().info(f"\n\n in xt _format_trading_rules {filters} \n ____\n ")
                for f in filters:
                    if(f.get('filter')=='QUOTE_QTY'):
                        min_notional_filter = f 
                    elif(f.get('filter')=='PRICE'):
                        price_filter = f
                    elif(f.get('filter')=='QUANTITY'):    
                        lot_size_filter = f
                # self.logger().info(f"\n\n in xt _format_trading_rules \n {min_notional_filter} \n ____\n ")
                # self.logger().info(f"\n\n in xt _format_trading_rules \n {price_filter} \n ____\n ")
                # self.logger().info(f"\n\n in xt _format_trading_rules \n {lot_size_filter} \n ____\n ")

                min_order_size = Decimal(lot_size_filter.get("min"))    if lot_size_filter.get("min")!= None else Decimal("0.0000001")
                step_size = Decimal(lot_size_filter.get("tickSize"))    if lot_size_filter.get("tickSize")!= None else  Decimal(f"1e-{quantityPrecision}")
                min_notional = Decimal(min_notional_filter.get("min"))  if min_notional_filter.get("min")!= None else Decimal("0.0000001")
                tick_size = Decimal(price_filter.get("tickSize"))       if  price_filter.get("tickSize")!= None else Decimal(f"1e-{pricePrecision}")

                # self.logger().info(f"\n\n in xt _format_trading_rules {min_order_size}\n {tick_size}\n {step_size} \n {min_notional}\n ___")
                retval.append(
                    TradingRule(trading_pair,
                                min_order_size=min_order_size,
                                min_price_increment=tick_size,
                                min_base_amount_increment=step_size,
                                min_notional_size=min_notional))

        return retval

    async def _status_polling_loop_fetch_updates(self):
        self.logger().info(f"_status_polling_loop_fetch_updates")
        await self._update_order_fills_from_trades()
        await super()._status_polling_loop_fetch_updates()

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange
        """
        pass

    async def _user_stream_event_listener(self):
        """
        This functions runs in background continuously processing the events received from the exchange by the user
        stream data source. It keeps reading events from the queue until the task is interrupted.
        The events received are balance updates, order updates and trade events.
        """

        async for event_message in self._iter_user_event_queue():
            self.logger().info(f"_user_stream_event_listener{event_message}")
            try:
                event_type = event_message.get("e")
                # Refer to https://github.com/xt-exchange/xt-official-api-docs/blob/master/user-data-stream.md
                # As per the order update section in Xt the ID of the order being canceled is under the "C" key
                if event_type == "executionReport":
                    execution_type = event_message.get("x")
                    if execution_type != "CANCELED":
                        client_order_id = event_message.get("c")
                    else:
                        client_order_id = event_message.get("C")

                    if execution_type == "TRADE":
                        tracked_order = self._order_tracker.all_fillable_orders.get(client_order_id)
                        if tracked_order is not None:
                            fee = TradeFeeBase.new_spot_fee(
                                fee_schema=self.trade_fee_schema(),
                                trade_type=tracked_order.trade_type,
                                percent_token=event_message["N"],
                                flat_fees=[TokenAmount(amount=Decimal(event_message["n"]), token=event_message["N"])]
                            )
                            trade_update = TradeUpdate(
                                trade_id=str(event_message["t"]),
                                client_order_id=client_order_id,
                                exchange_order_id=str(event_message["i"]),
                                trading_pair=tracked_order.trading_pair,
                                fee=fee,
                                fill_base_amount=Decimal(event_message["l"]),
                                fill_quote_amount=Decimal(event_message["l"]) * Decimal(event_message["L"]),
                                fill_price=Decimal(event_message["L"]),
                                fill_timestamp=event_message["T"] * 1e-3,
                            )
                            self._order_tracker.process_trade_update(trade_update)

                    tracked_order = self._order_tracker.all_updatable_orders.get(client_order_id)
                    if tracked_order is not None:
                        order_update = OrderUpdate(
                            trading_pair=tracked_order.trading_pair,
                            update_timestamp=event_message["E"] * 1e-3,
                            new_state=CONSTANTS.ORDER_STATE[event_message["X"]],
                            client_order_id=client_order_id,
                            exchange_order_id=str(event_message["i"]),
                        )
                        self._order_tracker.process_order_update(order_update=order_update)

                elif event_type == "outboundAccountPosition":
                    balances = event_message["B"]
                    for balance_entry in balances:
                        asset_name = balance_entry["a"]
                        free_balance = Decimal(balance_entry["f"])
                        total_balance = Decimal(balance_entry["f"]) + Decimal(balance_entry["l"])
                        self._account_available_balances[asset_name] = free_balance
                        self._account_balances[asset_name] = total_balance

            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error("Unexpected error in user stream listener loop.", exc_info=True)
                await self._sleep(5.0)

    async def _update_order_fills_from_trades(self):
        """
        This is intended to be a backup measure to get filled events with trade ID for orders,
        in case Xt's user stream events are not working.
        NOTE: It is not required to copy this functionality in other connectors.
        This is separated from _update_order_status which only updates the order status without producing filled
        events, since Xt's get order endpoint does not return trade IDs.
        The minimum poll interval for order status is 10 seconds.
        """
        small_interval_last_tick = self._last_poll_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
        small_interval_current_tick = self.current_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
        long_interval_last_tick = self._last_poll_timestamp / self.LONG_POLL_INTERVAL
        long_interval_current_tick = self.current_timestamp / self.LONG_POLL_INTERVAL

        if (long_interval_current_tick > long_interval_last_tick
                or (self.in_flight_orders and small_interval_current_tick > small_interval_last_tick)):
            query_time = int((self._last_trades_poll_xt_timestamp-10000)* 1e3)
            self._last_trades_poll_xt_timestamp = self._time_synchronizer.time()
            order_by_exchange_id_map = {}
            for order in self._order_tracker.all_fillable_orders.values():
                order_by_exchange_id_map[order.exchange_order_id] = order

            tasks = []
            trading_pairs = self.trading_pairs
            for trading_pair in trading_pairs:
                self.logger().info(f"at _update_order_fills_from_trades : res \n {trading_pair} , {query_time} \n\n")
                params = {
                    "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                }
                if self._last_poll_timestamp > 0:
                    params["startTime"] = query_time
                    
                tasks.append(self._api_get(
                    path_url=CONSTANTS.MY_TRADES_PATH_URL,
                    params=params,
                    is_auth_required=True))

            results = await safe_gather(*tasks, return_exceptions=True)
            self.logger().info(f"at _update_order_fills_from_trades : res \n {results} \n\n")
            if (results["rc"==0]):
                results = results[0].get("result")
                self.logger().info(f"at _update_order_fills_from_trades : res2 \n {results} \n\n")
                results = results["items"]           
                self.logger().info(f"at _update_order_fills_from_trades : res3 \n {results} \n\n")
            else:
                raise AttributeError(results["mc"])

            for trade,trading_pair in zip(results,trading_pairs):
                    if isinstance(trade, Exception):
                        self.logger().network(
                            f"Error fetching trades update for the order {trading_pair}: {trade}.",
                            app_warning_msg=f"Failed to fetch trade update for {trading_pair}."
                        )
                        continue
                    self.logger().info(f"at _update_order_fills_from_trades : res4 \n {trade} , {trading_pair}\n\n")
                    exchange_order_id = trade["orderId"]
                    if exchange_order_id in order_by_exchange_id_map:
                        # This is a fild for a tracked order
                        tracked_order = order_by_exchange_id_map[exchange_order_id]
                        fee = TradeFeeBase.new_spot_fee(
                            fee_schema=self.trade_fee_schema(),
                            trade_type=tracked_order.trade_type,
                            percent_token=trade["feeCurrency"],
                            flat_fees=[TokenAmount(amount=Decimal(trade["fee"]), token=trade["feeCurrency"])]
                        )
                        trade_update = TradeUpdate(
                            trade_id=str(trade['tradeId']),
                            client_order_id=tracked_order.client_order_id,
                            exchange_order_id=exchange_order_id,
                            trading_pair=trading_pair,
                            fee=fee,
                            fill_base_amount=Decimal(trade["quantity"]),
                            fill_quote_amount=Decimal(trade["quoteQty"]),
                            fill_price=Decimal(trade["price"]),
                            fill_timestamp=trade["time"] * 1e-3,
                        )
                        self._order_tracker.process_trade_update(trade_update)
                    elif self.is_confirmed_new_order_filled_event(str(trade["tradeId"]), exchange_order_id, trading_pair):
                        # This is a fill of an order registered in the DB but not tracked any more
                        self._current_trade_fills.add(TradeFillOrderDetails(
                            market=self.display_name,
                            exchange_trade_id=str(trade["tradeId"]),
                            symbol=trading_pair))
                        self.trigger_event(
                            MarketEvent.OrderFilled,
                            OrderFilledEvent(
                                timestamp=float(trade["time"]) * 1e-3,
                                order_id=self._exchange_order_ids.get(str(trade["orderId"]), None),
                                trading_pair=trading_pair,
                                trade_type=TradeType.BUY if trade["orderSide"]=="BUY" else TradeType.SELL,
                                order_type=OrderType.LIMIT_MAKER if trade['takerMaker']=="MAKER" else OrderType.LIMIT,
                                price=Decimal(trade["price"]),
                                amount=Decimal(trade["quantity"]),
                                trade_fee=DeductedFromReturnsTradeFee(
                                    flat_fees=[
                                        TokenAmount(
                                            trade["feeCurrency"],
                                            Decimal(trade["fee"])
                                        )
                                    ]
                                ),
                                exchange_trade_id=str(trade["tradeId"])
                            ))
                        self.logger().info(f"Recreating missing trade in TradeFill: {trade}")

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []

        if order.exchange_order_id is not None:
            exchange_order_id = int(order.exchange_order_id)
            trading_pair = await self.exchange_symbol_associated_to_pair(trading_pair=order.trading_pair)
            all_fills_response = await self._api_get(
                path_url=CONSTANTS.MY_TRADES_PATH_URL,
                params={
                    "symbol": trading_pair,
                    "orderId": exchange_order_id,
                    "limit":20
                },
                is_auth_required=True,
                )
            if all_fills_response.get("rc")==0:
                self.logger().info(f"trade : { all_fills_response}")
                all_fills_response=all_fills_response.get("result").get("items")
                self.logger().info(f"trade : { all_fills_response}")
                if(len(all_fills_response)>0):
                    for trade in all_fills_response:
                        exchange_order_id = trade.get("orderId",None)
                        fee = TradeFeeBase.new_spot_fee(
                        fee_schema=self.trade_fee_schema(),
                        trade_type=order.trade_type,
                        percent_token=trade["fee"],
                        flat_fees=[TokenAmount(amount=Decimal(trade["fee"]), token=trade["feeCurrency"])]
                        )
                        trade_update = TradeUpdate(
                            trade_id=str(trade["orderId"]),
                            client_order_id=order.client_order_id,
                            exchange_order_id=exchange_order_id,
                            trading_pair=trading_pair,
                            fee=fee,
                            fill_base_amount=Decimal(trade["quantity"]),
                            fill_quote_amount=Decimal(trade["quoteQty"]),
                            fill_price=Decimal(trade["price"]),
                            fill_timestamp=trade["time"] * 1e-3,
                        )
                        trade_updates.append(trade_update)
                    return trade_updates
                else:
                    return []   
            else:
                self.logger().error(f"Network Error {all_fills_response.get('msg')}", exc_info=True)
                raise 


    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        self.logger().info(f"_request_order_status")
        trading_pair = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
       
        updated_order_data = await self._api_get(
            path_url=CONSTANTS.ORDER_PATH_URL,
            params={
                "orderId": tracked_order.exchange_order_id},
            is_auth_required=True
            )
        updated_order_data= updated_order_data.get("result")
        self.logger().info(f"_request_order_status {updated_order_data} ")

        new_state = CONSTANTS.ORDER_STATE[updated_order_data["state"]]

        order_update = OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=updated_order_data["orderId"],
            trading_pair=tracked_order.trading_pair,
            update_timestamp=updated_order_data["updatedTime"] * 1e-3,
            new_state=new_state,
        )

        self.logger().error(f"_request_order_status L3 {order_update} ")
        return order_update

    async def _update_balances(self):
        local_asset_names = set(self._account_balances.keys())
        remote_asset_names = set()

        account_info = await self._api_get(
            path_url=CONSTANTS.ACCOUNTS_PATH_URL,
            is_auth_required=True)

        balances = account_info["result"]["assets"]
        for balance_entry in balances:
            self.logger().info(f'\n\n in xt _update_balances { balance_entry["currency"]} \n { balance_entry["availableAmount"]} \n { balance_entry["totalAmount"]}')        
            asset_name = balance_entry["currency"]
            free_balance = Decimal(balance_entry["availableAmount"])
            total_balance = Decimal(balance_entry["totalAmount"])
            self._account_available_balances[asset_name] = free_balance
            self._account_balances[asset_name] = total_balance
            remote_asset_names.add(asset_name)

        asset_names_to_remove = local_asset_names.difference(remote_asset_names)
        for asset_name in asset_names_to_remove:
            del self._account_available_balances[asset_name]
            del self._account_balances[asset_name]

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        mapping = bidict()
        exchange_info=exchange_info.get("result")
        for symbol_data in filter(xt_utils.is_exchange_information_valid, exchange_info["symbols"]):
            mapping[symbol_data["symbol"]] = combine_to_hb_trading_pair(base=symbol_data["baseCurrency"],
                                                                        quote=symbol_data["quoteCurrency"])
        
        self.logger().info(f"in xt _initialize_trading_pair_symbols_from_exchange_info \n\n  \n\n ________________ ")
        self._set_trading_pair_symbol_map(mapping)

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        params = {
            "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        }

        resp_json = await self._api_request(
            method=RESTMethod.GET,
            path_url=CONSTANTS.TICKER_PRICE_CHANGE_PATH_URL,
            params=params
        )
        resp_json= resp_json.get("result")[0]
        self.logger().info(f'in xt _get_last_traded_price {float(resp_json["p"])}')
        return float(resp_json["p"])
