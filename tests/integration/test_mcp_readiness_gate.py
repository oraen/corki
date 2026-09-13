"""Codex waits for MCP readiness before taking the Step execution gate."""

import asyncio

import pytest
from test_mcp_lazy_startup import setup_runtime

from corki.mcp.client import MCPProtocolError
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools.errors import FatalToolError


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("finish", ["complete", "startup_failure", "cancel", "close"])
def test_unready_mcp_does_not_hold_execution_gate(
    tmp_path, monkeypatch, streamed, read_only, mode, finish
):
    async def scenario():
        local_started = asyncio.Event()
        requests, events = [], []

        class Local:
            spec = ToolSpec(
                "local",
                "already ready",
                {"type": "object"},
                concurrency=ToolConcurrency.EXCLUSIVE,
            )

            async def execute(self, call, context):
                local_started.set()
                return ToolResult(call.id, call.name, "local done")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    names = ["mcp__lazy::lookup", "local"]
                    if mode == "direct":
                        assert set(names) <= {spec.name for spec in request.tools}
                        calls = tuple(
                            ToolCallItem(ToolCall(ToolCallId(name), name, {}), turn, step)
                            for name in names
                        )
                    else:
                        calls = (
                            ToolCallItem(
                                ToolCall(
                                    ToolCallId("cell"),
                                    "exec",
                                    None,
                                    input_kind="freeform",
                                    raw_arguments="const remote = tools.mcp__lazy__lookup({}); "
                                    "text(await tools.local({})); text(await remote);",
                                ),
                                turn,
                                step,
                            ),
                        )
                    if streamed:
                        for item in calls:
                            yield ModelItemCompleted(item)
                    yield ModelCompleted(calls)
                else:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    if mode == "direct":
                        assert [item.tool_name for item in results] == [
                            "mcp__lazy::lookup",
                            "local",
                        ]
                        assert [item.is_error for item in results] == [
                            finish == "startup_failure",
                            False,
                        ]
                    else:
                        assert len(results) == 1 and results[0].tool_name == "exec"
                        assert not results[0].is_error and "local done" in results[0].content
                        assert (
                            "startup fixture failed"
                            if finish == "startup_failure"
                            else "live result"
                        ) in results[0].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime, clients, started, _ = setup_runtime(
            tmp_path,
            monkeypatch,
            Model(),
            definition={
                "name": "lookup",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": read_only},
            },
            tool_mode=mode,
        )
        runtime._registry.register(Local())

        async def consume():
            async for event in runtime.stream("call remote and local"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 2)
            # Remote startup cannot finish until this assertion has succeeded.
            # The local exclusive call therefore must pass the gate independently.
            await asyncio.wait_for(local_started.wait(), 1)
            assert not clients[0].calls and not task.done()
            if finish in {"cancel", "close"}:
                if finish == "close":
                    await asyncio.wait_for(runtime.aclose(), 3)
                else:
                    await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                assert not clients[0].calls and len(requests) == 1
                assert isinstance(events[-1], TurnCancelled)
            else:
                if finish == "startup_failure":

                    async def fail_list():
                        raise MCPProtocolError("startup fixture failed")

                    clients[0].list_tools = fail_list
                clients[0].release.set()
                await asyncio.wait_for(task, 3)
                assert isinstance(events[-1], TurnCompleted)
                assert clients[0].calls == ([] if finish == "startup_failure" else ["lookup"])
                assert len(requests) == 2
            assert (
                sum(
                    isinstance(event, (TurnCompleted, TurnCancelled, TurnFailed))
                    for event in events
                )
                == 1
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_cold_saved_mcp_call_skips_dispatch_readiness_and_rpc(tmp_path, monkeypatch, completed):
    from corki.core.graph import GraphRunContext
    from corki.core.runtime import _initial_state
    from corki.mcp import manager as manager_module
    from corki.protocol.ids import new_turn_id
    from corki.protocol.items import UserMessageItem
    from corki.sessions import TurnRecord, TurnStatus

    async def scenario():
        requests = []
        call = ToolCall(ToolCallId("saved-remote"), "mcp__lazy::lookup", {})

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    assert len(results) == 1 and results[0].call_id == call.id
                    assert results[0].is_error is (not completed)
                    assert ("saved result" if completed else "unknown") in results[0].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        model = Model()
        warm, warm_clients, _, _ = setup_runtime(tmp_path, monkeypatch, model)
        turn = new_turn_id()
        try:
            await warm._ensure_ready()
            user = UserMessageItem("do once", turn)
            await warm._repository.save_turn(
                TurnRecord(turn, warm.thread_id, TurnStatus.RUNNING, "do once")
            )
            await warm._compiled.ainvoke(
                _initial_state(warm.thread_id, turn, warm._settings, user),
                config=warm._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["execute_tools"],
            )
            assert not warm_clients and len(requests) == 1
            assert await warm._repository.claim_tool_call(warm.thread_id, turn, call) is None
            if completed:
                await warm._repository.complete_tool_call(
                    warm.thread_id, turn, ToolResult(call.id, call.name, "saved result")
                )
        finally:
            await warm.aclose()
        cold, cold_clients, _, _ = setup_runtime(
            tmp_path, monkeypatch, model, thread_id=warm.thread_id
        )
        factory = manager_module.create_client

        def ready_client(settings):
            client = factory(settings)
            client.release.set()
            return client

        async def unexpected_readiness(server):
            pytest.fail("saved call must skip dispatch readiness")

        # Existing cold snapshot rebinding prepares its saved server catalog.
        # This test distinguishes that from the new per-call readiness wait.
        monkeypatch.setattr(manager_module, "create_client", ready_client)
        monkeypatch.setattr(cold._mcp_manager, "wait_until_ready", unexpected_readiness)
        try:

            async def resume():
                return [event async for event in cold.resume_pending()]

            events = await asyncio.wait_for(resume(), 3)
            assert isinstance(events[-1], TurnCompleted)
            assert len(cold_clients) == 1 and not cold_clients[0].calls and len(requests) == 2
            history = await cold._repository.load_items(cold.thread_id)
            assert sum(isinstance(item, ToolCallItem) for item in history) == 1
            assert sum(isinstance(item, UserMessageItem) for item in history) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_fatal_exclusive_call_does_not_release_queued_side_effect(tmp_path, monkeypatch, streamed):
    async def scenario():
        called = []

        class Tool:
            def __init__(self, name):
                self.spec = ToolSpec(name, "fixture", {"type": "object"})

            async def execute(self, call, context):
                called.append(call.name)
                if call.name == "fatal":
                    raise FatalToolError("fatal fixture")
                return ToolResult(call.id, call.name, "must not execute")

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                calls = tuple(
                    ToolCallItem(ToolCall(ToolCallId(name), name, {}), turn, step)
                    for name in ("fatal", "effect")
                )
                if streamed:
                    for item in calls:
                        yield ModelItemCompleted(item)
                yield ModelCompleted(calls)

            async def aclose(self):
                pass

        runtime, clients, _, _ = setup_runtime(tmp_path, monkeypatch, Model())
        runtime._registry.register(Tool("fatal"))
        runtime._registry.register(Tool("effect"))
        try:
            events = [event async for event in runtime.stream("fail before effect")]
            assert isinstance(events[-1], TurnFailed)
            assert called == ["fatal"]
            assert not clients
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
