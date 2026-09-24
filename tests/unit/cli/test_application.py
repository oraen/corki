import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import (
    AssistantMessageInterrupted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    ContextCompacted,
    ContextCompactionStarted,
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    ModelRetryScheduled,
    PlanUpdated,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    WarningEvent,
)
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.realtime import RealtimeTurnClosedError


@dataclass
class FakeUI:
    messages: Iterator[str]
    events: list[tuple[str, str]] = field(default_factory=list)

    def show_welcome(self) -> None:
        self.events.append(("welcome", ""))

    async def read_message(self) -> str:
        try:
            return next(self.messages)
        except StopIteration as error:
            raise EOFError from error

    def show_user_message(self, message: str) -> None:
        self.events.append(("user", message))

    def show_assistant_message(self, message: str, *, is_error: bool = False) -> None:
        self.events.append(("error" if is_error else "assistant", message))

    def begin_assistant_message(self) -> None:
        self.events.append(("assistant_start", ""))

    def append_assistant_delta(self, delta: str) -> None:
        self.events.append(("assistant_delta", delta))

    def end_assistant_message(self) -> None:
        self.events.append(("assistant_end", ""))

    def begin_reasoning(self) -> None:
        self.events.append(("reasoning_start", ""))

    def append_reasoning_delta(self, delta: str) -> None:
        self.events.append(("reasoning_delta", delta))

    def end_reasoning(self) -> None:
        self.events.append(("reasoning_end", ""))

    def show_tool_started(self, name: str, arguments_preview: str) -> None:
        self.events.append(("tool_started", name))

    def show_tool_output(self, output: str) -> None:
        self.events.append(("tool_output", output))

    def show_tool_completed(self, name: str, *, is_error: bool) -> None:
        self.events.append(("tool_error" if is_error else "tool_completed", name))

    def show_plan(
        self, plan: tuple[dict[str, str], ...], *, explanation: str | None = None
    ) -> None:
        self.events.append(("plan", str(len(plan))))

    def show_notice(self, message: str) -> None:
        self.events.append(("notice", message))

    def clear(self) -> None:
        self.events.append(("clear", ""))

    def show_goodbye(self) -> None:
        self.events.append(("goodbye", ""))


@dataclass
class FakeRuntime:
    received: list[str] = field(default_factory=list)

    async def stream(self, message: str, *, realtime: bool = False) -> AsyncIterator[object]:
        self.received.append(message)
        thread_id = new_thread_id()
        turn_id = new_turn_id()
        yield AssistantReasoningDelta(thread_id, turn_id, "thinking")
        yield AssistantTextDelta(thread_id, turn_id, f"reply: {message}")
        yield TurnCompleted(thread_id, turn_id, f"reply: {message}")

    async def resume_pending(self) -> AsyncIterator[object]:
        if False:
            yield None

    async def aclose(self) -> None:
        return None


def test_history_load_failure_exits_cleanly_without_accepting_a_new_turn(tmp_path):
    class Runtime(FakeRuntime):
        closed = False

        async def load_display_snapshot(self):
            raise OSError("PRIVATE_STORAGE_DETAIL")

        async def resume_pending(self):
            raise AssertionError("pending work must not resume without history")
            yield

        async def aclose(self):
            self.closed = True

    class UI(FakeUI):
        def replay_history(self, items):
            raise AssertionError("history did not load")

        def show_notice(self, message):
            self.events.append(("notice", message))

        async def read_message(self):
            raise AssertionError("composer must not accept a new turn")

    runtime = Runtime()
    ui = UI(iter(()))
    app = CorkiApplication(
        CorkiSettings(working_directory=tmp_path),
        CorkiPaths.from_home(tmp_path / ".corki"),
        runtime,
        ui,
    )
    assert asyncio.run(app.run()) == 1
    assert runtime.closed
    assert ui.events[0] == ("welcome", "")
    assert ui.events[-1] == ("goodbye", "")
    notice = next(text for kind, text in ui.events if kind == "notice")
    assert "Could not load conversation history" in notice
    assert "No new turn was started" in notice
    assert "PRIVATE_STORAGE_DETAIL" not in notice


@pytest.mark.parametrize("terminal_event", [False, True])
def test_cancellation_notice_once_per_operation_with_or_without_terminal_event(
    tmp_path, terminal_event
):
    class Runtime(FakeRuntime):
        async def stream(self, message, *, realtime=False):
            self.received.append(message)
            if terminal_event:
                yield TurnCancelled(new_thread_id(), new_turn_id())
            raise asyncio.CancelledError

    runtime = Runtime()
    ui = FakeUI(iter(("first", "second")))
    app = CorkiApplication(
        CorkiSettings(working_directory=tmp_path, realtime_enabled=False),
        CorkiPaths.from_home(tmp_path / ".corki"),
        runtime,
        ui,
    )
    assert asyncio.run(app.run()) == 0
    assert runtime.received == ["first", "second"]
    assert [entry for entry in ui.events if entry == ("notice", "Turn interrupted.")] == [
        ("notice", "Turn interrupted."),
        ("notice", "Turn interrupted."),
    ]


