"""Role changes and tombstones reach ordinary wire requests without rewriting history."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.prompting import PromptContribution, PromptRole, PromptSlot
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_step_role_change_removal_and_cold_request_preserve_authority(
    tmp_path, monkeypatch, api_mode
):
    async def scenario():
        requests = []

        class Contributor:
            def contributions(self, **kwargs):
                if len(requests) >= 2:
                    return ()
                return (
                    PromptContribution(
                        "fixture.role",
                        "memory/consolidation_inputs",
                        PromptRole.DEVELOPER if not requests else PromptRole.USER,
                        PromptSlot.EXTENSIONS,
                        variables={"inputs": "ROLE_PROOF"},
                        separate_message=True,
                    ),
                )

        class Advance:
            spec = ToolSpec("advance", "Continue the fixture step", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                return ToolResult(call.id, call.name, "advanced")

        def respond(request):
            assert request.url.host == "fixture.invalid"
            body = json.loads(request.content)
            requests.append(body)
            index = len(requests)
            if api_mode == "responses":
                assert request.url.path == "/v1/responses"
                item = (
                    {
                        "type": "function_call",
                        "id": f"item-{index}",
                        "call_id": f"call-{index}",
                        "name": "advance",
                        "arguments": "{}",
                    }
                    if index <= 3
                    else {"type": "message", "content": [{"type": "output_text", "text": "done"}]}
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{index}", "output": [item]},
                }
                text = f"data: {json.dumps(packet)}\n\n"
            else:
                assert request.url.path == "/v1/chat/completions"
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": "advance", "arguments": "{}"},
                            }
                        ]
                    }
                    if index <= 3
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {"delta": delta, "finish_reason": "tool_calls" if index <= 3 else "stop"}
                    ]
                }
                text = f"data: {json.dumps(packet)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text)

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        tool = Advance()

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(tool)
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    api_mode=api_mode,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                registry=registry,
                context_contributors=(Contributor(),),
                thread_id=thread,
            )

        runtime = await create()
        try:
            assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
            assert len(requests) == 4 and tool.calls == 3
            stored = await runtime._repository.load_items(runtime.thread_id)
            contexts = tuple(
                i for i in stored if isinstance(i, ContextItem) and i.key == "fixture.role"
            )
            assert len(contexts) == 4
            assert [i.role.value for i in contexts] == ["developer", "developer", "user", "user"]
            assert (
                "no longer apply" in contexts[1].content
                and "no longer apply" in contexts[3].content
            )
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance([e async for e in runtime.stream("cold")][-1], TurnCompleted)
            after = await runtime._repository.load_items(thread)
            assert after[: len(stored)] == stored
            assert (
                tuple(i for i in after if isinstance(i, ContextItem) and i.key == "fixture.role")
                == contexts
            )
            upper = "developer" if api_mode == "responses" else "system"
            for index, body in enumerate(requests):
                messages = body["input" if api_mode == "responses" else "messages"]
                selected = [
                    m
                    for m in messages
                    if "ROLE_PROOF" in json.dumps(m) or "fixture.role" in json.dumps(m)
                ]
                assert [m["role"] for m in selected] == (
                    [upper]
                    if index == 0
                    else [upper, upper, "user"]
                    if index == 1
                    else [upper, upper, "user", "user"]
                )
                if index:
                    assert "no longer apply" in json.dumps(selected[1])
            assert tool.calls == 3
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
