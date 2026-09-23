import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode,native,switch_mode",
    [
        ("responses", True, False),
        ("responses", False, False),
        ("chat_completions", False, False),
        ("responses", True, True),
        ("responses", False, True),
    ],
)
def test_same_leaf_names_route_independently_and_replay_after_restart(
    tmp_path, mode, native, switch_mode
):
    async def scenario():
        requests, calls = [], []

        class Read:
            def __init__(self, name):
                self.spec = ToolSpec(
                    name,
                    "Read " + name,
                    {"type": "object"},
                    namespace_description="Private recovery" if "::" in name else None,
                )

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "RESULT " + call.name)

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            first = len(requests) == 1
            names = []
            for spec in payload.get("tools", []):
                assert spec["type"] != "namespace"
                if spec["type"] == "namespace":
                    for leaf in spec["tools"]:
                        names.append((spec["name"], leaf["name"]))
                else:
                    names.append((None, spec.get("function", spec)["name"]))
            if mode == "responses":
                output = (
                    [
                        {
                            "type": "function_call",
                            "id": f"i-{i}",
                            "call_id": f"c-{i}",
                            "name": name,
                            **({"namespace": ns} if ns else {}),
                            "arguments": "{}",
                        }
                        for i, (ns, name) in enumerate(names)
                    ]
                    if first
                    else [
                        {
                            "type": "message",
                            "id": f"m-{len(requests)}",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ]
                )
                packet = {
                    "type": "response.completed",
                    "response": {"id": f"r-{len(requests)}", "output": output},
                }
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": i,
                                "id": f"c-{i}",
                                "type": "function",
                                "function": {"name": name, "arguments": "{}"},
                            }
                            for i, (_, name) in enumerate(names)
                        ]
                    }
                    if first
                    else {"content": "done"}
                )
                packet = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        async def create(thread=None):
            active_native = not native if thread is not None and switch_mode else native
            registry = ToolRegistry()
            for name in ("history::read", "notes::read", "read"):
                registry.register(Read(name))
            adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
            model = adapter(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=replace(
                    resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=mode),
                    supports_native_namespaces=active_native,
                ),
            )
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    api_mode=mode,
                    tool_namespace_mode="native" if active_native else "compatible",
                ),
                database_path=tmp_path / "sessions.db",
                model=model,
                registry=registry,
                thread_id=thread,
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read all three")]
            assert isinstance(events[-1], TurnCompleted)
            assert calls == ["history::read", "notes::read", "read"]
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            events = [event async for event in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 3 and len(calls) == 3
            assert all(tool["type"] == "function" for tool in requests[0]["tools"])
            from corki.protocol.tool_names import compatible_tool_name

            replay_names = (
                [
                    item["name"]
                    for item in requests[-1]["input"]
                    if item.get("type") == "function_call"
                ]
                if mode == "responses"
                else [
                    call["function"]["name"]
                    for item in requests[-1]["messages"]
                    for call in item.get("tool_calls", [])
                ]
            )
            assert replay_names == [compatible_tool_name(name) for name in calls]
            if not native:
                names = [tool.get("function", tool)["name"] for tool in requests[0]["tools"]]
                assert len(set(names)) == 3 and all(
                    "::" not in name and len(name) <= 64 for name in names
                )
            assert "RESULT history::read" in str(requests[-1])
            assert "RESULT notes::read" in str(requests[-1])
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("namespace", ["alien", "functions"])
def test_unknown_namespace_never_dispatches_to_default_leaf(tmp_path, namespace):
    async def scenario():
        requests, calls = [], []

        class Read:
            spec = ToolSpec("read", "Default read", {})

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "default result")

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            item = (
                {
                    "type": "function_call",
                    "id": "call-item",
                    "call_id": "call",
                    "namespace": namespace,
                    "name": "read",
                    "arguments": "{}",
                }
                if len(requests) == 1
                else {
                    "type": "message",
                    "id": "answer",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            )
            packet = {
                "type": "response.completed",
                "response": {"id": f"r-{len(requests)}", "output": [item]},
            }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        registry = ToolRegistry()
        registry.register(Read())
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid", api_mode="responses"),
                supports_native_namespaces=True,
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_namespace_mode="native",
            ),
            database_path=tmp_path / "sessions.db",
            model=model,
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("read")]
            from corki.protocol.events import TurnFailed

            assert isinstance(events[-1], TurnFailed)
            assert "namespace" in events[-1].error
            assert calls == []
            assert len(requests) == 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["alias", "description"])
