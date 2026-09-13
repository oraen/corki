"""Server-owned exposure masks across HTTP MCP, discovery and actual Code Mode cells."""

import asyncio
import json
from dataclasses import replace
from itertools import combinations
from types import SimpleNamespace

import httpx
import pytest
from test_code_mode import request_call

from corki.code_mode.specs import nested_specs
from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolExposure, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry

MASKS = tuple(
    subset
    for size in range(4)
    for subset in combinations(("direct", "deferred", "code_mode"), size)
)


def install_http_fixture(monkeypatch):
    calls, methods, clients, closed = [], [], [], []

    def factory(settings):
        def respond(request):
            if request.method == "GET":
                return httpx.Response(405, headers={"allow": "POST, DELETE"})
            if request.method == "DELETE":
                closed.append(settings.name)
                return httpx.Response(204)
            message = json.loads(request.content)
            method = message["method"]
            methods.append(method)
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                result = {
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {
                    "tools": [{"name": "echo", "description": "amberproof", "inputSchema": {}}]
                }
            elif method == "tools/call":
                calls.append(message["params"]["name"])
                result = {"content": [{"type": "text", "text": "ECHO_PROOF"}]}
            else:
                raise AssertionError(method)
            return httpx.Response(
                200,
                headers={"mcp-session-id": "fixture"},
                json={"jsonrpc": "2.0", "id": message["id"], "result": result},
            )

        client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)
    return calls, methods, clients, closed


@pytest.mark.parametrize("search", ["native", "compatible"])
def test_code_mode_only_mcp_omitted_nested_is_directly_callable(tmp_path, monkeypatch, search):
    async def scenario():
        calls, methods, _, closed = install_http_fixture(monkeypatch)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                tools = {s.name: s for s in request.tools}
                if len(requests) == 1:
                    assert runtime._registry.spec("mcp__fixture::echo") is not None
                    assert tools["mcp__fixture::echo"].exposure == ToolExposure.DIRECT_MODEL_ONLY
                    assert "tool_search" not in tools
                    assert "mcp__fixture__echo" not in tools["exec"].description
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "ECHO_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        server = MCPServerSettings.from_mapping(
            "fixture", {"url": "https://fixture.invalid/mcp", "omit_tools_from": ["code_mode"]}
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_mode="code_mode_only",
                tool_search_mode=search,
                mcp_servers=(server,),
            ),
            registry=ToolRegistry(),
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("echo")]
            assert isinstance(events[-1], TurnCompleted), (events, runtime._mcp_manager.warnings)
            assert calls == ["echo"] and len(requests) == 2
            assert methods.count("initialize") == methods.count("tools/list") == 1
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


@pytest.mark.parametrize("omitted", MASKS)
@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("search", ["disabled", "native", "compatible"])
@pytest.mark.parametrize("prefix", [True, False])
@pytest.mark.parametrize("direct_only", [False, True])
def test_runtime_server_mask_matrix(
    tmp_path,
    monkeypatch,
    omitted,
    mode,
    search,
    prefix,
    direct_only,
    model_search=True,
    provider_namespaces=True,
):
    async def scenario():
        calls, methods, _, closed = install_http_fixture(monkeypatch)
        namespace = "mcp__fixture" if prefix else "fixture"
        name = namespace + "::echo"
        nested = "code_mode" not in omitted and not direct_only
        deferred = (
            search != "disabled"
            and "deferred" not in omitted
            and not direct_only
            and (mode != "code_mode_only" or nested)
        )
        direct = "direct" not in omitted and not deferred
        if nested:
            expected = (
                ToolExposure.DEFERRED
                if deferred
                else ToolExposure.DIRECT
                if direct
                else ToolExposure.CODE_MODE_ONLY
            )
        else:
            expected = (
                ToolExposure.DEFERRED_MODEL_ONLY
                if deferred
                else ToolExposure.DIRECT_MODEL_ONLY
                if direct
                else ToolExposure.HIDDEN
            )
        visible = direct and (mode != "code_mode_only" or not nested)
        registry = ToolRegistry()
        unrelated = namespace + "::unrelated"
        registry.register(SimpleNamespace(spec=ToolSpec(unrelated, "unrelated host tool", {})))
        seen = []

        class Model:
            capabilities = replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                namespace_tools=provider_namespaces,
            )

            async def stream(self, request):
                seen.append(request)
                assert registry.spec(name).exposure == expected
                advertised = {t.name for t in request.tools}
                assert (name in advertised) == visible
                assert ("tool_search" in advertised) == deferred
                assert (unrelated in advertised) == (mode != "code_mode_only" or direct_only)
                module = nested_specs(registry.snapshot())
                assert (name in {s.name for s in module.values()}) == nested
                assert (unrelated in {s.name for s in module.values()}) == (not direct_only)
                assert request.client_metadata is None
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model="fixture",
                api_mode="responses",
                tool_mode=mode,
                tool_search_mode=search,
                tool_namespace_mode="native",
                non_prefixed_mcp_tool_names=not prefix,
                code_mode_direct_only_tool_namespaces=(namespace,) if direct_only else (),
                turn_metadata_includes_tool_info=True,
                model_contexts=(
                    ModelContextInfo(
                        "fixture", use_responses_lite=True, supports_search_tool=model_search
                    ),
                ),
                mcp_servers=(
                    MCPServerSettings(
                        "fixture",
                        "http",
                        url="https://fixture.invalid/mcp",
                        omit_tools_from=omitted,
                    ),
                ),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("inspect surfaces")]
            assert isinstance(events[-1], TurnCompleted), (
                events[-1],
                runtime._mcp_manager.warnings,
            )
            assert not calls and len(seen) == 1 and methods.count("tools/list") == 1
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


