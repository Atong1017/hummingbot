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
    write_logs(f"decompress_ws_message : message = > {message}")
    if type(message) == bytes:
        decompress = zlib.decompressobj(-zlib.MAX_WBITS)
        write_logs(f"decompress_ws_message : write_logs = > {write_logs}")
        inflated = decompress.decompress(message)
        write_logs(f"decompress_ws_message : inflated = > {inflated}")
        inflated += decompress.flush()
        write_logs(f"decompress_ws_message : inflated = > {inflated}")
        return inflated.decode('UTF-8')
    else:
        return message


def compress_ws_message(message):
    write_logs(f"decompress_ws_message : message = > {message}")
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

    write_logs(f"Enter your Coinstore API key = > {connector}")

    coinstore_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Coinstore API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    write_logs(f"Enter your Coinstore secret key = > {connector}")
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