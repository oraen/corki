"""Extension comparison metadata stays local on both ordinary HTTP transports."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.context.extension_world_state import WorldStateFragment, WorldStateSection
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextRole


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_extension_roles_and_silent_snapshots_on_ordinary_wire(tmp_path, monkeypatch, api_mode):
    async def scenario():
        requests = []

        class Source:
            async def world_state_contributions(self, **kwargs):
                index = len(requests)

                def render(previous):
                    if index >= 2:
                        return None
                    return WorldStateFragment(
                        ContextRole.DEVELOPER if index == 0 else ContextRole.USER,
                        "UPPER_FIXTURE" if index == 0 else "LOWER_FIXTURE",
                    )

                return (WorldStateSection("wire", json.dumps({"PRIVATE_COMPARE": index}), render),)

        def respond(request):
            assert request.url.host == "fixture.invalid"
            requests.append(json.loads(request.content))
            if api_mode == "responses":
                assert request.url.path == "/v1/responses"
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{len(requests)}",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                    },
                }
                text = f"data: {json.dumps(packet)}\n\n"
            else:
                assert request.url.path == "/v1/chat/completions"
                packet = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
                text = f"data: {json.dumps(packet)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text)

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *args, **kwargs: client_type(
                *args, **kwargs, transport=httpx.MockTransport(respond)
            ),
        )

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    memories_enabled=False,
                    api_mode=api_mode,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                context_contributors=(Source(),),
                thread_id=thread,
            )

        runtime = await create()
        try:
            for _ in range(3):
                assert isinstance(
                    [event async for event in runtime.stream("go")][-1], TurnCompleted
                )
            thread = runtime.thread_id
            stored = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        runtime = await create(thread)
        try:
            assert isinstance([event async for event in runtime.stream("cold")][-1], TurnCompleted)
            after = await runtime._repository.load_items(thread)
            assert after[: len(stored)] == stored
        finally:
            await runtime.aclose()

        assert len(requests) == 4
        for index, body in enumerate(requests):
            wire = json.dumps(body)
            assert "PRIVATE_COMPARE" not in wire
            assert "snapshot_state" not in wire
            assert "extension.world_state.wire" not in wire
            messages = body["input" if api_mode == "responses" else "messages"]
            upper = [message for message in messages if "UPPER_FIXTURE" in json.dumps(message)]
            lower = [message for message in messages if "LOWER_FIXTURE" in json.dumps(message)]
            assert len(upper) == 1
            assert upper[0]["role"] == ("developer" if api_mode == "responses" else "system")
            assert [message["role"] for message in lower] == ([] if index == 0 else ["user"])

    asyncio.run(scenario())
