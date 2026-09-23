"""A real renderer fault closes both the display stream and owning Runtime stream."""

import asyncio
from contextlib import asynccontextmanager
from io import StringIO

import pytest
from rich.console import Console

from corki.cli import stream_commit
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted, ModelReasoningDelta, ModelTextDelta
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("burst", [False, True])
def test_animation_drains_rows_while_real_model_stream_is_waiting(tmp_path, monkeypatch, burst):
    async def scenario():
        waiting, drained = asyncio.Event(), asyncio.Event()
        ticks, written = [], []
        source = "".join(f"- row {n}\n" for n in range(10)) if burst else "first\n\nsecond\n"

        class Model:
            async def stream(self, request):
                yield ModelTextDelta(source)
                waiting.set()
                await drained.wait()
                yield ModelCompleted(
                    (AssistantMessageItem(source, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class UI(TerminalUI):
            async def commit_stream_tick(self, *, catch_up_only=False, finish=False):
                stream = self._stream_markdown
                before = stream.emitted if stream else 0
                await super().commit_stream_tick(catch_up_only=catch_up_only, finish=finish)
                if not finish and stream is not None and stream.emitted > before:
                    if not catch_up_only:
                        assert waiting.is_set()
                    ticks.append((catch_up_only, stream.emitted - before))
                    if not stream.queued_lines:
                        drained.set()

        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "animation.db",
            model=Model(),
            home_path=tmp_path,
        )
        ui = UI(
            settings,
            tmp_path / "history",
            console=Console(file=StringIO(), force_terminal=True, width=40),
        )
        monkeypatch.setattr(stream_commit, "StdoutProxy", StringIO)
        monkeypatch.setattr(ui._session.app.output, "write_raw", written.append)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            await asyncio.wait_for(app._consume_events(runtime.stream("animate")), 5)
            if burst:
                assert len(ticks) == 1 and ticks[0][0] and ticks[0][1] >= 8
                assert "row 9" in "".join(written)
            else:
                assert len(ticks) > 1 and set(ticks) == {(False, 1)}
                assert "first" in "".join(written) and "second" in "".join(written)
            assert not ui._animation_enabled
            assert not any(t.get_name() == "corki-display-next-event" for t in asyncio.all_tasks())
        finally:
            drained.set()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("failure", ["write", "flush", "redraw", "write_redraw"])
def test_owned_terminal_write_failure_closes_real_runtime(tmp_path, monkeypatch, cancel, failure):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        class Model:
            stream_closed = False

            async def stream(self, request):
                try:
                    yield ModelTextDelta("Opening line\n\n")
                    await asyncio.Event().wait()
                finally:
                    self.stream_closed = True

            async def aclose(self):
                pass

        @asynccontextmanager
        async def terminal():
            entered.set()
            await release.wait()
            try:
                yield
            finally:
                if failure in {"redraw", "write_redraw"}:
                    raise OSError("owned terminal redraw failed")

        def fail_write(text):
            writes.append(text)
            if failure in {"write", "write_redraw"}:
                raise OSError("owned terminal write failed")

        def flush():
            if failure == "flush":
                raise OSError("owned terminal flush failed")

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            home_path=tmp_path,
        )
        ui = TerminalUI(
            settings,
            tmp_path / "input-history",
            console=Console(file=StringIO(), force_terminal=True, width=80),
        )
        monkeypatch.setattr(stream_commit, "StdoutProxy", StringIO)
        monkeypatch.setattr(stream_commit, "in_terminal", terminal)
        monkeypatch.setattr(ui._session.app.output, "write_raw", fail_write)
        monkeypatch.setattr(ui._session.app.output, "flush", flush)
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        task = asyncio.create_task(app._consume_events(runtime.stream("test owned cleanup")))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if cancel:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert not model.stream_closed
            release.set()
            expected = asyncio.CancelledError if cancel else OSError
            with pytest.raises(expected) as caught:
                await asyncio.wait_for(task, 5)
            if not cancel and failure == "write_redraw":
                assert str(caught.value) == "owned terminal write failed"
            assert len(writes) == 1 and "Opening line" in writes[0]
            assert model.stream_closed
            names = [method.__name__ for method, _, _ in ui._transcript.calls]
            assert names.count("show_assistant_message") == 1
            assert not {"begin_assistant_message", "append_assistant_delta"} & set(names)
            assert ui._transcript.render(80).count("Opening line") == 1
            assert ui._stream_markdown is None and not ui._assistant_started
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix", ["Opening line\n", "Opening line\nunfinished "])
def test_interleaved_tool_does_not_split_authoritative_assistant_source(tmp_path, prefix):
    async def scenario():
        finished = asyncio.Event()

        class Tool:
            spec = ToolSpec(
                "interleaved",
                "fixture",
                {"type": "object"},
                concurrency=ToolConcurrency.PARALLEL,
            )
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                finished.set()
                return ToolResult(call.id, call.name, "tool result")

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls == 2:
                    assert sum(isinstance(i, ToolResultItem) for i in request.items) == 1
                    yield ModelCompleted((AssistantMessageItem("Final answer", turn, step),))
                    return
                message = AssistantMessageItem(prefix + "Closing line", turn, step)
                call = ToolCallItem(ToolCall(new_tool_call_id(), "interleaved", {}), turn, step)
                yield ModelTextDelta(prefix, message.id)
                yield ModelItemCompleted(call)
                await finished.wait()
                yield ModelTextDelta("Closing line", message.id)
                yield ModelCompleted((message, call))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        registry, tool, model = ToolRegistry(), Tool(), Model()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            registry=registry,
            home_path=tmp_path,
        )
        ui = TerminalUI(
            settings, tmp_path / "input-history", console=Console(file=StringIO(), width=100)
        )
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            await asyncio.wait_for(app._consume_events(runtime.stream("Run")), 5)
            assert model.calls == 2 and tool.calls == 1
            for width in (40, 100):
                rendered = ui._transcript.render(width)
                assert rendered.count("Opening line") == 1
                assert rendered.count("Closing line") == 1
                assert rendered.count("Final answer") == 1
                if "unfinished" in prefix:
                    assert "unfinished Closing line" in rendered
                assert rendered.index("Closing line") < rendered.index("interleaved")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reasoning", [False, True])