def test_hook_completion_is_not_a_tool_or_duplicate_warning(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        ui = FakeUI(iter(()))
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            ui,
        )

        async def events():
            yield AssistantTextDelta(thread, turn, "answer")
            yield HookStarted(thread, turn, HookRunSummary("quiet", "key", "Stop", "running"))
            yield HookCompleted(thread, turn, HookRunSummary("quiet", "key", "Stop", "completed"))
            # Restored completion is valid even without a preceding start event.
            yield HookCompleted(
                thread,
                turn,
                HookRunSummary(
                    "blocked",
                    "key2",
                    "Stop",
                    "blocked",
                    entries=(
                        HookOutputEntry("warning", "notice"),
                        HookOutputEntry("feedback", "check"),
                        HookOutputEntry("context", "private model context"),
                    ),
                ),
            )
            yield TurnCompleted(thread, turn, "answer")

        await app._consume_events(events())
        notices = [text for kind, text in ui.events if kind == "notice"]
        assert notices == ["• Blocked by hook\n  └ notice\n  └ check"]
        assert not any(kind.startswith("tool_") for kind, _ in ui.events)
        assert ui.events.index(("assistant_end", "")) < ui.events.index(("notice", notices[0]))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "termination", ["eof", "error", "cancel", "failed", "cancelled", "completed"]
)
@pytest.mark.parametrize("renderer_fails", [False, True])
def test_hook_timer_cleared_on_every_event_stream_exit(tmp_path, termination, renderer_fails):
    async def scenario():
        ui = TerminalUI(
            CorkiSettings(tmp_path), tmp_path / "history", console=Console(file=StringIO())
        )
        app = CorkiApplication(
            CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path), FakeRuntime(), ui
        )
        thread, turn = new_thread_id(), new_turn_id()
        handles = []

        if renderer_fails:

            def fail_notice(message):
                raise RuntimeError("renderer broke")

            ui.show_notice = fail_notice

        async def events():
            yield HookStarted(thread, turn, HookRunSummary("active", "key", "Stop", "running"))
            handles.append(ui._hook_timer)
            if renderer_fails:
                yield WarningEvent(thread, turn, "notice")
            if termination == "error":
                raise RuntimeError("stream broke")
            if termination == "cancel":
                raise asyncio.CancelledError
            if termination == "completed":
                yield TurnCompleted(thread, turn, "done")
            elif termination == "failed":
                yield TurnFailed(thread, turn, "failed")
            elif termination == "cancelled":
                yield TurnCancelled(thread, turn)
            if termination in {"completed", "failed", "cancelled"}:
                # Terminal rendering must clear immediately, not wait for iterator EOF.
                assert handles[0].cancelled() and ui._hook_timer is None

        if renderer_fails or termination in {"error", "cancel"}:
            error = (
                RuntimeError if renderer_fails or termination == "error" else asyncio.CancelledError
            )
            with pytest.raises(error):
                await app._consume_events(events())
        else:
            await app._consume_events(events())
        assert len(handles) == 1 and handles[0].cancelled()
        assert ui._hook_timer is None
        assert ui._hook_activity.summary is ui._hook_activity.deadline is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "termination", ["eof", "error", "cancel", "failed", "cancelled", "completed"]
)
@pytest.mark.parametrize("replayed", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_unfinished_tool_display_is_closed_by_call_identity(
    tmp_path, termination, replayed, streaming
):
    async def scenario():
        ui = FakeUI(iter(()))
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path),
            FakeRuntime(),
            ui,
        )
        thread, turn = new_thread_id(), new_turn_id()
        first, second = new_tool_call_id(), new_tool_call_id()
        if replayed:
            app._replayed_calls = {first}

        async def events():
            if streaming:
                yield AssistantTextDelta(thread, turn, "unfinished")
            yield ToolCallStarted(thread, turn, first, "same_tool", "one")
            yield ToolCallStarted(thread, turn, second, "same_tool", "two")
            yield ToolOutputDelta(thread, turn, second, "output")
            yield PlanUpdated(thread, turn, (), tool_call_id=second)
            yield ToolCallCompleted(thread, turn, second, "same_tool", False)
            if streaming:
                assert not any(kind.startswith("tool_") or kind == "plan" for kind, _ in ui.events)
            if termination == "error":
                raise ValueError("stream failure")
            if termination == "cancel":
                raise asyncio.CancelledError
            if termination == "failed":
                yield TurnFailed(thread, turn, "turn failure")
            elif termination == "cancelled":
                yield TurnCancelled(thread, turn)
            elif termination == "completed":
                yield TurnCompleted(thread, turn, "answer")

        if termination in {"error", "cancel"}:
            with pytest.raises(ValueError if termination == "error" else asyncio.CancelledError):
                await app._consume_events(events())
        else:
            await app._consume_events(events())
        notice = ("notice", "same_tool interrupted; completion not confirmed.")
        assert ui.events.count(notice) == 1, ui.events
        assert ui.events.count(("tool_completed", "same_tool")) == 1
        assert ("tool_error", "same_tool") not in ui.events
        output = ui.events.index(("tool_output", "output"))
        plan = ui.events.index(("plan", "0"))
        completed = ui.events.index(("tool_completed", "same_tool"))
        assert output < plan < completed
        if streaming:
            assert ui.events.index(("assistant_end", "")) < output
        if termination == "failed":
            assert ui.events.index(notice) < ui.events.index(("error", "turn failure"))

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["assistant", "reasoning"])
@pytest.mark.parametrize("termination", ["eof", "error", "cancel"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_event_stream_exit_closes_partial_display(tmp_path, kind, termination, cleanup_fails):
    async def scenario():
        ui = FakeUI(iter(()))
        if cleanup_fails:

            def failed_close():
                ui.events.append((kind + "_end", ""))
                raise OSError("display cleanup failed")

            setattr(
                ui,
                "end_assistant_message" if kind == "assistant" else "end_reasoning",
                failed_close,
            )
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path),
            FakeRuntime(),
            ui,
        )
        closed = []

        async def events():
            try:
                event = AssistantTextDelta if kind == "assistant" else AssistantReasoningDelta
                yield event(new_thread_id(), new_turn_id(), "unfinished")
                if termination == "error":
                    raise ValueError("stream failed")
                if termination == "cancel":
                    raise asyncio.CancelledError
            finally:
                closed.append(True)

        if termination == "eof" and not cleanup_fails:
            await app._consume_events(events())
        else:
            error = {
                "error": ValueError,
                "cancel": asyncio.CancelledError,
                "eof": OSError,
            }[termination]
            with pytest.raises(error):
                await app._consume_events(events())
        assert closed == [True]
        assert ui.events == [
            (kind + "_start", ""),
            (kind + "_delta", "unfinished"),
            (kind + "_end", ""),
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("failed", [False, True])
def test_memory_reset_command_does_not_sample_or_fake_success(tmp_path, failed):
    class Runtime(FakeRuntime):
        resets = 0
        memory_reset_targets = (tmp_path / "custom-memory",)

        async def reset_memory(self):
            self.resets += 1
            if failed:
                raise RuntimeError("partial reset")
            return (tmp_path / "memories",)

    def messages():
        yield "/memory reset"
        yield "/memory reset confirm"
        yield "hello"
        raise EOFError

    async def scenario():
        runtime = Runtime()
        ui = FakeUI(messages())
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            runtime,
            ui,
        )
        assert await app.run() == 0
        assert runtime.resets == 1 and runtime.received == ["hello"]
        assert any(str(tmp_path / "custom-memory") in value for _, value in ui.events)
        assert any("Reset local memories in" in value for _, value in ui.events) is not failed
        assert (
            any(kind == "error" and "partial reset" in value for kind, value in ui.events) is failed
        )

    asyncio.run(scenario())