@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_namespace_fault_fails_before_any_provider_request(tmp_path, failure, api_mode):
    from corki.protocol.events import TurnFailed
    from corki.protocol.tool_names import compatible_tool_name

    async def scenario():
        requests = []

        class Read:
            def __init__(self, spec):
                self.spec = spec

            async def execute(self, call, context):
                raise AssertionError("must not execute")

        def respond(request):
            requests.append(request)
            return httpx.Response(500)

        registry = ToolRegistry()
        registry.register(Read(ToolSpec("notes::read", "read", {}, namespace_description="Notes")))
        if failure == "alias":
            registry.register(Read(ToolSpec(compatible_tool_name("notes::read"), "other", {})))
        if failure == "description":
            registry.register(
                Read(ToolSpec("notes::write", "write", {}, namespace_description="Different"))
            )
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model_type = OpenAIResponsesModel if api_mode == "responses" else OpenAICompatibleModel
        model = model_type(
            api_key="fixture",
            base_url="https://fixture.invalid",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid", api_mode=api_mode),
                supports_native_namespaces=failure != "capability",
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode=api_mode,
                tool_namespace_mode=(
                    "native"
                    if api_mode == "responses" and failure == "description"
                    else "compatible"
                ),
                error_on_tool_collisions=failure == "description",
            ),
            database_path=tmp_path / "sessions.db",
            model=model,
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnFailed)
            assert not requests
            assert (
                "collision"
                if failure == "alias"
                else "tool collision: notes"
                if failure == "description"
                else "capability"
            ) in events[-1].error
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_namespaced_handlers_keep_canonical_identity_inside_real_code_mode(tmp_path):
    from corki.models import ModelCompleted
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    async def scenario():
        calls, requests = [], []

        class Read:
            def __init__(self, name):
                self.spec = ToolSpec(name, "Read", {})

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "VALUE " + call.name)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    raw_arguments=(
                                        "text(await tools.history__read({})); "
                                        "text(await tools.notes__read({})); "
                                        "text(await tools.read({}));"
                                    ),
                                    input_kind="freeform",
                                ),
                                turn,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    assert len(results) == 1 and not results[0].is_error
                    assert "VALUE history::read" in results[0].content
                    assert "VALUE notes::read" in results[0].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        for name in ("history::read", "notes::read", "read"):
            registry.register(Read(name))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=registry,
        )
        try:
            events = [event async for event in runtime.stream("read all")]
            assert isinstance(events[-1], TurnCompleted)
            assert calls == ["history::read", "notes::read", "read"]
            assert len(requests) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "native_namespace,native_search", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("native_freeform", [False, True])
def test_namespaced_discovery_loads_freeform_and_survives_reopening(
    tmp_path, native_namespace, native_search, native_freeform
):
    from corki.protocol.items import ToolResultItem
    from corki.protocol.tools import ToolExposure

    async def scenario():
        requests, calls = [], []

        class Read:
            spec = ToolSpec(
                "vault::recover",
                "Recover prior text",
                {},
                exposure=ToolExposure.DEFERRED_MODEL_ONLY,
                input_kind="freeform",
                namespace_description="vaultproof recovery service",
            )

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "RECOVERED")

        def respond(request):
            payload = json.loads(request.content)
            requests.append(payload)
            index = len(requests)
            if index == 1:
                assert "vaultproof" not in str(payload["tools"])
                item = {
                    "type": "function_call",
                    "name": "tool_search",
                    "call_id": "search",
                    "arguments": json.dumps({"query": "vaultproof", "limit": 1}),
                }
            elif index == 2:
                definitions = payload["tools"]
                definitions = [spec for spec in definitions if spec.get("name") != "tool_search"]
                assert len(definitions) == 1
                definition = definitions[0]
                assert definition["type"] == "function"
                assert definition["parameters"] == {
                    "type": "object",
                    "properties": {"input": {"type": "string"}},
                    "required": ["input"],
                    "additionalProperties": False,
                }
                item = {
                    "type": "function_call",
                    "name": definition["name"],
                    "call_id": "read",
                    "arguments": json.dumps({"input": "raw body"}),
                }
            else:
                assert "RECOVERED" in str(payload["input"])
                item = {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            item["id"] = f"item-{index}"
            packet = {
                "type": "response.completed",
                "response": {"id": f"r-{index}", "output": [item]},
            }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Read())
            capabilities = replace(
                resolve_capabilities(base_url="https://fixture.invalid", api_mode="responses"),
                supports_native_namespaces=native_namespace,
                supports_native_tool_search=native_search,
                supports_native_freeform=native_freeform,
            )
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid",
                client=client,
                capabilities=capabilities,
            )
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    api_mode="responses",
                    tool_namespace_mode="native" if native_namespace else "compatible",
                    tool_search_mode="native" if native_search else "compatible",
                    model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                    tool_freeform_mode="native" if native_freeform else "compatible",
                ),
                database_path=tmp_path / "sessions.db",
                model=model,
                registry=registry,
                thread_id=thread,
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("recover text")]
            assert isinstance(events[-1], TurnCompleted)
            assert [(call.name, call.raw_arguments, call.input_kind) for call in calls] == [
                ("vault::recover", "raw body", "freeform")
            ]
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            events = [event async for event in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 4 and len(calls) == 1
            stored = await cold._repository.load_items(thread)
            found = [
                spec
                for item in stored
                if isinstance(item, ToolResultItem)
                for spec in item.discovered_tools
            ]
            assert len(found) == 1 and found[0] == Read.spec
        finally:
            await cold.aclose()
            await client.aclose()

    asyncio.run(scenario())
