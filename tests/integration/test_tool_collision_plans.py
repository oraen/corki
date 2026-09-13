"""Native finalizer ownership is applied to the selected Turn, not at startup."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, OpenAIResponsesModel
from corki.models.capabilities import ProviderCapabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("control", ["tool_search", "tool_search::inspect", "exec", "wait"])
@pytest.mark.parametrize("strict", [False, True])
def test_default_control_collision_uses_harness_handler_without_losing_source(
    tmp_path, control, strict
):
    async def scenario():
        executions, requests = [], []
        is_search = control.startswith("tool_search")

        class Tool:
            def __init__(self, name, exposure=ToolExposure.DIRECT):
                self.spec = ToolSpec(name, "amberproof", {}, exposure=exposure)

            async def execute(self, call, context):
                executions.append(call.name)
                return ToolResult(call.id, call.name, "SOURCE_RESULT")

        registry = ToolRegistry()
        source = Tool(control)
        registry.register(source)
        registry.register(Tool("probe", ToolExposure.DEFERRED))

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    if is_search:
                        yield request_call(request, "tool_search", {"query": "amberproof"})
                    else:
                        yield request_call(request, "exec", "text('HARNESS_RESULT')")
                elif len(requests) == 2 and is_search:
                    yield request_call(request, "probe", {})
                else:
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert any(
                        ("SOURCE_RESULT" if is_search else "HARNESS_RESULT") in i.content
                        for i in results
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                error_on_tool_collisions=strict,
                tool_mode="direct" if is_search else "code_mode_only",
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("use the harness control")]
            assert isinstance(events[-1], TurnFailed if strict else TurnCompleted), events[-1]
            if strict:
                identity = control.replace("::", ".") if "::" in control else f"functions.{control}"
                assert f"tool collision: {identity}" in events[-1].error
                assert not requests and not executions
            else:
                assert executions == (["probe"] if is_search else [])
            assert registry.get(control) is source, "planning must not delete the host source"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cold", [False, True])
def test_strict_current_catalog_does_not_reject_old_discovered_namespace_history(tmp_path, cold):
    async def scenario():
        bodies, executed = [], []

        class Tool:
            def __init__(self, leaf, description):
                self.spec = ToolSpec(
                    f"shared::{leaf}",
                    "amberproof",
                    {},
                    exposure=ToolExposure.DEFERRED,
                    namespace_description=description,
                )

            async def execute(self, call, context):
                executed.append(call.name)
                return ToolResult(call.id, call.name, "RESULT_" + call.name)

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/responses"
            body = json.loads(request.content)
            bodies.append(body)
            index = len(bodies)
            if index in {1, 4}:
                output = [
                    {
                        "type": "function_call",
                        "name": "tool_search",
                        "call_id": f"search-{index}",
                        "arguments": json.dumps({"query": "amberproof"}),
                    }
                ]
            elif index in {2, 5}:
                output = [
                    {
                        "type": "function_call",
                        "name": next(
                            tool["name"]
                            for tool in body["tools"]
                            if tool["description"].startswith(
                                "shared::old\n" if index == 2 else "shared::new\n"
                            )
                        ),
                        "call_id": f"read-{index}",
                        "arguments": "{}",
                    }
                ]
            else:
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": f"r-{index}", "output": output},
                    }
                )
                + "\n\n",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

        def create(tool, thread=None):
            registry = ToolRegistry()
            owner = registry.create_owner()
            registry.replace_owned(owner, (tool,))
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    model="fixture",
                    skills_enabled=False,
                    api_mode="responses",
                    tool_namespace_mode="native",
                    tool_search_mode="native",
                    error_on_tool_collisions=True,
                    model_contexts=(ModelContextInfo("fixture", supports_search_tool=True),),
                ),
                registry=registry,
                database_path=tmp_path / "s.db",
                thread_id=thread,
                model=OpenAIResponsesModel(
                    api_key="fixture",
                    base_url="https://fixture.invalid",
                    client=client,
                    capabilities=ProviderCapabilities(
                        name="fixture",
                        api_mode="responses",
                        supports_native_namespaces=True,
                        supports_native_tool_search=True,
                    ),
                ),
            )
            return runtime, registry, owner

        runtime, registry, owner = create(Tool("old", "Old namespace."))
        try:
            first = [e async for e in runtime.stream("discover and read old")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            assert len(bodies) == 3
            archived = await runtime._repository.load_items(runtime.thread_id)
            if cold:
                thread = runtime.thread_id
                await runtime.aclose()
                runtime, registry, owner = create(Tool("new", "New namespace."), thread)
            else:
                registry.replace_owned(owner, (Tool("new", "New namespace."),))
            second = [e async for e in runtime.stream("discover and read new")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert executed == ["shared::old", "shared::new"]
            assert len(bodies) == 6
            replay = json.dumps(bodies[-1]["input"])
            assert "Old namespace." not in replay and "New namespace." in replay
            assert "Definitions changed or are unavailable; search again." in replay
            assert "RESULT_shared::old" in replay and "RESULT_shared::new" in replay
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(archived)] == archived
            searches = [
                item
                for item in stored
                if isinstance(item, ToolResultItem) and item.tool_name == "tool_search"
            ]
            assert [s.discovered_tools[0].namespace_description for s in searches] == [
                "Old namespace.",
                "New namespace.",
            ]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("lite", [False, True])
@pytest.mark.parametrize("inventory", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("same_owner", [False, True])
def test_strict_namespace_owner_policy_is_gated_in_real_turn(
    tmp_path, lite, inventory, hidden, same_owner
):
    async def scenario():
        requests = []

        class Tool:
            def __init__(self, name, owner, exposure=ToolExposure.DIRECT):
                self.spec = ToolSpec(
                    f"shared::{name}", "proof", {}, exposure=exposure, source="forged-owner"
                )
                self.mcp_server_name = owner

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool("a", "server"))
        registry.register(
            Tool(
                "b",
                "server" if same_owner else None,
                ToolExposure.HIDDEN if hidden else ToolExposure.DIRECT,
            )
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                model="fixture",
                skills_enabled=False,
                model_contexts=(ModelContextInfo("fixture", use_responses_lite=lite),),
                error_on_tool_collisions=True,
                turn_metadata_includes_tool_info=inventory,
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("check namespace owners")]
            fails = False  # Removed Lite inventory does not impose a remote owner schema.
            assert isinstance(events[-1], TurnFailed if fails else TurnCompleted), events[-1]
            assert len(requests) == (0 if fails else 1)
            if fails:
                assert "tool collision: shared" in events[-1].error
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("first_description", ["First namespace.", "\x1c", "\x1f"])
def test_namespace_description_policy_reaches_actual_http_or_fails_before_sampling(
    tmp_path, strict, first_description
):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            f"[features.tool_registry]\nerror_on_tool_collisions={str(strict).lower()}\n"
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        settings = replace(
            settings, api_mode="responses", tool_namespace_mode="native", skills_enabled=False
        )
        calls = []

        class Tool:
            def __init__(self, name, description):
                self.spec = ToolSpec(
                    f"shared::{name}", "fixture", {}, namespace_description=description
                )

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "unused")

        registry = ToolRegistry()
        registry.register(Tool("z", first_description))
        registry.register(Tool("a", "Second namespace."))

        def respond(request):
            body = json.loads(request.content)
            calls.append(body)
            assert str(request.url) == "https://fixture.invalid/responses"
            assert all(tool["type"] == "function" for tool in body["tools"])
            shared = [t for t in body["tools"] if t["description"].startswith("shared::")]
            assert len(shared) == 2 and len({t["name"] for t in shared}) == 2
            assert {t["description"] for t in shared} == {
                f"shared::z\n{first_description}\nfixture",
                "shared::a\nSecond namespace.\nfixture",
            }
            return httpx.Response(
                200,
                text='data: {"type":"response.completed","response":{"id":"r","output":[]}}\n\n',
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        runtime = LangGraphRuntime.create(
            settings=settings,
            registry=registry,
            model=OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid",
                client=client,
                capabilities=ProviderCapabilities(
                    name="fixture", api_mode="responses", supports_native_namespaces=True
                ),
            ),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("use shared namespace")]
            assert isinstance(events[-1], TurnFailed if strict else TurnCompleted), events[-1]
            assert len(calls) == (0 if strict else 1)
            if strict:
                assert "tool collision: shared" in events[-1].error
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
