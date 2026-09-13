"""Tool observations, fatal dispatch failures and per-server MCP cancellation."""

import asyncio
from dataclasses import replace

import pytest

import corki.tools as tool_package
from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import ToolCallCompleted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec, ToolStateUpdate
from corki.tools import ToolRegistry


class ObservationModel:
    def __init__(self, names, check=lambda result: None):
        self.names, self.check, self.requests = names, check, []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            yield ModelCompleted(
                tuple(
                    ToolCallItem(ToolCall(ToolCallId(f"call-{index}"), name, {}), turn, step)
                    for index, name in enumerate(self.names)
                )
            )
        else:
            result = next(
                item for item in reversed(request.items) if isinstance(item, ToolResultItem)
            )
            assert result.is_error is True
            self.check(result)
            yield ModelCompleted((AssistantMessageItem("handled the observation", turn, step),))

    async def aclose(self):
        pass


def _runtime(tmp_path, registry, model):
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
        database_path=tmp_path / "sessions.db",
        registry=registry,
        model=model,
    )


@pytest.mark.parametrize("failure", ["unknown", "invalid_json", "handler"])
def test_dispatch_error_reaches_next_model_step_without_extra_execution(tmp_path, failure):
    async def scenario():
        effects = []

        class Tool:
            spec = ToolSpec("probe", "failure boundary", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                raise ValueError("handler fixture failed")

        class Model(ObservationModel):
            async def stream(self, request):
                if not self.requests:
                    self.requests.append(request)
                    call = ToolCall(
                        ToolCallId("bad-call"),
                        "missing" if failure == "unknown" else "probe",
                        None if failure == "invalid_json" else {},
                        raw_arguments="{" if failure == "invalid_json" else None,
                        parse_error="unterminated object" if failure == "invalid_json" else None,
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, request.items[-1].turn_id, new_step_id()),)
                    )
                    return
                async for event in super().stream(request):
                    yield event

        registry = ToolRegistry()
        registry.register(Tool())
        model = Model(())
        runtime = _runtime(tmp_path, registry, model)
        try:
            events = [event async for event in runtime.stream("observe failure")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 2
            assert effects == (["bad-call"] if failure == "handler" else [])
            results = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, ToolResultItem)
            ]
            assert len(results) == 1 and results[0].is_error
            expected = {
                "unknown": "not advertised",
                "invalid_json": "invalid JSON",
                "handler": "handler fixture failed",
            }[failure]
            assert expected in results[0].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", None),
        ("display_content", {}),
        ("is_error", "false"),
        ("attachments", (object(),)),
        ("state_update", None),
        ("state_update", ToolStateUpdate(plan=({"step": "bad", "status": "INVALID"},))),
        ("content", "broken unicode \ud800"),
        ("content", lambda: "x" * 32_000_001),
        ("state_update", lambda: ToolStateUpdate(new_context_requested="yes")),
        ("state_update", lambda: ToolStateUpdate(plan_explanation=42)),
        ("mcp_result_json", "not json"),
        ("mcp_error", {"error": "not a string"}),
        ("patch_delta_json", "not json"),
        ("call_id", "unrelated-call"),
        ("tool_name", "unrelated-tool"),
        ("mcp_result_json", '{"value":NaN}'),
        ("mcp_result_json", '{"value":Infinity}'),
        ("raw_result", {"content": "not a ToolResult"}),
        ("raw_result", None),
    ],
)
def test_bad_result_becomes_durable_observation_and_model_can_continue(tmp_path, field, value):
    async def scenario():
        class Tool:
            spec = ToolSpec("broken", "Broken result fixture", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                if field == "raw_result":
                    return value
                return replace(
                    ToolResult(call.id, call.name, "ok"),
                    **{field: value() if callable(value) else value},
                )

        tool, registry = Tool(), ToolRegistry()
        registry.register(tool)
        model = ObservationModel((tool.spec.name,))
        runtime = _runtime(tmp_path, registry, model)
        try:
            events = [event async for event in runtime.stream("use the tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 2 and tool.calls == 1
            items = await runtime._repository.load_items(runtime.thread_id)
            results = [item for item in items if isinstance(item, ToolResultItem)]
            assert len(results) == 1 and results[0].is_error
            assert results[0].call_id == "call-0"
            assert results[0].tool_name == tool.spec.name
            assert not results[0].attachments and results[0].state_update.plan is None
            assert results[0].state_update.new_context_requested is False
            completed = [event for event in events if isinstance(event, ToolCallCompleted)]
            assert len(completed) == 1 and completed[0].is_error
            assert completed[0].mcp_result_json is None
            assert completed[0].mcp_error is None
            assert completed[0].patch_delta_json is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["return", "error"])
@pytest.mark.parametrize("owner_cancel", [False, True])
def test_nested_masked_cancellation_preserves_owner_and_unknown_ledger(
    tmp_path, outcome, owner_cancel
):
    async def scenario():
        calls, requests, events = [], [], []
        started = asyncio.Event()

        class Tool:
            spec = ToolSpec("cancelled", "Cancellation fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                started.set()
                if not owner_cancel:
                    asyncio.current_task().cancel()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    if outcome == "error":
                        raise ValueError("cleanup masked cancellation") from None
                    return ToolResult(call.id, call.name, "false success")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    call = ToolCall(
                        ToolCallId("cell"),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=(
                            "try { text(await tools.cancelled({})); } "
                            "catch (error) { text(String(error)); }"
                        ),
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    assert not owner_cancel
                    result = next(
                        item for item in reversed(request.items) if isinstance(item, ToolResultItem)
                    )
                    assert "nested tool call cancelled" in result.content
                    assert "false success" not in result.content
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, plugins_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "home",
            registry=registry,
            model=Model(),
        )

        async def consume():
            async for event in runtime.stream("run nested"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 2)
            if owner_cancel:
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCancelled if owner_cancel else TurnCompleted)
            assert len(requests) == (1 if owner_cancel else 2)
            assert len(calls) == 1
            assert not any(
                isinstance(event, ToolCallCompleted) and event.tool_call_id == calls[0].id
                for event in events
            )
            turn = requests[0].items[-1].turn_id
            result = await runtime._repository.claim_tool_call(runtime.thread_id, turn, calls[0])
            assert result is not None and result.is_error and result.dispatch_error
            assert "unknown" in result.content and "not repeated" in result.content
            assert len(calls) == 1
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_explicit_fatal_error_stops_turn_and_joins_parallel_sibling(tmp_path):
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()

        class Fatal:
            spec = ToolSpec(
                "fatal", "Fatal fixture", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL
            )

            async def execute(self, call, context):
                await started.wait()
                raise getattr(tool_package, "FatalToolError", RuntimeError)(
                    "execution environment lost"
                )

        class Sibling:
            spec = ToolSpec(
                "sibling", "Wait fixture", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL
            )

            async def execute(self, call, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        registry = ToolRegistry()
        registry.register(Fatal())
        registry.register(Sibling())
        model = ObservationModel(("fatal", "sibling"))
        runtime = _runtime(tmp_path, registry, model)
        try:
            async with asyncio.timeout(1):
                events = [event async for event in runtime.stream("run both")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert events[-1].error_kind == "tool" and not events[-1].retryable
            assert closed.is_set()
            assert len(model.requests) == 1
            assert (
                sum(
                    isinstance(event, (TurnCompleted, TurnFailed, TurnCancelled))
                    for event in events
                )
                == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_mcp_timeout_is_observation_but_user_cancel_is_control_flow(tmp_path, cancel):
    async def scenario():
        class Client(MCPClient):
            calls = 0

            def __init__(self):
                super().__init__(
                    MCPServerSettings(
                        name="fixture",
                        transport="stdio",
                        command="unused",
                        timeout_seconds=10 if cancel else 0.02,
                    )
                )
                self.started, self.closed = asyncio.Event(), asyncio.Event()

            async def start(self):
                pass

            async def _exchange(self, message):
                self.calls += 1
                self.started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.closed.set()

            async def _send_notification(self, message):
                pass

            async def aclose(self):
                pass

        client, registry = Client(), ToolRegistry()
        tool = MCPTool("fixture", {"name": "wait", "inputSchema": {"type": "object"}}, client)
        registry.register(tool)

        def check(result):
            assert "timed out" in result.content and "unknown" in result.content

        model = ObservationModel((tool.spec.name,), check)
        runtime = _runtime(tmp_path, registry, model)
        events = []

        async def consume():
            async for event in runtime.stream("call remote tool"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(client.started.wait(), 1)
            if cancel:
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            else:
                await asyncio.wait_for(task, 1)
            assert isinstance(events[-1], TurnCancelled if cancel else TurnCompleted), events[-1]
            assert client.closed.is_set() and client.calls == 1
            assert len(model.requests) == (1 if cancel else 2)
        finally:
            await runtime.aclose()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["return", "error"])
@pytest.mark.parametrize("stage", ["handler", "media"])
def test_execution_cannot_replace_pending_cancellation_with_result(
    tmp_path, monkeypatch, outcome, stage
):
    async def scenario():
        calls, prepared = [], []

        async def mask_cancellation(result):
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                if outcome == "error":
                    raise ValueError("cleanup masked cancellation") from None
                return result

        class Tool:
            spec = ToolSpec("cancelled", "Cancellation fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                result = ToolResult(call.id, call.name, "false success")
                return await mask_cancellation(result) if stage == "handler" else result

        if stage == "media":

            async def prepare_result(self, result):
                prepared.append(result)
                return await mask_cancellation(result)

            monkeypatch.setattr(
                "corki.media.preparation.MediaPreparation.prepare_result", prepare_result
            )

        registry = ToolRegistry()
        registry.register(Tool())
        model = ObservationModel(("cancelled",))
        runtime = _runtime(tmp_path, registry, model)
        events = []
        try:
            with pytest.raises(asyncio.CancelledError):
                async for event in runtime.stream("run"):
                    events.append(event)
            assert isinstance(events[-1], TurnCancelled)
            assert len(model.requests) == 1
            assert not any(isinstance(event, ToolCallCompleted) for event in events)
            assert len(calls) == 1
            assert len(prepared) == (1 if stage == "media" else 0)
            previous = await runtime._repository.claim_tool_call(
                runtime.thread_id, events[-1].turn_id, calls[0]
            )
            assert previous.is_error and previous.dispatch_error
            assert "unknown" in previous.content and "not repeated" in previous.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
