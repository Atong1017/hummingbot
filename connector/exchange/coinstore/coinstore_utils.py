import zlib
from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True

EXAMPLE_PAIR = "ETH-USDT"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.0025"),
    taker_percent_fee_decimal=Decimal("0.0025"),
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    exinfo = exchange_info.get("openTrade", False)

    return exinfo


# Decompress WebSocket messages
def decompress_ws_message(message):
    if type(message) == bytes:
        decompress = zlib.decompressobj(-zlib.MAX_WBITS)
        inflated = decompress.decompress(message)
        inflated += decompress.flush()
        return inflated.decode('UTF-8')
    else:
        return message


def compress_ws_message(message):
    if type(message) == str:
        message = message.encode()
        compress = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        deflated = compress.compress(message)
        deflated += compress.flush()
        return deflated
    else:
        return message


class CoinstoreConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="coinstore", client_data=None)

    coinstore_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Coinstore API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    coinstore_secret_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Coinstore secret key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "coinstore"


KEYS = CoinstoreConfigMap.construct()