def test_realtime_memory_reset_reaches_actual_runtime_without_steering(tmp_path):
    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, new_step_id

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        root = tmp_path / "custom-memory"
        root.mkdir()
        (root / "old.md").write_text("old")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                entered.set()
                await release.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(FakeUI):
            reads = 0

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "work"
                if self.reads == 2:
                    await entered.wait()
                    return "/memory reset"
                if self.reads == 3:
                    assert (root / "old.md").exists()
                    return "/memory reset confirm"
                if self.reads == 4:
                    assert not list(root.iterdir())
                    release.set()
                    await asyncio.Event().wait()
                raise EOFError

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, realtime_enabled=True
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "actual-home",
            memory_root=root,
            model=Model(),
        )
        ui = UI(iter(()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "ui-home"), runtime, ui)
        try:
            assert await asyncio.wait_for(app.run(), 3) == 0
        finally:
            release.set()
            await runtime.aclose()
        assert len(requests) == 1
        assert any(kind == "notice" and str(root) in value for kind, value in ui.events)
        assert any("Reset local memories in" in value for _, value in ui.events)

    asyncio.run(scenario())


@pytest.mark.parametrize("failed", [False, True])
def test_memory_mode_command_reports_durable_result_without_sampling(tmp_path, failed):
    class Runtime(FakeRuntime):
        modes = []

        async def set_thread_memory_mode(self, mode):
            self.modes.append(mode)
            if failed:
                raise RuntimeError("metadata write failed")

    def messages():
        yield "/memory mode disabled"
        yield "hello"
        raise EOFError

    async def scenario():
        runtime, ui = Runtime(), FakeUI(messages())
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / "home"),
            runtime,
            ui,
        )
        assert await app.run() == 0
        assert runtime.modes == ["disabled"] and runtime.received == ["hello"]
        assert (
            any("Thread memory source mode: disabled" in value for _, value in ui.events)
            is not failed
        )
        assert (
            any(kind == "error" and "metadata write failed" in value for kind, value in ui.events)
            is failed
        )

    asyncio.run(scenario())


def test_realtime_memory_mode_updates_real_runtime_without_steering(tmp_path):
    import sqlite3

    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, new_step_id

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []
        database = tmp_path / "history.db"

        class Model:
            async def stream(self, request):
                requests.append(request)
                entered.set()
                await release.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(FakeUI):
            reads = 0

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "work"
                if self.reads == 2:
                    await entered.wait()
                    return "/memory mode disabled"
                if self.reads in (3, 4):
                    expected = "disabled" if self.reads == 3 else "enabled"
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT memory_mode FROM threads").fetchone() == (
                            expected,
                        )
                    if self.reads == 3:
                        return "/memory mode enabled"
                    release.set()
                    await asyncio.Event().wait()
                raise EOFError

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, realtime_enabled=True
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            home_path=tmp_path / "home",
            model=Model(),
        )
        ui = UI(iter(()))
        try:
            app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), runtime, ui)
            assert await asyncio.wait_for(app.run(), 3) == 0
        finally:
            release.set()
            await runtime.aclose()
        assert len(requests) == 1
        assert not any(kind == "error" for kind, _ in ui.events)
        assert sum("Thread memory source mode:" in value for _, value in ui.events) == 2

    asyncio.run(scenario())