@pytest.mark.parametrize("model_search", [False, True])
@pytest.mark.parametrize("provider_namespaces", [False, True])
@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
def test_legacy_search_setting_does_not_require_native_capabilities(
    tmp_path, monkeypatch, model_search, provider_namespaces, mode
):
    test_runtime_server_mask_matrix(
        tmp_path,
        monkeypatch,
        (),
        mode,
        "native",
        True,
        False,
        model_search=model_search,
        provider_namespaces=provider_namespaces,
    )


def test_reconcile_changes_nested_and_search_surfaces_without_reconnecting(tmp_path, monkeypatch):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        settings = MCPServerSettings(
            "fixture", "http", url="https://fixture.invalid/mcp", omit_tools_from=("code_mode",)
        )
        phase, step = 0, 0
        snapshots, searches = [], []

        class Model:
            async def stream(self, request):
                nonlocal step
                step += 1
                if step == 1:
                    snapshots.append(runtime._registry.snapshot())
                    searches.append(runtime._registry.get("tool_search"))
                    if phase == 0:
                        yield request_call(request, "mcp__fixture::echo", {})
                    elif phase == 2:
                        yield request_call(
                            request,
                            "exec",
                            "text({kind:typeof tools.mcp__fixture__echo, "
                            "names:ALL_TOOLS.map(t=>t.name)});"
                            "try {await tools.mcp__fixture__echo({});} "
                            "catch(e) {text('HIDDEN_CALL_BLOCKED');}",
                        )
                    else:
                        yield request_call(
                            request, "exec", "text(await tools.mcp__fixture__echo({}));"
                        )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert not result.is_error, result
                    assert ('"kind":"undefined"' if phase == 2 else "ECHO_PROOF") in result.content
                    if phase == 2:
                        assert "HIDDEN_CALL_BLOCKED" in result.content
                        assert "mcp__fixture__echo" not in result.content
                        assert calls == ["echo"] * 2
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode_only",
                mcp_servers=(settings,),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "s.db",
        )
        try:
            for phase, mask in enumerate(
                (("code_mode",), (), ("direct", "deferred", "code_mode"), ("direct", "deferred"))
            ):
                step = 0
                if phase:
                    runtime.request_mcp_reconcile((replace(settings, omit_tools_from=mask),))
                events = [e async for e in runtime.stream("phase " + str(phase))]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["echo"] * 3 and len(clients) == 1
            assert methods.count("initialize") == methods.count("tools/list") == 1
            assert closed == []
            assert [s.spec("mcp__fixture::echo").exposure for s in snapshots] == [
                ToolExposure.DIRECT_MODEL_ONLY,
                ToolExposure.DEFERRED,
                ToolExposure.HIDDEN,
                ToolExposure.CODE_MODE_ONLY,
            ]
            assert [s is not None for s in searches] == [False, True, False, False]
            assert searches[1]._definitions[0].exposure == ToolExposure.DEFERRED
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


@pytest.mark.parametrize("changed", [False, True])
def test_cold_resume_uses_saved_spec_validation_for_changed_server_mask(
    tmp_path, monkeypatch, changed
):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        server = MCPServerSettings(
            "fixture", "http", url="https://fixture.invalid/mcp", omit_tools_from=("code_mode",)
        )
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode_only",
            mcp_servers=(server,),
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    assert "mcp__fixture::echo" in {s.name for s in request.tools}
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert result.is_error is changed
                    assert ("definition changed" if changed else "ECHO_PROOF") in result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        def create(selected, thread=None):
            return LangGraphRuntime.create(
                settings=selected,
                registry=ToolRegistry(),
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        old = create(settings)
        await old._ensure_ready()
        thread, turn = old.thread_id, new_turn_id()
        user = UserMessageItem("once", turn)
        await old._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "once"))

        class Sink:
            async def emit(self, event):
                pass

        await old._compiled.ainvoke(
            _initial_state(thread, turn, settings, user),
            config=old._graph_config(turn),
            context=GraphRunContext(events=Sink()),
            interrupt_before=["call_model"],
        )
        await old.aclose()
        cold = create(
            replace(
                settings,
                mcp_servers=(replace(server, omit_tools_from=("direct", "deferred", "code_mode")),),
            )
            if changed
            else settings,
            thread,
        )
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ([] if changed else ["echo"])
            assert methods.count("initialize") == 2 and len(clients) == 2
            stored = await cold._repository.load_items(thread)
            assert sum(isinstance(i, UserMessageItem) and i.content == "once" for i in stored) == 1
        finally:
            await cold.aclose()
        assert closed == ["fixture", "fixture"]

    asyncio.run(scenario())
