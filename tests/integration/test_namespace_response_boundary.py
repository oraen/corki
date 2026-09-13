"""Only flat aliases may authorize calls, including before typed item filtering."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("namespace", [None, "group", 42, {"group": "wrong"}])
@pytest.mark.parametrize("boundary", ["added", "done", "completed"])
def test_namespace_cannot_be_ignored_or_routed_to_leaf(tmp_path, monkeypatch, namespace, boundary):
    async def scenario():
        requests, calls = [], []

        class Probe:
            def __init__(self, name):
                self.spec = ToolSpec(name, "fixture", {})

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "PROOF")

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            payload = json.loads(request.content)
            requests.append(payload)
            assert all(t["type"] == "function" and "namespace" not in t for t in payload["tools"])
            events = []
            if len(requests) == 1:
                item = {
                    "type": "function_call",
                    "id": "i",
                    "call_id": "c",
                    "arguments": "{}",
                    "name": compatible_tool_name("group::probe") if namespace is None else "probe",
                }
                if namespace is not None:
                    item["namespace"] = namespace
                if boundary == "completed":
                    events.append(
                        {"type": "response.completed", "response": {"id": "r", "output": [item]}}
                    )
                else:
                    events.append({"type": "response.output_item." + boundary, "item": item})
                    if boundary == "added":
                        events.append({"type": "response.output_item.done", "item": item})
            else:
                assert any(
                    i.get("type") == "function_call_output" and i["output"] == "PROOF"
                    for i in payload["input"]
                )
            events.append({"type": "response.completed", "response": {"id": "r"}})
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        registry = ToolRegistry()
        registry.register(Probe("probe"))
        registry.register(Probe("group::probe"))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                api_mode="responses",
                api_base="https://fixture.invalid/v1",
                api_key="fixture",
                skills_enabled=False,
                tool_namespace_mode="native",
                model_max_retries=0,
                model_request_max_retries=0,
            ),
            database_path=tmp_path / "history.db",
            registry=registry,
        )
        try:
            events = [e async for e in runtime.stream("call")]
            stored = await runtime._repository.load_items(runtime.thread_id)
            if namespace is None:
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert calls == ["group::probe"] and len(requests) == 2
            else:
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert "namespace" in events[-1].error and not events[-1].retryable
                assert calls == [] and len(requests) == 1
                assert not any(isinstance(i, (ToolCallItem, ToolResultItem)) for i in stored)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
