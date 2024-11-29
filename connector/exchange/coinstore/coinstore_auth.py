import hashlib
import hmac
import json
import math
from typing import Any, Dict, List, Optional
import time

from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTRequest, WSRequest


class CoinstoreAuth(AuthBase):
    """
    Auth class required by BitMart API
    Learn more at https://developer-pro.bitmart.com/en/part2/auth.html
    """

    def __init__(self, api_key: str, secret_key: str, time_provider: TimeSynchronizer):
        self.api_key = api_key
        self.secret_key = secret_key
        self.time_provider: TimeSynchronizer = time_provider

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:

        """
        Adds the server time and the signature to the request, required for authenticated interactions. It also adds
        the required parameter in the request header.

        :param request: the request to be configured for authenticated interaction

        :return: The RESTRequest with auth information included
        """
        headers = {}
        if request.headers is not None:
            headers.update(request.headers)
        headers.update(self.authentication_headers(request=request))
        request.headers = headers

        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:

        """
        This method is intended to configure a websocket request to be authenticated. OKX does not use this
        functionality
        """

        return request  # pass-through

    def _generate_signature(self, timestamp: str, body: Optional[str] = None) -> str:
        expires_key = str(math.floor(int(timestamp) / 30000))
        expires_key = expires_key.encode("utf-8")

        key = hmac.new(self.secret_key.encode('utf-8'), expires_key, hashlib.sha256).hexdigest()
        key = key.encode("utf-8")

        payload = json.dumps(body.params) if body.params is not None else json.dumps(json.loads(body.data))

        payload = payload.encode("utf-8")

        signature = hmac.new(key, payload, hashlib.sha256).hexdigest()

        return signature

    def authentication_headers(self, request: RESTRequest) -> Dict[str, Any]:
        timestamp = str(int(self.time_provider.time() * 1e3))

        sign = self._generate_signature(timestamp=timestamp, body=request)

        header = {
            "X-CS-APIKEY": self.api_key,
            "X-CS-SIGN": sign,
            "X-CS-EXPIRES": timestamp,
            'exch-language': 'en_US',
            'Content-Type': 'application/json',
            'Accept': '*/*',
            'Connection': 'keep-alive'
        }
        return header

    def websocket_login_parameters(self) -> List[str]:
        timestamp = str(int(self.time_provider.time() * 1e3))

        return [
            self.api_key,
            timestamp,
            self._generate_signature(
                timestamp=timestamp,
                body="")
        ]
