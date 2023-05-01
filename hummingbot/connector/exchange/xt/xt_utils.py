from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "eth_usdt"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.002"),
    taker_percent_fee_decimal=Decimal("0.002"),
    buy_percent_fee_deducted_from_returns=True
)


def is_exchange_information_valid(symbol_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    state=symbol_info.get("state", None)
    trading=symbol_info.get("tradingEnabled", None)
    openApi=symbol_info.get("openapiEnabled", None)
    return  state == "ONLINE" and trading == True 


class XtConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="xt", const=True, client_data=None)
    xt_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your XT API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    xt_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your XT API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "xt"

KEYS = XtConfigMap.construct()



### if you need to add another domain appkey and secret use this 

    # OTHER_DOMAINS = ["xt_us"]
    # OTHER_DOMAINS_PARAMETER = {"xt_us": "us"}
    # OTHER_DOMAINS_EXAMPLE_PAIR = {"xt_us": "BTC-USDT"}
    # OTHER_DOMAINS_DEFAULT_FEES = {"xt_us": DEFAULT_FEES}


    # class XtUSConfigMap(BaseConnectorConfigMap):
    #     connector: str = Field(default="xt_us", const=True, client_data=None)
    #     xt_api_key: SecretStr = Field(
    #         default=...,
    #         client_data=ClientFieldData(
    #             prompt=lambda cm: "Enter your XT US API key",
    #             is_secure=True,
    #             is_connect_key=True,
    #             prompt_on_new=True,
    #         )
    #     )
    #     xt_api_secret: SecretStr = Field(
    #         default=...,
    #         client_data=ClientFieldData(
    #             prompt=lambda cm: "Enter your XT US API secret",
    #             is_secure=True,
    #             is_connect_key=True,
    #             prompt_on_new=True,
    #         )
    #     )

    #     class Config:
    #         title = "xt_us"


    # OTHER_DOMAINS_KEYS = {"xt_us": XtUSConfigMap.construct()}
