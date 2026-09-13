"""Host shell approval Cancel is Turn control flow, unlike a declined command."""

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from corki.cli.application import CorkiApplication
from corki.code_mode.service import CodeModeService
from corki.config import CorkiPaths, CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ToolCallStarted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.fixture
def compiler():
    value = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if not value or sys.platform != "darwin":
        pytest.skip("requires explicit native compiler and macOS")
    return Path(value)


class ApprovalModel:
    def __init__(self, mode, command, count=1, *, settle=False, patch=None):
        self.mode, self.command, self.count = mode, command, count
        self.settle = settle
        self.patch = patch
        self.requests, self.turns = [], set()

    async def stream(self, request):
        self.requests.append(request)
        turn = request.items[-1].turn_id
        if turn in self.turns:
            yield ModelCompleted(())
            return
        self.turns.add(turn)
        arguments = {
            "cmd": self.command,
            "login": False,
            "sandbox_permissions": "require_escalated",
        }
        tool_name = "exec_command"
        if self.patch is not None:
            tool_name, arguments = "apply_patch", {"patch": self.patch}
        if self.mode == "direct":
            calls = [ToolCall(new_tool_call_id(), tool_name, arguments) for _ in range(self.count)]
        else:
            source = (
                "try {await Promise."
                + ("allSettled" if self.settle else "all")
                + "(["
                + ",".join(
                    "tools." + tool_name + "(" + json.dumps(arguments) + ")"
                    for _ in range(self.count)
                )
                + "]);} catch(e) {text(String(e));} store('after_review',true);"
            )
            calls = [
                ToolCall(
                    new_tool_call_id(), "exec", None, input_kind="freeform", raw_arguments=source
                )
            ]
        step = new_step_id()
        yield ModelCompleted(tuple(ToolCallItem(call, turn, step) for call in calls))

    async def aclose(self):
        pass


def create_runtime(tmp_path, compiler, model, *, thread_id=None, queue_size=256):
    if model.mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=model.mode,
            event_queue_size=queue_size,
            execution_permissions=ExecutionPermissions(
                compiler, tmp_path, '{"type":"read-only"}', approval_policy_json='"on-request"'
            ),
        ),
        home_path=tmp_path / "home",
        database_path=tmp_path / "state.db",
        model=model,
        thread_id=thread_id,
    )


