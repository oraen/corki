"""Deferred namespace hints share a Step with discovery, not a second registry read."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context.deferred_tools import decode_snapshot
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import AssistantTextDelta, ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry

KEY = "tools.deferred_namespaces"


class Read:
    spec = ToolSpec(
        "vault::read",
        "Read vaultproof records",
        {"type": "object"},
        exposure=ToolExposure.DEFERRED,
        namespace_description="vaultproof recovery",
        source="Vault Source",
    )

    def __init__(self, calls):
        self.calls = calls

    async def execute(self, call, context):
        self.calls.append(call.name)
        return ToolResult(call.id, call.name, "RECOVERED")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_catalog_discovery_execution_cold_reopen_and_compaction(tmp_path, enabled, native):
    async def scenario():
        requests, calls = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                catalog = [i for i in request.items if isinstance(i, ContextItem) and i.key == KEY]
                if index != 5:
                    assert len(catalog) == int(enabled)
                    if enabled:
                        assert catalog[0].role.value == "developer"
                        assert (
                            "Deferred tool namespaces:\n- vault: vaultproof recovery"
                            in catalog[0].content
                        )
                    search = next(s for s in request.tools if s.name == "tool_search")
                    assert ("Vault Source" in search.description) is not enabled
                if index in (1, 6):
                    assert all(s.name != "vault::read" for s in request.tools)
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "vaultproof"})
                    item = ToolCallItem(call, turn, step)
                elif index in (2, 7):
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert results[-1].discovered_tools == (Read.spec,)
                    assert any(s.name == "vault::read" for s in request.tools)
                    item = ToolCallItem(ToolCall(new_tool_call_id(), "vault::read", {}), turn, step)
                else:
                    if index == 4:
                        assert any(s.name == "vault::read" for s in request.tools)
                    if index == 5:
                        assert not request.tools
                    if index not in (5,):
                        assert any(
                            isinstance(i, ToolResultItem) and "RECOVERED" in i.content
                            for i in request.items
                        )
                    assert request.compaction_turn_id is None
                    item = AssistantMessageItem("summary" if index == 5 else "done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        def create(thread_id=None):
            registry = ToolRegistry()
            registry.register(Read(calls))
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    api_mode="responses",
                    tool_search_mode="native" if native else "compatible",
                    model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                    deferred_tool_world_state=enabled,
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                registry=registry,
                model=Model(),
                thread_id=thread_id,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            before = await runtime._repository.load_items(runtime.thread_id)
        finally:
            await runtime.aclose()
        cold = create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4 and calls == ["vault::read"]
            assert any(isinstance(e, ContextCompacted) for e in [e async for e in cold.compact()])
            compacted_archive = await cold._repository.load_items(cold.thread_id)
            assert compacted_archive[: len(before)] == before
            # The old search result remains durable, but must not resurrect a
            # released schema when a fresh Runtime rebuilds its active window.
            await cold.aclose()
            cold = create(runtime.thread_id)
            events = [e async for e in cold.stream("read again")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold.thread_id)
            assert after[: len(compacted_archive)] == compacted_archive
            assert calls == ["vault::read", "vault::read"] and len(requests) == 8
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_frozen_prepare_search_and_namespace_deltas_then_last_removal(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Read([]),))
        newer = Read([])
        newer.spec = replace(Read.spec, namespace_description="new metadata")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    assert "vaultproof recovery" in catalogs(request.items)[0].content
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "vaultproof"}),
                        turn,
                        step,
                    )
                else:
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = make_runtime(tmp_path, registry, Model())
        # Fault injection targets prepare, after backend admission has published
        # the effective settings/builder (not a retained pre-admission method).
        await runtime._ensure_ready()
        build = runtime._graph._context_builder.build
        published = False

        async def publish_during_prepare(**kwargs):
            nonlocal published
            if not published:
                published = True
                registry.replace_owned(owner, (newer,))
            return await build(**kwargs)

        runtime._graph._context_builder.build = publish_during_prepare
        try:
            await complete(runtime, "search")
            raw = await runtime._repository.load_items(runtime.thread_id)
            found = next(i for i in raw if isinstance(i, ToolResultItem))
            assert found.discovered_tools == (Read.spec,)
            rows = catalogs(raw)
            assert len(rows) == 2
            assert "Added deferred tool namespaces:\n- vault: new metadata" in rows[-1].content
            assert "Deferred tool namespaces:\n- vault: new metadata" in rows[-1].snapshot_content
            await complete(runtime, "unchanged")
            assert catalogs(await runtime._repository.load_items(runtime.thread_id)) == rows
            registry.replace_owned(owner, ())
            await complete(runtime, "removed")
            removed = catalogs(requests[-1].items)[-1]
            assert "Removed deferred tool namespaces:\n- vault: new metadata" in removed.content
            assert "No deferred tool namespaces remain." in removed.content
            assert all(s.name != "tool_search" for s in requests[-1].tools)
            registry.replace_owned(owner, (newer,))
            await complete(runtime, "restored")
            assert catalogs(requests[-1].items)[-1].content.startswith(
                "<tools>\nDeferred tool namespaces:"
            )
            assert raw == (await runtime._repository.load_items(runtime.thread_id))[: len(raw)]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["prepare", "model"])
def test_steering_keeps_the_current_step_catalog_until_next_prepare(tmp_path, phase):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Read([]),))
        newer = Read([])
        newer.spec = replace(Read.spec, namespace_description="new metadata")
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if phase == "model" and len(requests) == 1:
                    yield ModelTextDelta("waiting")
                    await release.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = make_runtime(tmp_path, registry, Model())
        await runtime._ensure_ready()
        build = runtime._graph._context_builder.build
        first = True

        async def held_build(**kwargs):
            nonlocal first
            if phase == "prepare" and first:
                first = False
                entered.set()
                await release.wait()
            return await build(**kwargs)

        runtime._graph._context_builder.build = held_build

        async def consume():
            events = []
            async for event in runtime.stream("original", realtime=True):
                events.append(event)
                if isinstance(event, AssistantTextDelta) and event.delta == "waiting":
                    registry.replace_owned(owner, (newer,))
                    await runtime.steer("steered")
                    release.set()
            return events

        task = asyncio.create_task(consume())
        try:
            if phase == "prepare":
                await asyncio.wait_for(entered.wait(), 5)
                registry.replace_owned(owner, (newer,))
                await runtime.steer("steered")
                release.set()
            events = await asyncio.wait_for(task, 10)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            rows = catalogs(await runtime._repository.load_items(runtime.thread_id))
            assert len(rows) == 2
            assert decode_snapshot(rows[0].snapshot_state) == {"vault": "vaultproof recovery"}
            assert rows[-1].content.startswith("<tools>\nAdded deferred tool namespaces:")
            assert decode_snapshot(rows[-1].snapshot_state) == {"vault": "new metadata"}
            assert len(requests) == 2
            assert decode_snapshot(catalogs(requests[0].items)[-1].snapshot_state) == {
                "vault": "vaultproof recovery"
            }
            assert decode_snapshot(catalogs(requests[1].items)[-1].snapshot_state) == {
                "vault": "new metadata"
            }
            assert all("No deferred" not in row.content for row in rows)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelled_context_build_does_not_publish_a_partial_namespace_snapshot(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Read([]),))
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = make_runtime(tmp_path, registry, Model())
        await runtime._ensure_ready()
        build = runtime._graph._context_builder.build

        async def held_build(**kwargs):
            snapshot = await build(**kwargs)
            entered.set()
            await release.wait()
            return snapshot

        runtime._graph._context_builder.build = held_build
        task = asyncio.create_task(complete(runtime, "cancelled"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not requests
            assert not catalogs(await runtime._repository.load_items(runtime.thread_id))
            newer = Read([])
            newer.spec = replace(Read.spec, namespace_description="after cancellation")
            registry.replace_owned(owner, (newer,))
            release.set()
            await complete(runtime, "retry")
            assert decode_snapshot(catalogs(requests[-1].items)[0].snapshot_state) == {
                "vault": "after cancellation"
            }
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def catalogs(items):
    return [i for i in items if isinstance(i, ContextItem) and i.key == KEY and i.content]


def make_runtime(tmp_path, registry, model, **settings):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            deferred_tool_world_state=True,
            **settings,
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        registry=registry,
        model=model,
    )


async def complete(runtime, text):
    events = [e async for e in runtime.stream(text)]
    assert isinstance(events[-1], TurnCompleted), events[-1]


@pytest.mark.parametrize("exposure", [ToolExposure.DIRECT, ToolExposure.DEFERRED])
def test_search_disabled_keeps_existing_exposure_without_namespace_hints(tmp_path, exposure):
    async def scenario():
        registry = ToolRegistry()
        tool = Read([])
        tool.spec = replace(Read.spec, exposure=exposure)
        registry.register(tool)

        class Model:
            async def stream(self, request):
                assert not catalogs(request.items)
                assert (
                    any(s.name == "vault::read" for s in request.tools) == exposure.is_model_visible
                )
                assert all(s.name != "tool_search" for s in request.tools)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = make_runtime(tmp_path, registry, Model(), tool_search_mode="disabled")
        try:
            await complete(runtime, "direct")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
