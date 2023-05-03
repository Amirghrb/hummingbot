import asyncio
import time
from typing import TYPE_CHECKING, List, Optional

from hummingbot.connector.exchange.xt import xt_constants as CONSTANTS, xt_web_utils as web_utils
from hummingbot.connector.exchange.xt.xt_auth import XtAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.xt.xt_exchange import XtExchange


class XtAPIUserStreamDataSource(UserStreamTrackerDataSource):

    LISTEN_KEY_KEEP_ALIVE_INTERVAL = 1800  # Recommended to Ping/Update listen key to keep connection alive
    HEARTBEAT_TIME_INTERVAL = 30.0

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 auth: XtAuth,
                 trading_pairs: List[str],
                 connector: 'XtExchange',
                 api_factory: WebAssistantsFactory,
                 domain: str = CONSTANTS.DEFAULT_DOMAIN):
        super().__init__()
        self._auth: XtAuth = auth
        self._current_listen_key = None
        self._domain = domain
        self._api_factory = api_factory

        self._listen_key_initialized_event: asyncio.Event = asyncio.Event()
        self._last_listen_key_ping_ts = 0


    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Creates an instance of WSAssistant connected to the exchange
        """
        self._manage_listen_key_task = safe_ensure_future(self._manage_listen_key_task_loop())
        await self._listen_key_initialized_event.wait()

        ws: WSAssistant = await self._get_ws_assistant()
        url = f"{CONSTANTS.WSS_URL_PRIVATE.format(self._domain)}"
        await ws.connect(ws_url=url, ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
        return ws

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        """
        Subscribes to the trade events and diff orders events through the provided websocket connection.

        Xt does not require any channel subscription.

        :param websocket_assistant: the websocket assistant used to connect to the exchange
        """
        pass

    async def _get_listen_key(self):
        request=RESTRequest(
            url=CONSTANTS.REST_URL.format(CONSTANTS.DEFAULT_DOMAIN)+CONSTANTS.PRIVATE_API_VERSION+CONSTANTS.WS_TOCKEN,
            method=RESTMethod.POST,
            is_auth_required= True,
            endpoint_url="/"+CONSTANTS.PRIVATE_API_VERSION+CONSTANTS.WS_TOCKEN
                            )
        rest_assistant = await self._api_factory.get_rest_assistant()
        try:
            data = await rest_assistant.execute_request(
                url=web_utils.public_rest_url(path_url=CONSTANTS.WS_TOCKEN, domain=self._domain),
                method=RESTMethod.POST,
                throttler_limit_id=CONSTANTS.WS_TOCKEN,
                headers=self._auth.header_for_authentication(request)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            raise IOError(f"Error fetching user stream listen key. Error: {exception}")
        data=data.get('result')
        # self.logger().info(data["accessToken"])
        return data["accessToken"]
# xt dosent support put and update key so we gnerate a new one in a interval -> L127
    async def _ping_listen_key(self) -> bool:
        request=RESTRequest(
            url=CONSTANTS.REST_URL.format(CONSTANTS.DEFAULT_DOMAIN)+CONSTANTS.PRIVATE_API_VERSION+CONSTANTS.WS_TOCKEN,
            method=RESTMethod.POST,
            is_auth_required= True,
            endpoint_url="/"+CONSTANTS.PRIVATE_API_VERSION+CONSTANTS.WS_TOCKEN
                            )
        rest_assistant = await self._api_factory.get_rest_assistant()
        try:
            data = await rest_assistant.execute_request(
                url=web_utils.public_rest_url(path_url=CONSTANTS.XT_USER_STREAM_PATH_URL, domain=self._domain),
                params={"listenKey": self._current_listen_key},
                method=RESTMethod.PUT,
                return_err=True,
                throttler_limit_id=CONSTANTS.XT_USER_STREAM_PATH_URL,
                headers=self._auth.header_for_authentication(request)
            )

            if "code" in data:
                self.logger().warning(f"Failed to refresh the listen key {self._current_listen_key}: {data}")
                return False

        except asyncio.CancelledError:
            raise
        except Exception as exception:
            self.logger().warning(f"Failed to refresh the listen key {self._current_listen_key}: {exception}")
            return False

        return True

    async def _manage_listen_key_task_loop(self):
        try:
            while True:
                now = int(time.time())
                if self._current_listen_key is None:
                    self._current_listen_key = await self._get_listen_key()
                    self.logger().info(f"Successfully obtained listen key {self._current_listen_key}")
                    self._listen_key_initialized_event.set()
                    self._last_listen_key_ping_ts = int(time.time())

                if now - self._last_listen_key_ping_ts >= self.LISTEN_KEY_KEEP_ALIVE_INTERVAL:
                    success: bool =False
                    if not success:
                        self.logger().error("Error occurred renewing listen key ...")
                        break
                    else:
                        self.logger().info(f"Refreshed listen key {self._current_listen_key}.")
                        self._last_listen_key_ping_ts = int(time.time())
                else:
                    await self._sleep(self.LISTEN_KEY_KEEP_ALIVE_INTERVAL)
        finally:
            self._current_listen_key = None
            self._listen_key_initialized_event.clear()

    async def _get_ws_assistant(self) -> WSAssistant:
        if self._ws_assistant is None:
            self._ws_assistant = await self._api_factory.get_ws_assistant()
        return self._ws_assistant

    async def _on_user_stream_interruption(self, websocket_assistant: Optional[WSAssistant]):
        await super()._on_user_stream_interruption(websocket_assistant=websocket_assistant)
        self._manage_listen_key_task and self._manage_listen_key_task.cancel()
        self._current_listen_key = None
        self._listen_key_initialized_event.clear()
        await self._sleep(5)
