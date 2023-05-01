import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.xt import xt_constants as CONSTANTS, xt_web_utils as web_utils
from hummingbot.connector.exchange.xt.xt_order_book import XtOrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.xt.xt_exchange import XtExchange


class XtAPIOrderBookDataSource(OrderBookTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0
    TRADE_STREAM_ID = 1
    DIFF_STREAM_ID = 2
    ONE_HOUR = 60 * 60

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 trading_pairs: List[str],
                 connector: 'XtExchange',
                 api_factory: WebAssistantsFactory,
                 domain: str = CONSTANTS.DEFAULT_DOMAIN):
        super().__init__(trading_pairs)
        self._connector = connector
        self._trade_messages_queue_key = CONSTANTS.TRADE_EVENT_TYPE
        self._diff_messages_queue_key = CONSTANTS.DIFF_EVENT_TYPE
        self._domain = domain
        self._api_factory = api_factory

    async def get_last_traded_prices(self,
                                     trading_pairs: List[str],
                                     domain: Optional[str] = None) -> Dict[str, float]:
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Retrieves a copy of the full order book from the exchange, for a particular trading pair.

        :param trading_pair: the trading pair for which the order book will be retrieved

        :return: the response from the exchange (JSON dictionary)
        """
        tp=await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        #self.logger().info("in snap shot ----------------\n\n")
        #self.logger().info(f" --------{trading_pair}--------\n\n")
        #self.logger().info(f" --------{tp}--------\n\n")
        #self.logger().info("in snap shot ----------------\n\n")
        params = {
            "symbol":tp,
            "limit": "450"
        }

        rest_assistant = await self._api_factory.get_rest_assistant()
        data = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(path_url=CONSTANTS.SNAPSHOT_PATH_URL, domain=self._domain),
            params=params,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.SNAPSHOT_PATH_URL,
        )
        data=data.get("result")
        #self.logger().info("data :\n {data}")
        return data

    async def _subscribe_channels(self, ws: WSAssistant):
        """
        Subscribes to the trade events and diff orders events through the provided websocket connection.
        :param ws: the websocket assistant used to connect to the exchange
        """
        try:
            trade_params = []
            depth_params = []
            for trading_pair in self._trading_pairs:
                symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                trade_params.append(f"trade@{symbol.lower()}")
                depth_params.append(f"depth_update@{symbol.lower()}")
            payload = {
                "method": "SUBSCRIBE",
                "params": trade_params,
                "id": 1
            }
            subscribe_trade_request: WSJSONRequest = WSJSONRequest(payload=payload)

            payload = {
                "method": "SUBSCRIBE",
                "params": depth_params,
                "id": 2
            }
            subscribe_orderbook_request: WSJSONRequest = WSJSONRequest(payload=payload)

            await ws.send(subscribe_trade_request)
            await ws.send(subscribe_orderbook_request)

            #self.logger().info("Subscribed to public order book and trade channels...")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error(
                "Unexpected error occurred subscribing to order book trading and delta streams...",
                exc_info=True
            )
            raise

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_URL_PUBLIC.format(self._domain),
                         ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
        return ws

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:
        snapshot: Dict[str, Any] = await self._request_order_book_snapshot(trading_pair)
        #self.logger().info(f"_order_book_snapshot snapShot S : {snapshot}")
        snapshot_timestamp: float = time.time()
        snapshot_msg: OrderBookMessage = XtOrderBook.snapshot_message_from_exchange(
            snapshot,
            snapshot_timestamp,
            metadata={"trading_pair": trading_pair}
        )
        #self.logger().info(f"_order_book_snapshot_message end: \n {snapshot_msg}") 
        return snapshot_msg

    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        #self.logger().info(f"_parse_trade_message s: \n {raw_message}") 
        if(raw_message["topic"]=="trade"):
            data= raw_message.get("data")
            if "result" not in raw_message:
                trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=data.get("s"))
                trade_message = XtOrderBook.trade_message_from_exchange(
                    data , {"trading_pair": trading_pair})
                message_queue.put_nowait(trade_message)
            #self.logger().info(f"_parse_trade_message e: \n {trade_message}") 

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):
        data= raw_message.get("data")
        ## self.logger().info(f"_parse_order_book_message******: \n {data}") 
        if "result" not in raw_message:
            trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=data.get("s"))
            order_book_message: OrderBookMessage = XtOrderBook.diff_message_from_exchange(
                data, time.time(), {"trading_pair": trading_pair})
            message_queue.put_nowait(order_book_message)
        ## self.logger().info(f"_parse_order_book_diff_message******: \n {order_book_message}") 

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        channel = ""
        #self.logger().info(f"_originating_message S: \n {event_message} \n") 
        if "result" not in event_message:
            event_type = event_message.get("id")
            channel = (self._diff_messages_queue_key if event_type == "2"
                    else self._trade_messages_queue_key)
        #self.logger().info(f"_originating_message E: \n {channel}") 
        return channel
