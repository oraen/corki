"""Cold CLI resumes real SQLite checkpoints without replaying durable side effects."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec, ToolStateUpdate
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("boundary", ["model_commit", "tool_history"])
def test_cold_cli_replays_then_resumes_pre_checkpoint_commit(tmp_path, monkeypatch, boundary):
    class Sink:
        async def emit(self, event):
            pass

    class UI(TerminalUI):
        async def read_message(self):
            if not self._mode_cycle_enabled:
                # The recovery composer is live; silence is not EOF/cancellation.
                # Its owned reader is cancelled when the recovered turn finishes.
                await asyncio.Future()
            raise EOFError

    class Tool:
        spec = ToolSpec("saved_operation", "A durable operation", {"type": "object"})
        calls = 0

        async def execute(self, call, context):
            self.calls += 1
            return ToolResult(
                call.id,
                call.name,
                "Durable result marker",
                state_update=ToolStateUpdate(
                    plan=({"step": "Recovered checklist", "status": "completed"},),
                    plan_explanation="Durable plan explanation",
                ),
            )

    class Model:
        calls = 0
        closed = False

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            result = next((i for i in request.items if isinstance(i, ToolResultItem)), None)
            if result is None:
                yield ModelCompleted(
                    (
                        AssistantMessageItem("Before operation marker", turn, step),
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), "saved_operation", {}), turn, step
                        ),
                    )
                )
            else:
                assert result.content == "Durable result marker" and not result.is_error
                yield ModelCompleted((AssistantMessageItem("Final recovery marker", turn, step),))

        async def aclose(self):
            self.closed = True

    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        database = tmp_path / "sessions.db"
        tool, registry, warm_model = Tool(), ToolRegistry(), Model()
        registry.register(tool)
        warm = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=warm_model,
            registry=registry,
            home_path=tmp_path,
        )
        await warm._ensure_ready()
        repository, thread, turn = warm._repository, warm.thread_id, new_turn_id()
        user = UserMessageItem("Recover pending operation", turn)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread, (user,))
        reached = asyncio.Event()
        method = "commit_model_step" if boundary == "model_commit" else "append_items"
        original = getattr(type(repository), method)

        async def crash_after_write(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            if boundary == "model_commit" or any(isinstance(i, ToolResultItem) for i in args[1]):
                reached.set()
                # Simulate death after business commit, before this graph node checkpoint.
                await asyncio.Event().wait()

        try:
            with monkeypatch.context() as patch:
                patch.setattr(type(repository), method, crash_after_write)
                invocation = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=Sink()),
                        config=warm._graph_config(turn),
                    )
                )
                try:
                    await asyncio.wait_for(reached.wait(), 5)
                finally:
                    invocation.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await invocation
            assert reached.is_set() and warm_model.calls == 1
            assert tool.calls == (boundary == "tool_history")
            assert await warm._checkpointer.aget_tuple(warm._graph_config(turn)) is not None
            before = await repository.load_items(thread)
            assert any(isinstance(i, AssistantMessageItem) for i in before)
            assert (await repository.latest_running_turn(thread)).id == turn
        finally:
            await warm.aclose()

        cold_model = Model()
        registry = ToolRegistry()
        registry.register(tool)
        cold = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=cold_model,
            registry=registry,
            thread_id=thread,
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), cold, ui)
        assert await app.run() == 0
        rendered = output.getvalue()
        assert "Turn interrupted." not in rendered
        assert not app._turn_cancelled
        reflowed = ui._transcript.render(100)
        for marker in (
            "Recover pending operation",
            "Before operation marker",
            "Durable result marker",
            "Durable plan explanation",
            "✔ Recovered checklist",
            "Final recovery marker",
        ):
            assert rendered.count(marker) == 1, rendered
            assert reflowed.count(marker) == 1, reflowed
        assert tool.calls == 1 and cold_model.calls == 1
        assert warm_model.closed and cold_model.closed
        stored = await cold._repository.load_items(thread)
        assert len([i for i in stored if isinstance(i, UserMessageItem)]) == 1
        assert len([i for i in stored if isinstance(i, ToolResultItem)]) == 1
        assert all(i in stored for i in before)
        assert await cold._repository.latest_running_turn(thread) is None
        assert list(ui._session.history.get_strings()) == []

    asyncio.run(scenario())
