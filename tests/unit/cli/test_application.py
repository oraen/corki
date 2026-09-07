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
    ModelRetryScheduled,
    TurnCompleted,
    TurnFailed,
    WarningEvent,
)
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.realtime import RealtimeTurnClosedError


@dataclass
class FakeUI:
    messages: Iterator[str]
    events: list[tuple[str, str]] = field(default_factory=list)

    def show_welcome(self) -> None:
        self.events.append(("welcome", ""))

    async def read_message(self) -> str:
        return next(self.messages)

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

    def show_plan(self, plan: tuple[dict[str, str], ...]) -> None:
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

    async def stream(self, message: str) -> AsyncIterator[object]:
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


@pytest.mark.parametrize("limit", [3, None])
def test_retry_closes_partial_output_without_claiming_user_steering(tmp_path, limit):
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
            yield ModelRetryScheduled(thread, turn, 1, limit, 5, "network failure")
            yield AssistantTextDelta(thread, turn, "recovered")
            yield TurnCompleted(thread, turn, "recovered")

        await app._consume_events(events())
        assert ui.events == [
            ("assistant_start", ""),
            ("assistant_delta", "partial"),
            ("assistant_end", ""),
            ("notice", "Response interrupted; retrying with updated history..."),
            ("notice", f"Reconnecting... 1/{limit if limit is not None else '∞'} in 5s"),
            ("assistant_start", ""),
            ("assistant_delta", "recovered"),
            ("assistant_end", ""),
        ]

    asyncio.run(scenario())


def test_late_steering_submission_is_kept_for_next_turn(tmp_path: Path) -> None:
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
                if len(self.received) == 2:
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
                if self.reads == 2:
                    await first_started.wait()
                    return "late input"
                await second_finished.wait()
                raise EOFError

        runtime, ui = ClosingRuntime(), BoundaryUI(iter(()))
        settings = CorkiSettings(working_directory=tmp_path, realtime_enabled=True)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), timeout=3) == 0
        assert runtime.received == ["initial", "late input"]
        assert not any(kind == "error" for kind, _ in ui.events)

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


def test_realtime_compact_cancels_current_turn_and_runs_standalone_operation(tmp_path):
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
                assert "checkpoint compaction" in request.items[-1].content
                compacted.set()
                yield ModelCompleted(
                    (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
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
                    return "/compact"
                await compacted.wait()
                raise EOFError

        settings = CorkiSettings(
            working_directory=tmp_path, realtime_enabled=True, skills_enabled=False
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        ui = UI(iter(()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path / ".corki"), runtime, ui)
        assert await asyncio.wait_for(app.run(), 3) == 0
        assert len(requests) == 2
        assert not any(getattr(i, "content", None) == "/compact" for r in requests for i in r.items)
        assert not any(
            "<turn_aborted>" in getattr(i, "content", "") for r in requests for i in r.items
        )
        assert any(kind == "notice" and "Context compacted" in text for kind, text in ui.events)

    asyncio.run(scenario())