def test_warning_is_a_notice_not_an_assistant_error(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        ui = FakeUI(iter(()))
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            ui,
        )

        async def events():
            yield WarningEvent(thread, turn, "Failed to load skill at /[red]/SKILL.md")
            yield TurnCompleted(thread, turn, "done")

        await app._consume_events(events())
        assert ui.events == [
            ("notice", "Warning: Failed to load skill at /[red]/SKILL.md"),
            ("assistant", "done"),
        ]

    asyncio.run(scenario())


def test_terminal_notice_does_not_interpret_error_text_as_markup():
    output = StringIO()
    # Rendering does not need an interactive prompt session or terminal input.
    ui = TerminalUI.__new__(TerminalUI)
    ui._console = Console(file=output, width=200, force_terminal=False)
    message = "Warning: Failed to load skill at /[red]/SKILL.md: [/broken]"
    ui.show_notice(message)
    assert message in output.getvalue()


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("renderer_cancelled", [False, True])
def test_repeated_cancel_joins_event_iterator_cleanup(tmp_path, cleanup_fails, renderer_cancelled):
    async def scenario():
        entered = asyncio.Event()
        released = asyncio.Event()
        closed = False

        class Events:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if renderer_cancelled:
                    raise asyncio.CancelledError
                raise StopAsyncIteration

            async def aclose(self):
                nonlocal closed
                entered.set()
                await released.wait()
                closed = True
                if cleanup_fails:
                    raise RuntimeError("late cleanup failure")

        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            FakeUI(iter(())),
        )
        task = asyncio.create_task(app._consume_events(Events()))
        await entered.wait()
        if not renderer_cancelled:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        await asyncio.sleep(0)
        try:
            assert not task.done()
        finally:
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [3, None])
@pytest.mark.parametrize("purpose", ["sampling", "compaction"])
@pytest.mark.parametrize(
    "error,shown",
    [
        ("network failure", "network failure"),
        ("\x1b[31m拒绝\r\n重试", "[31m拒绝 \n重试"),
        (" \t", ""),
        ("x" * 5000, "x" * 4000),
    ],
)
def test_retry_closes_partial_output_without_claiming_user_steering(
    tmp_path, limit, purpose, error, shown
):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        ui = FakeUI(iter(()))
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            ui,
        )

        async def events():
            yield AssistantTextDelta(thread, turn, "partial")
            yield AssistantMessageInterrupted(thread, turn, reason="retry")
            yield ModelRetryScheduled(thread, turn, 1, limit, 5, error, purpose=purpose)
            yield AssistantTextDelta(thread, turn, "recovered")
            yield TurnCompleted(thread, turn, "recovered")

        await app._consume_events(events())
        label = "Retrying compaction" if purpose == "compaction" else "Reconnecting"
        assert ui.events == [
            ("assistant_start", ""),
            ("assistant_delta", "partial"),
            ("assistant_end", ""),
            ("notice", "Response interrupted; retrying with updated history..."),
            (
                "notice",
                f"{label}... 1/{limit if limit is not None else '∞'} in 5s"
                + ("\nReason: " + shown if shown else ""),
            ),
            ("assistant_start", ""),
            ("assistant_delta", "recovered"),
            ("assistant_end", ""),
        ]

    asyncio.run(scenario())


def test_retry_before_any_visible_response_does_not_claim_it_was_interrupted(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        ui = FakeUI(iter(()))
        app = CorkiApplication(
            CorkiSettings(working_directory=tmp_path),
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            ui,
        )

        async def events():
            yield AssistantMessageInterrupted(thread, turn, reason="retry")
            yield ModelRetryScheduled(thread, turn, 1, 1, 0.2, "connection unavailable")
            yield TurnFailed(thread, turn, "connection unavailable")

        await app._consume_events(events())
        assert ("notice", "Response interrupted; retrying with updated history...") not in ui.events
        assert any("Reconnecting..." in text for kind, text in ui.events if kind == "notice")
        assert ui.events[-1] == ("error", "connection unavailable")

    asyncio.run(scenario())


def test_model_error_display_redacts_configured_key_and_bearer_token(tmp_path):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        ui = FakeUI(iter(()))
        settings = CorkiSettings(working_directory=tmp_path, api_key="FAKE_MODEL_SECRET_123")
        app = CorkiApplication(
            settings,
            CorkiPaths.from_home(tmp_path / ".corki"),
            FakeRuntime(),
            ui,
        )

        async def events():
            yield ModelRetryScheduled(
                thread,
                turn,
                1,
                1,
                1,
                "Authorization: Bearer FAKE_MODEL_SECRET_123; alternate Bearer OTHER_FAKE_TOKEN",
            )
            yield TurnFailed(thread, turn, "provider rejected FAKE_MODEL_SECRET_123")

        await app._consume_events(events())
        display = repr(ui.events)
        assert "FAKE_MODEL_SECRET_123" not in display
        assert "OTHER_FAKE_TOKEN" not in display
        assert "[redacted]" in display
        assert "Reconnecting..." in display
        assert "provider rejected" in display

        output = StringIO()
        terminal = TerminalUI(
            settings, tmp_path / "history", console=Console(file=output, width=100)
        )
        rendered_app = CorkiApplication(
            settings, CorkiPaths.from_home(tmp_path / ".corki"), FakeRuntime(), terminal
        )
        await rendered_app._consume_events(events())
        assert "FAKE_MODEL_SECRET_123" not in output.getvalue()
        assert "OTHER_FAKE_TOKEN" not in output.getvalue()
        assert "FAKE_MODEL_SECRET_123" not in terminal._transcript.render(100)

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["cancel", "fail", "retry", "raise"])
def test_interrupted_stream_keeps_received_tail_without_final_newline(tmp_path, outcome):
    async def scenario():
        thread, turn = new_thread_id(), new_turn_id()
        output = StringIO()
        settings = CorkiSettings(working_directory=tmp_path)
        ui = TerminalUI(settings, tmp_path / "history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / "home"), FakeRuntime(), ui)
        text = "已经收到的正文\n没有换行的尾部🙂"

        async def events():
            yield AssistantTextDelta(thread, turn, text)
            if outcome == "cancel":
                yield TurnCancelled(thread, turn)
            elif outcome == "fail":
                yield TurnFailed(thread, turn, "Provider failed")
            elif outcome == "raise":
                raise OSError("stream ended without a terminal event")
            else:
                yield AssistantMessageInterrupted(thread, turn, reason="retry")

        if outcome == "raise":
            with pytest.raises(OSError, match="without a terminal event"):
                await app._consume_events(events())
        else:
            await app._consume_events(events())
        assert output.getvalue().count("没有换行的尾部🙂") == 1
        assert ui._transcript.render(100).count("没有换行的尾部🙂") == 1
        assert not ui._assistant_pending and ui._table_source is None

    asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["failed", "cancelled"])
