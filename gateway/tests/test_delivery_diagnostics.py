"""The built-in transport diagnostic reaches notification storage; plugin errors stay opaque."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from corki_gateway.connector import Connector
from corki_gateway.errors import DeliveryError


def test_connector_delivers_structured_builtin_errors_only():
    async def scenario():
        for channel in ("dingtalk", "custom"):
            connector = Connector(None, {"id": channel})
            stream = asyncio.StreamReader()
            connector.process = SimpleNamespace(stdout=stream)
            future = asyncio.get_running_loop().create_future()
            connector.pending["fixture"] = future
            stream.feed_data(
                (
                    json.dumps(
                        {
                            "id": "fixture",
                            "error": "failed",
                            "diagnostic": {
                                "provider": "dingtalk",
                                "message": "回复凭据过期",
                                "code": "ReplySessionExpired",
                                "path": "/robot/sendBySession",
                                "status": 400,
                            },
                        }
                    )
                    + "\n"
                ).encode()
            )
            stream.feed_eof()
            await connector.read()
            with pytest.raises(ValueError) as caught:
                await future
            if channel == "dingtalk":
                assert isinstance(caught.value, DeliveryError)
                assert "回复凭据过期" in str(caught.value)
                assert "HTTP 状态码：400" in str(caught.value)
            else:
                assert not isinstance(caught.value, DeliveryError)
                assert "回复凭据过期" not in str(caught.value)

    asyncio.run(scenario())