def test_terminal_fault_after_partial_output_closes_runtime_and_display(tmp_path, reasoning):
    class Model:
        stream_closed = False
        closed = False

        async def stream(self, request):
            try:
                yield (ModelReasoningDelta if reasoning else ModelTextDelta)("partial output")
                await asyncio.Event().wait()
            finally:
                self.stream_closed = True

        async def aclose(self):
            self.closed = True

    class UI(TerminalUI):
        def append_assistant_delta(self, delta):
            super().append_assistant_delta(delta)
            raise OSError("terminal write failed")

        def append_reasoning_delta(self, delta):
            super().append_reasoning_delta(delta)
            raise OSError("terminal write failed")

    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            home_path=tmp_path,
        )
        ui = UI(settings, tmp_path / "input-history", console=Console(file=StringIO(), width=80))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        try:
            with pytest.raises(OSError, match="terminal write failed"):
                await asyncio.wait_for(app._consume_events(runtime.stream("test cleanup")), 5)
            assert model.stream_closed
            names = [method.__name__ for method, _, _ in ui._transcript.calls]
            end = "end_reasoning" if reasoning else "end_assistant_message"
            if reasoning:
                assert names[-1] == end and names.count(end) == 1
            else:
                assert names[-1] == "show_assistant_message"
                assert ui._transcript.render(80).count("partial output") == 1
                assert ui._stream_markdown is None and not ui._assistant_started
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await runtime.aclose()
        assert model.closed

    asyncio.run(scenario())


def test_actual_tool_cancellation_marks_display_unconfirmed_once(tmp_path):
    async def scenario():
        entered, displayed = asyncio.Event(), asyncio.Event()

        class Tool:
            spec = ToolSpec("held_operation", "Wait for cancellation", {"type": "object"})
            closed = False
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    self.closed = True

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "held_operation", {}),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        class UI(TerminalUI):
            def show_tool_started(self, name, arguments_preview):
                super().show_tool_started(name, arguments_preview)
                displayed.set()

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        registry, tool, model = ToolRegistry(), Tool(), Model()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            model=model,
            registry=registry,
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        task = asyncio.create_task(app._consume_events(runtime.stream("Run the held operation")))
        try:
            await asyncio.wait_for(asyncio.gather(entered.wait(), displayed.wait()), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            notice = "held_operation interrupted; completion not confirmed."
            assert output.getvalue().count(notice) == 1
            assert ui._transcript.render(100).count(notice) == 1
            assert tool.closed and tool.calls == model.calls == 1
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
