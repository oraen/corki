"""Host policy reaches ordinary transports as independent, ordered messages."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_policy_wire_cold_replace_and_missing_policy_removal(tmp_path, monkeypatch, api_mode):
    async def scenario():
        requests = []

        def respond(request):
            assert request.url.host == "fixture.invalid"
            assert request.url.path == (
                "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
            )
            body = json.loads(request.content)
            requests.append(body)
            if api_mode == "responses":
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
                packet = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
                text = f"data: {json.dumps(packet)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text)

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        thread = None
        prefix = ()
        for index, text in enumerate(("POLICY ONE", "POLICY TWO", None)):
            policy = compose_mcp_requirements(
                ()
                if text is None
                else (
                    MCPRequirementsLayer(
                        "host", "additional_developer_instructions = " + json.dumps(text)
                    ),
                )
            )
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    api_mode=api_mode,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    base_instructions="FIXED BASE",
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                registry=ToolRegistry(),
                thread_id=thread,
                mcp_requirements=policy,
            )
            try:
                assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(prefix)] == prefix
                prefix, thread = stored, runtime.thread_id
            finally:
                await runtime.aclose()
            body = requests[-1]
            messages = body["input" if api_mode == "responses" else "messages"]
            selected = [m for m in messages if "<managed_developer_instructions>" in json.dumps(m)]
            assert len(selected) == index + 1
            assert all(
                m["role"] == ("developer" if api_mode == "responses" else "system")
                for m in selected
            )
            assert "FIXED BASE" not in json.dumps(selected)
            newest = json.dumps(selected[-1])
            if index == 0:
                assert "POLICY ONE" in newest and "replace all" not in newest
            elif index == 1:
                assert "POLICY TWO" in newest and "replace all previously provided" in newest
            else:
                assert "no longer apply" in newest
            assert "managed_config.developer_instructions" not in json.dumps(body)
        assert len(requests) == 3

    asyncio.run(scenario())