def test_tool_chunks_preserve_lines_and_keep_parallel_calls_separate(tmp_path, finish):
    async def scenario():
        output = StringIO()
        settings = CorkiSettings(tmp_path)
        ui = TerminalUI(settings, tmp_path / "history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), FakeRuntime(), ui)
        thread, turn = new_thread_id(), new_turn_id()
        first, second = new_tool_call_id(), new_tool_call_id()

        async def events():
            yield ToolCallStarted(thread, turn, first, "first", "")
            yield ToolCallStarted(thread, turn, second, "second", "")
            yield ToolOutputDelta(thread, turn, first, "hel")
            yield ToolOutputDelta(thread, turn, second, "世界\n")
            yield ToolOutputDelta(thread, turn, first, "lo\n\n")
            yield ToolOutputDelta(thread, turn, second, "尾部")
            yield ToolCallCompleted(thread, turn, first, "first", False)
            if finish == "failed":
                yield ToolCallCompleted(thread, turn, second, "second", True)
            else:
                yield TurnCancelled(thread, turn)

        await app._consume_events(events())
        text = output.getvalue()
        assert "hello\n\n" in text and "hel\n" not in text
        assert text.count("世界\n") == 1 and "世界\n\n" not in text
        assert text.count("尾部\n") == 1
        status = "second failed" if finish == "failed" else "second interrupted"
        assert text.index("尾部") < text.index(status)
        assert ui._transcript.render(100) == text

    asyncio.run(scenario())


@pytest.mark.parametrize("queued", [False, True])
def test_late_steering_submission_is_kept_for_next_turn(tmp_path: Path, queued) -> None:
    from corki.cli.input_owner import QueuedInput

    async def scenario():
        first_started = asyncio.Event()
        steering_attempted = asyncio.Event()
        second_finished = asyncio.Event()

        class ClosingRuntime(FakeRuntime):
            async def stream(self, message, *, realtime=False):
                self.received.append(message)
                thread, turn = new_thread_id(), new_turn_id()
                if len(self.received) == 1:
                    first_started.set()
                    await steering_attempted.wait()
                yield TurnCompleted(thread, turn, f"reply: {message}")
                if len(self.received) == 2 + int(queued):
                    second_finished.set()

            async def steer(self, message):
                assert message == "late input"
                steering_attempted.set()
                raise RealtimeTurnClosedError("turn closed before event delivery")

            async def cancel_active(self):
                pass

        class BoundaryUI(FakeUI):
            reads = 0

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "initial"
                if queued and self.reads == 2:
                    await first_started.wait()
                    return QueuedInput("already queued")
                if self.reads == 2 + int(queued):
                    await first_started.wait()
                    return "late input"
                await second_finished.wait()
                raise EOFError

        runtime, ui = ClosingRuntime(), BoundaryUI(iter(()))
        settings = CorkiSettings(working_directory=tmp_path, realtime_enabled=True)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), timeout=3) == 0
        assert runtime.received == [
            "initial",
            *(["already queued"] if queued else []),
            "late input",
        ]
        assert not any(kind == "error" for kind, _ in ui.events)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "command", ["/help", "/status", "/missing", "/clear", "/mcp refresh", "/copy"]
)
def test_local_commands_are_not_sent_as_live_model_input(tmp_path, command):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        steered = []
        refreshes = []

        class Runtime(FakeRuntime):
            async def stream(self, message, *, realtime=False):
                self.received.append(message)
                started.set()
                await release.wait()
                yield TurnCompleted(new_thread_id(), new_turn_id(), "done")

            async def steer(self, message):
                steered.append(message)

            def request_mcp_refresh(self):
                refreshes.append(True)

            async def cancel_active(self):
                release.set()

        class UI(FakeUI):
            reads = 0

            async def read_copy(self, request):
                self.events.append(("copy", ""))
                self.show_notice("Copied to clipboard.")

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "initial"
                await started.wait()
                if self.reads == 2:
                    return command
                raise EOFError

        runtime, ui = Runtime(), UI(iter(()))
        settings = CorkiSettings(working_directory=tmp_path, realtime_enabled=True)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert steered == []
        assert runtime.received == ["initial"]
        assert refreshes == ([True] if command == "/mcp refresh" else [])
        if command == "/clear":
            assert ("clear", "") not in ui.events
            assert any("unavailable" in text for _, text in ui.events)
        elif command == "/copy":
            assert ("copy", "") in ui.events
        else:
            assert ("notice", app._commands.dispatch(command).output) in ui.events
        assert any(kind == "notice" and text for kind, text in ui.events)

    asyncio.run(scenario())


