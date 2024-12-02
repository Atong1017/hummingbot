from typing import Callable, Optional
from urllib.parse import urljoin

import hummingbot.connector.exchange.coinstore.coinstore_constants as CONSTANTS
from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.connector.utils import TimeSynchronizerRESTPreProcessor
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
import time

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
    log_entry = f"{current_time} - coinstore_web_utils - {text}"

    with open(LOG_FILE_PATH, "a") as test_file:
        test_file.write(log_entry + '\n')


def public_rest_url(path_url: str, **kwargs) -> str:
    """
    Creates a full URL for provided REST endpoint

    :param path_url: a public REST endpoint

    :return: the full URL to the endpoint
    """
    return urljoin(CONSTANTS.REST_URL, path_url)


def private_rest_url(path_url: str, **kwargs) -> str:
    return public_rest_url(path_url)


def build_api_factory(
        throttler: Optional[AsyncThrottler] = None,
        time_synchronizer: Optional[TimeSynchronizer] = None,
        time_provider: Optional[Callable] = None,
        auth: Optional[AuthBase] = None, ) -> WebAssistantsFactory:
    try:
        throttler = throttler or create_throttler()
        time_synchronizer = time_synchronizer or TimeSynchronizer()
        time_provider = time_provider or (lambda: get_current_server_time(throttler=throttler))
        api_factory = WebAssistantsFactory(
            throttler=throttler,
            auth=auth,
            rest_pre_processors=[
                TimeSynchronizerRESTPreProcessor(synchronizer=time_synchronizer, time_provider=time_provider),
            ])
        return api_factory
    except Exception as e:
        write_logs(f"build_api_factory : Error => {e}")


def build_api_factory_without_time_synchronizer_pre_processor(throttler: AsyncThrottler) -> WebAssistantsFactory:
    api_factory = WebAssistantsFactory(throttler=throttler)
    return api_factory


def create_throttler() -> AsyncThrottler:
    return AsyncThrottler(CONSTANTS.RATE_LIMITS)


async def get_current_server_time(
        throttler: Optional[AsyncThrottler] = None,
        domain: str = CONSTANTS.DEFAULT_DOMAIN) -> float:

    return int(time.time() * 1000)