async def observe(runtime, message):
    events = []
    try:
        async for event in runtime.stream(message):
            events.append(event)
    except asyncio.CancelledError:
        # The Python stream reports TurnCancelled then raises cancellation. Do
        # not hide the test timeout or any unrelated caller cancellation.
        if asyncio.current_task().cancelling():
            raise
        assert events and isinstance(events[-1], TurnCancelled)
    return events


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("host", ["api", "cli"])
@pytest.mark.parametrize("action", ["cancel", "decline"])
def test_host_cancel_ends_turn_while_decline_can_continue(tmp_path, compiler, mode, host, action):
    async def scenario():
        target = tmp_path / "must-not-exist"
        model = ApprovalModel(mode, "mkdir " + shlex.quote(str(target)))
        runtime = create_runtime(tmp_path, compiler, model)
        prompts = []

        async def respond(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, action)

        class UI:
            async def read_elicitation(self, request):
                prompts.append(request)
                return action, None

        if host == "api":
            runtime.set_execution_approval_handler(respond)
        else:
            CorkiApplication(
                CorkiSettings(working_directory=tmp_path),
                CorkiPaths.from_home(tmp_path / "home"),
                runtime,
                UI(),
            )
        try:
            async with asyncio.timeout(10):
                events = await observe(runtime, "review command")
            terminal = [
                event
                for event in events
                if isinstance(event, (TurnCancelled, TurnCompleted, TurnFailed))
            ]
            assert len(terminal) == 1 and isinstance(
                terminal[0], TurnCancelled if action == "cancel" else TurnCompleted
            )
            assert len(prompts) == 1 and not target.exists()
            assert len(model.requests) == (1 if action == "cancel" else 2)
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            assert not runtime._process_manager.approvals.router._pending
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            if mode == "code_mode_only":
                assert ("after_review" in runtime._code_mode.stored) is (action == "decline")
                assert not runtime._code_mode.cells
                assert all(task.done() for task in runtime._code_mode.calls)
            if action == "decline":
                output = [
                    item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
                ][-1]
                assert "execution approval rejected" in output.content
            with pytest.raises(ValueError, match="Unknown"):
                runtime.respond_execution_approval(prompts[0].request_id, "cancel")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_cancel_dismisses_parallel_reviews_and_stale_tokens_cannot_cancel_next_turn(
    tmp_path, compiler, mode
):
    async def scenario():
        target = tmp_path / "after-cancel"
        model = ApprovalModel(mode, "mkdir " + shlex.quote(str(target)), count=2)
        runtime = create_runtime(tmp_path, compiler, model)
        prompts, dismissed = [], set()

        async def respond(request):
            prompts.append(request)
            try:
                if len(prompts) == 2:
                    runtime.respond_execution_approval(request.request_id, "cancel")
                else:
                    await asyncio.Event().wait()
            finally:
                dismissed.add(request.request_id)

        runtime.set_execution_approval_handler(respond)
        try:
            async with asyncio.timeout(10):
                events = await observe(runtime, "two pending reviews")
            assert isinstance(events[-1], TurnCancelled)
            assert len(prompts) == 2 and dismissed == {p.request_id for p in prompts}
            assert len(model.requests) == 1 and not target.exists()
            assert not runtime._process_manager._sessions and not runtime._process_manager._starting
            assert not runtime._process_manager.approvals.router._pending
            if mode == "code_mode_only":
                assert "after_review" not in runtime._code_mode.stored

            async def approve_next(request):
                for stale in [p.request_id for p in prompts] + ["forged-token"]:
                    with pytest.raises(ValueError, match="Unknown"):
                        runtime.respond_execution_approval(stale, "cancel")
                    assert not runtime._active_run.cancel_requested
                with pytest.raises(ValueError, match="Unknown"):
                    runtime.respond_mcp_elicitation("local-shell", request.request_id, "cancel")
                assert not runtime._active_run.cancel_requested
                runtime.respond_execution_approval(request.request_id, "accept")
                with pytest.raises(ValueError, match="completed"):
                    runtime.respond_execution_approval(request.request_id, "cancel")
                assert not runtime._active_run.cancel_requested

            model.count = 1
            runtime.set_execution_approval_handler(approve_next)
            async with asyncio.timeout(10):
                events = [event async for event in runtime.stream("fresh authorized command")]
            assert isinstance(events[-1], TurnCompleted)
            assert target.is_dir() and len(model.requests) == 3
            history = model.requests[1].items
            calls = {item.call.id for item in history if isinstance(item, ToolCallItem)}
            results = {item.call_id for item in history if isinstance(item, ToolResultItem)}
            assert calls and calls == results
            assert all(item.is_error for item in history if isinstance(item, ToolResultItem))
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_approval_cancel_cold_resume_does_not_replay_pending_command(tmp_path, compiler, mode):
    async def scenario():
        target = tmp_path / "never-replayed"
        command = "mkdir " + shlex.quote(str(target))
        model = ApprovalModel(mode, command)
        runtime = create_runtime(tmp_path, compiler, model)
        thread_id = runtime.thread_id

        async def respond(request):
            runtime.respond_execution_approval(request.request_id, "cancel")

        runtime.set_execution_approval_handler(respond)
        try:
            async with asyncio.timeout(10):
                events = await observe(runtime, "cancel before spawning")
            assert isinstance(events[-1], TurnCancelled) and len(model.requests) == 1
        finally:
            await runtime.aclose()
        restored_model = ApprovalModel(mode, command)
        restored = create_runtime(tmp_path, compiler, restored_model, thread_id=thread_id)
        try:
            async with asyncio.timeout(10):
                assert [event async for event in restored.resume_pending()] == []
            assert not restored_model.requests and not target.exists()
            assert await restored._repository.latest_running_turn(thread_id) is None
        finally:
            await restored.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("queue_size", [1, 256])
def test_host_approval_cancel_finishes_cleanup_with_paused_event_consumer(
    tmp_path, compiler, queue_size
):
    async def scenario():
        target = tmp_path / "paused-consumer-fixture"
        model = ApprovalModel("direct", "mkdir " + shlex.quote(str(target)))
        runtime = create_runtime(tmp_path, compiler, model, queue_size=queue_size)
        entered, paused, released, dismissed = (asyncio.Event() for _ in range(4))
        prompts, events = [], []

        async def handler(request):
            prompts.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                dismissed.set()

        runtime.set_execution_approval_handler(handler)

        async def consume():
            try:
                async for event in runtime.stream("pause display during review"):
                    events.append(event)
                    if isinstance(event, ToolCallStarted):
                        paused.set()
                        await released.wait()
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                assert isinstance(events[-1], TurnCancelled)

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(10):
                await paused.wait()
                await entered.wait()
                run = runtime._active_run
                runtime.respond_execution_approval(prompts[0].request_id, "cancel")
                await run.done.wait()
                assert isinstance(run.result(), TurnCancelled) and not consumer.done()
                assert dismissed.is_set() and not runtime._process_manager.approvals.router._pending
                assert not runtime._process_manager._starting and not target.exists()
                assert len(model.requests) == 1
                released.set()
                await consumer
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
        finally:
            released.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
