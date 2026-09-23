"""Ordinary search survives model changes, retries and durable checkpoint recovery."""

import asyncio
from dataclasses import replace

import pytest
from test_code_mode import request_call
from test_mcp_exposure_surfaces import install_http_fixture

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


def settings_for(root, supported):
    return CorkiSettings(
        root,
        model="initial",
        api_mode="responses",
        skills_enabled=False,
        tool_search_mode="native",
        tool_namespace_mode="native",
        step_model_switching=True,
        model_contexts=(
            ModelContextInfo("initial", supports_search_tool=supported),
            ModelContextInfo("other", supports_search_tool=not supported),
        ),
        mcp_servers=(MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp"),),
    )


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("retry", [False, True])
def test_active_model_update_and_retry_keep_search_until_next_turn(
    tmp_path, monkeypatch, supported, retry
):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        requests, counts = [], [0, 0]
        phase = 0

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "tool_search" in {t.name for t in request.tools}
                if len(requests) == 1:
                    changed = await runtime.update_turn_settings(
                        runtime._active_run.turn_id, model="other"
                    )
                    assert changed.status == "applied"
                    if retry:
                        raise ModelError(
                            "retry",
                            kind=ModelErrorKind.TRANSPORT,
                            retryable=True,
                            retry_after_seconds=0,
                        )
                counts[phase] += 1
                index = counts[phase]
                if index == 1:
                    yield request_call(request, "tool_search", {"query": "amberproof"})
                elif index == 2:
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "ECHO_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, supported),
            registry=ToolRegistry(),
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            phase = 1
            await runtime.update_thread_settings(model="other")
            events = [e async for e in runtime.stream("second")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [r.model for r in requests] == ["initial"] * (2 if retry else 1) + ["other"] * 5
            if retry:
                assert requests[0].tools == requests[1].tools
                assert requests[0].items == requests[1].items
            assert calls == ["echo", "echo"]
            assert len(clients) == methods.count("initialize") == methods.count("tools/list") == 1
        finally:
            await runtime.aclose()
        assert closed == ["fixture"]

    asyncio.run(scenario())


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("boundary", ["prepare_model_context", "call_model", "execute_tools"])
def test_cold_checkpoint_keeps_search_selection_when_catalog_changes(
    tmp_path, monkeypatch, supported, boundary
):
    async def scenario():
        calls, methods, clients, closed = install_http_fixture(monkeypatch)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "tool_search" in {t.name for t in request.tools}
                if len(requests) == 1:
                    yield request_call(request, "tool_search", {"query": "amberproof"})
                elif len(requests) == 2:
                    yield request_call(request, "mcp__fixture::echo", {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "ECHO_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = settings_for(tmp_path, supported)

        async def create(selected, thread=None):
            return await LangGraphRuntime.acreate(
                settings=selected,
                registry=ToolRegistry(),
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        old = await create(settings)
        try:
            await old._ensure_ready()
            thread, turn = old.thread_id, new_turn_id()
            user = UserMessageItem("ONLY_ONCE", turn)
            state = _initial_state(thread, turn, settings, user)
            await old._repository.save_turn(
                TurnRecord(
                    turn,
                    thread,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )

            class Sink:
                async def emit(self, event):
                    pass

            await old._compiled.ainvoke(
                state,
                config=old._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=[boundary],
            )
            saved = await old._compiled.aget_state(old._graph_config(turn))
            assert saved.next == (boundary,)
            if boundary != "prepare_model_context":
                assert saved.values["tool_search_enabled"] is True
            assert len(requests) == (1 if boundary == "execute_tools" else 0)
            assert not calls
        finally:
            await old.aclose()
        changed = replace(
            settings,
            model_contexts=tuple(
                replace(i, supports_search_tool=not supported) for i in settings.model_contexts
            ),
        )
        cold = await create(changed, thread)
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["echo"] and len(requests) == 3
            assert (
                sum(
                    isinstance(i, UserMessageItem)
                    for i in await cold._repository.load_items(thread)
                )
                == 1
            )
            events = [e async for e in cold.stream("new capability")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["echo"]
        finally:
            await cold.aclose()
        assert len(clients) == methods.count("tools/list") == 2
        assert closed == ["fixture", "fixture"]

    asyncio.run(scenario())
