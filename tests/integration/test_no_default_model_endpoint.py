import asyncio

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnFailed
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_key_without_endpoint_does_not_connect_anywhere(tmp_path, monkeypatch, mode):
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(500)

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                api_key="fixture",
                api_mode=mode,
                skills_enabled=False,
                model_max_retries=0,
                model_request_max_retries=0,
            ),
            database_path=tmp_path / "session.db",
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnFailed)
            assert "base URL" in events[-1].error
            assert not calls
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
