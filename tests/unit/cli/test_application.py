import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from corki.cli.application import CorkiApplication
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import AssistantReasoningDelta, AssistantTextDelta, TurnCompleted
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
