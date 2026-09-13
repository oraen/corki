"""Namespace policy reaches actual cells, direct dispatch and discovery, not only prompts."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_code_mode import request_call

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_namespace_policy_controls_real_module_and_direct_dispatch(tmp_path, mode):
    if not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            f'[tools]\nmode="{mode}"\n'
            "[skills]\nenabled=false\n"
            "[features.code_mode]\n"
            'direct_only_tool_namespaces=["direct_only"]\n'
            'excluded_tool_namespaces=["excluded"]\n'
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        calls, requests = [], []

        class Probe:
            def __init__(self, name, exposure):
                self.spec = ToolSpec(name, "namespace proof", {}, exposure=exposure)

            async def execute(self, call, context):
                calls.append(call.name)
                return ToolResult(call.id, call.name, "PROOF:" + call.name)

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec",
                        "text({excluded:typeof tools.excluded__lookup,"
                        "directOnly:typeof tools.direct_only__lookup,"
                        "names:ALL_TOOLS.map(t=>t.name)});"
                        "text(await tools.allowed__lookup({}));",
                    )
                elif len(requests) == 2:
                    output = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert '"excluded":"undefined"' in output.content, output.content
                    assert '"directOnly":"undefined"' in output.content, output.content
                    assert '"names":["allowed__lookup"]' in output.content, output.content
                    assert "PROOF:allowed::lookup" in output.content
                    yield request_call(request, "direct_only::lookup", {})
                elif len(requests) == 3:
                    assert any(
                        isinstance(i, ToolResultItem) and i.content == "PROOF:direct_only::lookup"
                        for i in request.items
                    )
                    yield request_call(request, "excluded::lookup", {})
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert output.is_error is (mode == "code_mode_only")
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe("allowed::lookup", ToolExposure.DIRECT))
        registry.register(Probe("direct_only::lookup", ToolExposure.DEFERRED))
        registry.register(Probe("excluded::lookup", ToolExposure.DIRECT))
        runtime = LangGraphRuntime.create(
            settings=settings, registry=registry, model=Model(), database_path=tmp_path / "state.db"
        )
        try:
            events = [e async for e in runtime.stream("inspect then call")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["allowed::lookup", "direct_only::lookup"] + (
                ["excluded::lookup"] if mode == "code_mode" else []
            )
            for request in requests:
                tools = {s.name: s for s in request.tools}
                assert "tool_search" not in tools
                assert tools["direct_only::lookup"].exposure == ToolExposure.DIRECT_MODEL_ONLY
                definitions = json.loads(
                    tools["exec"].description.split("Direct nested definitions: ")[1]
                )
                assert set(definitions) == {"allowed__lookup"}
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("search_mode", ["native", "compatible"])
def test_exclusion_preserves_search_load_call_and_observation(tmp_path, search_mode):
    async def scenario():
        requests, calls = [], []

        class Lookup:
            spec = ToolSpec("excluded::lookup", "amberproof", {}, exposure=ToolExposure.DEFERRED)

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "SEARCH_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                names = {spec.name for spec in request.tools}
                if len(requests) == 1:
                    assert "excluded::lookup" not in names
                    yield request_call(request, "tool_search", {"query": "amberproof"})
                elif len(requests) == 2:
                    # Legacy native configuration also uses Harness-owned
                    # discovery and loads ordinary callable definitions.
                    assert "excluded::lookup" in names
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert [s.name for s in result.discovered_tools] == ["excluded::lookup"]
                    yield request_call(request, "excluded::lookup", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and i.content == "SEARCH_PROOF"
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Lookup())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_mode="code_mode",
                tool_search_mode=search_mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                code_mode_excluded_tool_namespaces=("excluded",),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("find")][-1], TurnCompleted)
            assert len(calls) == 1 and len(requests) == 3
            assert (
                "excluded__lookup"
                not in next(t for t in requests[0].tools if t.name == "exec").description
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("changed_policy", [False, True])
def test_cold_checkpoint_validates_saved_exposure_before_direct_dispatch(tmp_path, changed_policy):
    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            code_mode_direct_only_tool_namespaces=("notes",),
        )
        requests, calls = [], []

        class Lookup:
            spec = ToolSpec("notes::lookup", "coldproof", {}, exposure=ToolExposure.DEFERRED)

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "COLD_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    assert (
                        next(t for t in request.tools if t.name == "notes::lookup").exposure
                        == ToolExposure.DIRECT_MODEL_ONLY
                    )
                    yield request_call(request, "notes::lookup", {})
                else:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    assert result.is_error is changed_policy
                    assert (
                        "definition changed" if changed_policy else "COLD_PROOF"
                    ) in result.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        def create(selected, thread=None):
            registry = ToolRegistry()
            registry.register(Lookup())
            return LangGraphRuntime.create(
                settings=selected,
                registry=registry,
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        old = create(settings)
        await old._ensure_ready()
        thread, turn = old.thread_id, new_turn_id()
        user = UserMessageItem("only once", turn)
        await old._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))

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
            replace(settings, code_mode_direct_only_tool_namespaces=())
            if changed_policy
            else settings,
            thread,
        )
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == (0 if changed_policy else 1)
            stored = await cold._repository.load_items(thread)
            assert (
                sum(isinstance(i, UserMessageItem) and i.content == "only once" for i in stored)
                == 1
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_missing_engine_fallback_retains_default_namespace_projection(tmp_path, monkeypatch):
    monkeypatch.setattr(CodeModeService, "available", staticmethod(lambda: False))

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "exec" not in {s.name for s in request.tools}
                assert all(s.exposure == ToolExposure.DIRECT_MODEL_ONLY for s in request.tools)
                assert "tool_search" not in {s.name for s in request.tools}
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode",
                code_mode_direct_only_tool_namespaces=("functions",),
            ),
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            assert isinstance([e async for e in runtime.stream("inspect")][-1], TurnCompleted)
            assert len(requests) == 1 and requests[0].tools
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