def test_explicit_queue_is_fifo_and_does_not_steer_active_turn(tmp_path):
    from corki.cli.input_owner import QueuedInput

    async def scenario():
        started, release, all_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Runtime(FakeRuntime):
            steered = []

            async def stream(self, message, *, realtime=False):
                self.received.append(message)
                thread, turn = new_thread_id(), new_turn_id()
                if message == "initial":
                    started.set()
                    await release.wait()
                yield TurnCompleted(thread, turn, f"reply: {message}")
                if message == "second queued":
                    all_finished.set()

            async def steer(self, message):
                self.steered.append(message)
                assert self.received == ["initial"]
                release.set()

            async def cancel_active(self):
                pass

        class UI(FakeUI):
            reads = 0
            previews = []

            def set_pending_inputs(self, messages):
                self.previews.append(messages)

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "initial"
                await started.wait()
                if self.reads == 2:
                    return QueuedInput(" first queued ")
                if self.reads == 3:
                    return QueuedInput("second queued")
                if self.reads == 4:
                    return "steer current"
                await all_finished.wait()
                raise EOFError

        runtime, ui = Runtime(), UI(iter(()))
        settings = CorkiSettings(working_directory=tmp_path, realtime_enabled=True)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert runtime.received == ["initial", "first queued", "second queued"]
        assert runtime.steered == ["steer current"]
        assert ui.previews == [
            ("first queued",),
            ("first queued", "second queued"),
            ("second queued",),
            (),
        ]
        assert not any(kind == "error" for kind, _ in ui.events)

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["/stop", "keyboard", "eof"])
def test_stopping_turn_restores_queued_inputs_instead_of_autosending(tmp_path, stop):
    from corki.cli.input_owner import QueuedInput

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()

        class Runtime(FakeRuntime):
            async def stream(self, message, *, realtime=False):
                self.received.append(message)
                thread, turn = new_thread_id(), new_turn_id()
                if message == "initial":
                    started.set()
                    await cancelled.wait()
                    yield TurnCancelled(thread, turn)
                else:
                    yield TurnCompleted(thread, turn, "unexpected")

            async def cancel_active(self):
                cancelled.set()

        class UI(FakeUI):
            reads = 0
            restored = []

            def restore_queued_inputs(self, messages):
                self.restored.append(messages)

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "initial"
                await started.wait()
                if self.reads in (2, 3):
                    return QueuedInput(f"queued {self.reads}")
                if self.reads == 4:
                    if stop == "keyboard":
                        raise KeyboardInterrupt
                    if stop == "eof":
                        raise EOFError
                    return stop
                raise EOFError

        runtime, ui = Runtime(), UI(iter(()))
        app = CorkiApplication(CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert runtime.received == ["initial"]
        assert ui.restored == [("queued 2", "queued 3")]
        assert not app._pending_messages

    asyncio.run(scenario())


@pytest.mark.parametrize("restore_fails", [False, True])
def test_external_cancel_restores_after_reader_cleanup_and_preserves_cancel(
    tmp_path, restore_fails
):
    async def scenario():
        reading, stopped = asyncio.Event(), asyncio.Event()

        class Runtime(FakeRuntime):
            async def stream(self, message, *, realtime=False):
                await stopped.wait()
                yield TurnCancelled(new_thread_id(), new_turn_id())

            async def cancel_active(self):
                stopped.set()

        class UI(FakeUI):
            reader_closed = False
            restored = []

            async def read_message(self):
                reading.set()
                try:
                    await asyncio.Future()
                finally:
                    self.reader_closed = True

            def restore_queued_inputs(self, messages):
                assert self.reader_closed
                self.restored.append(messages)
                if restore_fails:
                    raise OSError("restore failed")

        ui = UI(iter(()))
        app = CorkiApplication(
            CorkiSettings(tmp_path), CorkiPaths.from_home(tmp_path), Runtime(), ui
        )
        app._pending_messages.append("unsent")
        task = asyncio.create_task(app._consume_turn("initial"))
        await asyncio.wait_for(reading.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ui.restored == [("unsent",)]
        assert list(app._pending_messages) == (["unsent"] if restore_fails else [])
        assert app._queue_autosend is not restore_fails

    asyncio.run(scenario())


def test_application_routes_commands_and_messages(tmp_path: Path) -> None:
    def inputs() -> Iterator[str]:
        yield "/status"
        yield "hello"
        raise KeyboardInterrupt

    settings = CorkiSettings(working_directory=tmp_path)
    paths = CorkiPaths.from_home(tmp_path / ".corki")
    runtime = FakeRuntime()
    ui = FakeUI(inputs())
    app = CorkiApplication(settings, paths, runtime, ui)

    assert asyncio.run(app.run()) == 0
    assert runtime.received == ["hello"]
    # prompt-toolkit already leaves submitted input in terminal scrollback;
    # the application must not render the same user message a second time.
    assert ("user", "/status") not in ui.events
    assert ("user", "hello") not in ui.events
    assert ("reasoning_delta", "thinking") in ui.events
    assert ("assistant_delta", "reply: hello") in ui.events
    assert ui.events[-1] == ("goodbye", "")


def test_explicit_live_text_opt_out_does_not_start_a_turn_reader(tmp_path):
    class UI(FakeUI):
        async def read_message(self):
            raise AssertionError("opted-out turn must not read input")

    runtime, ui = FakeRuntime(), UI(iter(()))
    settings = CorkiSettings(working_directory=tmp_path, realtime_enabled=False)
    app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
    asyncio.run(app._consume_turn("hello"))
    assert runtime.received == ["hello"]
    assert ("assistant_delta", "reply: hello") in ui.events


@pytest.mark.parametrize("compact", [False, True])
def test_nonrealtime_composer_queues_without_enabling_runtime_steering(tmp_path, compact):
    from corki.cli.input_owner import QueuedInput

    async def scenario():
        started, queued = asyncio.Event(), asyncio.Event()
        thread, turn = new_thread_id(), new_turn_id()

        class Runtime(FakeRuntime):
            async def stream(self, message):  # Deliberately rejects realtime=True.
                self.received.append(message)
                started.set()
                await queued.wait()
                yield TurnCompleted(thread, turn, "done")

            async def compact(self):
                async for event in self.stream("summary"):
                    yield event

            async def steer(self, message):
                raise AssertionError("non-realtime input must never reach steer")

        class UI(FakeUI):
            keep_composer_during_turn = True
            reads = 0

            async def read_message(self):
                await started.wait()
                self.reads += 1
                if self.reads == 1:
                    return "next one"
                if self.reads == 2:
                    return QueuedInput("next two")
                queued.set()
                await asyncio.Future()

        runtime, ui = Runtime(), UI(iter(()))
        settings = CorkiSettings(tmp_path, realtime_enabled=False)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        await asyncio.wait_for(
            app._consume_realtime_turn(None) if compact else app._consume_turn("initial"), 2
        )
        assert runtime.received == ["summary" if compact else "initial"]
        assert list(app._pending_messages) == ["next one", "next two"]
        assert app._input._reader is None
        assert not app._realtime_enabled

    asyncio.run(scenario())


def test_mcp_refresh_command_reaches_runtime_without_model_input(tmp_path):
    class Runtime(FakeRuntime):
        refreshed = False

        def request_mcp_refresh(self):
            self.refreshed = True

    def inputs():
        yield "/mcp refresh"
        raise EOFError

    runtime, ui = Runtime(), FakeUI(inputs())
    app = CorkiApplication(
        CorkiSettings(working_directory=tmp_path),
        CorkiPaths.from_home(tmp_path / ".corki"),
        runtime,
        ui,
    )
    assert asyncio.run(app.run()) == 0
    assert runtime.refreshed and runtime.received == []


@pytest.mark.parametrize("failed", [False, True])
def test_compact_command_uses_runtime_operation_not_normal_input(tmp_path, failed):
    class Runtime(FakeRuntime):
        compacted = False

        async def compact(self):
            self.compacted = True
            thread, turn = new_thread_id(), new_turn_id()
            yield ContextCompactionStarted(thread, turn)
            if failed:
                yield TurnFailed(thread, turn, "summary unavailable")
            else:
                yield ContextCompacted(thread, turn, 123)
                yield TurnCompleted(thread, turn, "")

    def inputs():
        yield "/compact"
        raise EOFError

    runtime, ui = Runtime(), FakeUI(inputs())
    app = CorkiApplication(
        CorkiSettings(working_directory=tmp_path),
        CorkiPaths.from_home(tmp_path / ".corki"),
        runtime,
        ui,
    )
    assert asyncio.run(app.run()) == 0
    assert runtime.compacted and runtime.received == []
    assert ("notice", "Compacting context...") in ui.events
    if failed:
        assert ("error", "summary unavailable") in ui.events
    else:
        assert ("notice", "Context compacted (123 estimated tokens).") in ui.events


@pytest.mark.parametrize("pending_steer, queued", [(False, False), (True, False), (True, True)])
def test_busy_compact_is_rejected_and_idle_compact_remains_available(
    tmp_path, pending_steer, queued
):
    from corki.cli.input_owner import QueuedInput
    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, new_step_id
    from corki.tools import ToolRegistry

    async def scenario():
        entered, compacted = asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    entered.set()
                    await asyncio.Event().wait()
                assert len(requests) == 2
                assert "checkpoint compaction" in request.items[-1].content
                yield ModelCompleted(
                    (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(FakeUI):
            reads = 0
            restored = ()

            def restore_queued_inputs(self, messages):
                self.restored += messages

            async def read_message(self):
                self.reads += 1
                if self.reads == 2:
                    await entered.wait()
                messages = ["work"]
                if pending_steer:
                    messages.append("UNSUBMITTED STEER")
                if queued:
                    messages.append(QueuedInput("queued followup"))
                messages.extend(("/compact", "/stop", "/compact"))
                if self.reads <= len(messages):
                    if messages[self.reads - 1] == "/stop":
                        assert len(requests) == 1
                        assert not compacted.is_set()
                        assert (
                            "error",
                            "'/compact' is disabled while a task is in progress.",
                        ) in self.events
                    return messages[self.reads - 1]
                await compacted.wait()
                raise EOFError

        class Application(CorkiApplication):
            async def _consume_realtime_turn(self, message):
                await super()._consume_realtime_turn(message)
                if message is None:
                    compacted.set()

        settings = CorkiSettings(
            working_directory=tmp_path, realtime_enabled=True, skills_enabled=False
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        ui = UI(iter(()))
        app = Application(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert len(requests) == 2
        assert ui.restored == (
            *(("UNSUBMITTED STEER",) if pending_steer else ()),
            *(("queued followup",) if queued else ()),
        )
        assert not any(
            "UNSUBMITTED STEER" in getattr(i, "content", "") for r in requests for i in r.items
        )
        assert not any(getattr(i, "content", None) == "/compact" for r in requests for i in r.items)
        assert any(kind == "notice" and "Context compacted" in text for kind, text in ui.events)

    asyncio.run(scenario())


def test_tab_queued_compact_runs_after_current_turn_without_cancellation(tmp_path):
    from corki.cli.input_owner import QueuedInput
    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted
    from corki.protocol.items import AssistantMessageItem, new_step_id
    from corki.tools import ToolRegistry

    async def scenario():
        entered, queued, compacted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    entered.set()
                    await queued.wait()
                else:
                    assert len(requests) == 2
                    assert "checkpoint compaction" in request.items[-1].content
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(FakeUI):
            reads = 0

            def show_notice(self, message):
                super().show_notice(message)
                if message == "Queued for the next turn.":
                    queued.set()

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "work"
                if self.reads == 2:
                    await entered.wait()
                    return QueuedInput("/compact")
                await compacted.wait()
                raise EOFError

        class Application(CorkiApplication):
            async def _consume_realtime_turn(self, message):
                await super()._consume_realtime_turn(message)
                if message is None:
                    compacted.set()

        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        ui = UI(iter(()))
        app = Application(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert len(requests) == 2
        assert not any("interrupted" in text.lower() for _, text in ui.events)
        assert not any(kind == "error" for kind, _ in ui.events)
        assert not any(getattr(i, "content", None) == "/compact" for r in requests for i in r.items)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel, fail", [(False, False), (True, False), (False, True)])
def test_compaction_keeps_input_available_and_owns_followups(tmp_path, cancel, fail):
    from corki.cli.input_owner import QueuedInput
    from corki.core import LangGraphRuntime
    from corki.models import ModelCompleted, ModelError
    from corki.protocol.items import AssistantMessageItem, CompactionItem, new_step_id
    from corki.tools import ToolRegistry

    async def scenario():
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 2:
                    assert "checkpoint compaction" in request.items[-1].content
                    entered.set()
                    await release.wait()
                    if fail:
                        raise ModelError("summary unavailable")
                if fail and len(requests) == 3:
                    stored = await runtime._repository.load_items(runtime.thread_id)
                    assert {i.id for i in seed_history} <= {i.id for i in stored}
                    assert not any(isinstance(i, CompactionItem) for i in stored)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(FakeUI):
            reads = 0
            queued_count = 0
            restored = ()

            def show_notice(self, message):
                super().show_notice(message)
                if message == "Queued for the next turn.":
                    self.queued_count += 1
                    if self.queued_count == 2 and not cancel:
                        release.set()

            def restore_queued_inputs(self, messages):
                assert not release.is_set()
                self.restored = messages
                finished.set()

            async def read_message(self):
                self.reads += 1
                if self.reads == 1:
                    return "/compact"
                await entered.wait()
                if self.reads == 2:
                    return "after one"
                if self.reads == 3:
                    return QueuedInput("after two")
                if self.reads == 4 and cancel:
                    return "/stop"
                await finished.wait()
                raise EOFError

        class Application(CorkiApplication):
            async def _consume_turn(self, message):
                await super()._consume_turn(message)
                if message == "after two":
                    finished.set()

        # Test terminal failure ownership, separately from compaction's retry policy.
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, model_max_retries=0
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        async for _ in runtime.stream("seed"):
            pass
        seed_history = await runtime._repository.load_items(runtime.thread_id)
        ui = UI(iter(()))
        app = Application(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert ui.queued_count == 2
        if fail:
            assert any(
                kind == "error" and "summary unavailable" in text for kind, text in ui.events
            )
            assert not any("Context compacted" in text for _, text in ui.events)
        assert not any(
            getattr(i, "content", "") in {"after one", "after two"} for i in requests[1].items
        )
        if cancel:
            assert len(requests) == 2
            assert ui.restored == ("after one", "after two")
        else:
            assert len(requests) == 4
            assert [r.items[-1].content for r in requests[2:]] == ["after one", "after two"]
            assert ui.restored == ()

    asyncio.run(scenario())
