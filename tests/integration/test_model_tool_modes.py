"""Captured model tool mode selects a real step router and worker."""

import asyncio
import re
from dataclasses import replace

import pytest
from test_code_mode import request_call
from test_mcp_exposure_surfaces import install_http_fixture

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.items import ContextItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "configured,selected",
    [("direct", "code_mode_only"), ("code_mode_only", "direct"), ("direct", "code_mode")],
)
def test_catalog_mode_overrides_configuration_and_executes(tmp_path, configured, selected):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            f'[agent]\nmodel="fixture"\n[skills]\nenabled=false\n'
            f'[tools]\nmode="{configured}"\n'
            f'[models.catalog.fixture]\ntool_mode="{selected}"\n'
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        requests, calls = [], []

        class Probe:
            spec = ToolSpec("probe", "model mode proof", {})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "MODEL_MODE_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                names = {t.name for t in request.tools}
                assert ("exec" in names) == (selected != "direct")
                assert ("probe" in names) == (selected != "code_mode_only")
                if len(requests) == 1:
                    yield (
                        request_call(request, "probe", {})
                        if selected == "direct"
                        else request_call(request, "exec", "text(await tools.probe({}));")
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "MODEL_MODE_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=settings, registry=registry, model=Model(), database_path=tmp_path / "s.db"
        )
        try:
            events = [e async for e in runtime.stream("use model tool mode")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(calls) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def mode_settings(tmp_path, *, initial="nested", **changes):
    config = tmp_path / "modes.toml"
    config.write_text(
        f'[agent]\nmodel="{initial}"\n[skills]\nenabled=false\n'
        '[models.catalog.nested]\ntool_mode="code_mode_only"\nuse_responses_lite=true\n'
        "supports_search_tool=true\n"
        '[models.catalog.direct]\ntool_mode="direct"\nuse_responses_lite=true\n'
        "supports_search_tool=true\n"
    )
    return replace(CorkiSettings.for_directory(tmp_path, config_file=config), **changes)


def test_active_model_update_retains_turn_router_then_next_turn_uses_new_mode(tmp_path):
    async def scenario():
        requests, calls = [], []

        class Probe:
            spec = ToolSpec("probe", "proof", {})

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                number = len(requests)
                assert ("exec" in {t.name for t in request.tools}) == (number <= 2)
                if number == 1:
                    changed = await runtime.update_turn_settings(
                        runtime._active_run.turn_id, model="direct"
                    )
                    assert changed.status == "applied", changed
                    yield request_call(request, "exec", "text(await tools.probe({}));")
                elif number == 3:
                    yield request_call(request, "probe", {})
                else:
                    assert (
                        "PROOF"
                        in [i for i in request.items if isinstance(i, ToolResultItem)][-1].content
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=mode_settings(tmp_path, step_model_switching=True),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            await runtime.update_thread_settings(model="direct")
            assert isinstance([e async for e in runtime.stream("second")][-1], TurnCompleted)
            assert [r.model for r in requests] == ["nested", "direct", "direct", "direct"]
            assert calls == ["probe", "probe"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_new_turn_replans_mcp_mask_inventory_and_context_without_global_mutation(
    tmp_path, monkeypatch
):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                nested = request.model == "nested"
                assert request.client_metadata is None
                catalog = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and i.key == "extensions.mcp.catalog"
                ][-1]
                assert ("Deferred sources: fixture" in catalog.content) == (not nested)
                if len(requests) == 1:
                    yield request_call(request, "tool_search", {"query": "amberproof"})
                elif len(requests) in (2, 4):
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    assert (
                        "ECHO_PROOF"
                        in [i for i in request.items if isinstance(i, ToolResultItem)][-1].content
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=mode_settings(
                tmp_path,
                initial="direct",
                api_mode="responses",
                tool_search_mode="native",
                tool_namespace_mode="native",
                turn_metadata_includes_tool_info=True,
                mcp_servers=(
                    MCPServerSettings(
                        "fixture",
                        "http",
                        url="https://fixture.invalid/mcp",
                        omit_tools_from=("code_mode",),
                    ),
                ),
            ),
            registry=ToolRegistry(),
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("find")][-1], TurnCompleted)
            original = runtime._registry.specs()
            await runtime.update_thread_settings(model="nested")
            events = [e async for e in runtime.stream("call directly")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert runtime._registry.specs() == original
            assert (
                len(clients) == 1 and methods.count("tools/list") == 1 and calls == ["echo", "echo"]
            )
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


def test_cell_waits_through_direct_turn_then_uses_new_code_mode_worker(tmp_path):
    async def scenario():
        held, release, finished, probed = (asyncio.Event() for _ in range(4))
        requests, ids = [], []

        class Hold:
            spec = ToolSpec("hold", "hold", {})

            async def execute(self, call, context):
                held.set()
                await release.wait()
                finished.set()
                return ToolResult(call.id, call.name, "released")

        class Probe:
            spec = ToolSpec("probe", "proof", {})

            async def execute(self, call, context):
                probed.set()
                return ToolResult(call.id, call.name, "LATER_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                number = len(requests)
                if number == 1:
                    yield request_call(
                        request,
                        "exec",
                        '// @exec: {"yield_time_ms":10}\n'
                        "await tools.hold({}); text(await tools.probe({}));",
                    )
                elif number == 2:
                    await asyncio.wait_for(held.wait(), 2)
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    ids.append(re.search(r"cell ID (\S+)", output.content)[1])
                    yield ModelCompleted(())
                elif number == 3:
                    assert not runtime._code_mode.active.is_set()
                    assert "exec" not in {t.name for t in request.tools}
                    release.set()
                    await asyncio.wait_for(finished.wait(), 2)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(probed.wait(), 0.05)
                    yield ModelCompleted(())
                elif number == 4:
                    yield request_call(request, "wait", {"cell_id": ids[0]})
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "LATER_PROOF" in output.content and "Script completed" in output.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Hold())
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=mode_settings(tmp_path),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            for model in ("nested", "direct", "nested"):
                await runtime.update_thread_settings(model=model)
                events = [e async for e in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert probed.is_set() and len(requests) == 5
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,disabled,strict",
    [("code_mode", False, False), ("code_mode", True, True), ("code_mode_only", False, True)],
)
def test_unavailable_engine_model_selection_warns_once_and_returns_observations(
    tmp_path, monkeypatch, mode, disabled, strict
):
    async def scenario():
        monkeypatch.setattr("corki.code_mode.service.CodeModeService.available", lambda: False)
        requests, called = [], []
        stages = 0

        class Probe:
            spec = ToolSpec("probe", "proof", {})

            async def execute(self, call, context):
                called.append(call.name)
                return ToolResult(call.id, call.name, "DIRECT_PROOF")

        class Model:
            async def stream(self, request):
                nonlocal stages
                requests.append(request)
                names = {t.name for t in request.tools}
                if request.model == "direct":
                    assert "exec" not in names
                    yield ModelCompleted(())
                    return
                assert ("exec" in names) is strict
                assert ("probe" in names) == (mode != "code_mode_only")
                stage = stages % (3 if strict else 2)
                stages += 1
                if stage == 0:
                    yield request_call(
                        request,
                        "exec" if strict else "probe",
                        "text(await tools.probe({}));" if strict else {},
                    )
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert output.is_error is strict
                    assert ("engine unavailable" if strict else "DIRECT_PROOF") in output.content
                    if strict and stage == 1:
                        yield request_call(request, "wait", {"cell_id": "not-created"})
                    else:
                        yield ModelCompleted(())

            async def aclose(self):
                pass

        base = mode_settings(tmp_path, initial="direct", code_mode_disable_fallback=disabled)
        settings = replace(
            base,
            model_contexts=tuple(
                replace(info, tool_mode=mode) if info.model == "nested" else info
                for info in base.model_contexts
            ),
        )
        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            warnings = []
            for model in ("direct", "nested", "nested"):
                await runtime.update_thread_settings(model=model)
                events = [e async for e in runtime.stream("try tools")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                warnings.append(
                    [
                        e.message
                        for e in events
                        if isinstance(e, WarningEvent) and "engine unavailable" in e.message
                    ]
                )
            assert [len(w) for w in warnings] == [0, 1, 0]
            assert ("fail closed" if strict else "Falling back") in warnings[1][0]
            assert called == ([] if strict else ["probe", "probe"])
            assert not runtime._code_mode.cells
            assert len(requests) == (7 if strict else 5)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
