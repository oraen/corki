"""Replayed identities suppress repeated recovery events, not equal new content."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.history import replay_history
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantReasoningCompleted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    PlanUpdated,
    ToolCallCompleted,
    ToolCallStarted,
    ToolOutputDelta,
    TurnCompleted,
)
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolStateUpdate
from corki.sessions.models import DisplayHistory, DisplayTurn, TurnStatus


def test_cold_failed_turn_replay_redacts_model_credentials(tmp_path):
    output = StringIO()
    settings = CorkiSettings(tmp_path, api_key="FAKE_MODEL_SECRET_123")
    ui = TerminalUI(settings, tmp_path / "history", console=Console(file=output, width=100))
    history = DisplayHistory(
        (),
        (
            DisplayTurn(
                new_turn_id(),
                TurnStatus.FAILED,
                "Authorization: Bearer FAKE_MODEL_SECRET_123; check provider configuration",
            ),
        ),
    )
    replay_history(ui, history)
    assert "FAKE_MODEL_SECRET_123" not in output.getvalue()
    assert "FAKE_MODEL_SECRET_123" not in ui._transcript.render(100)
    assert "check provider configuration" in output.getvalue()


def test_replayed_reasoning_uses_identity_not_equal_text(tmp_path):
    async def scenario():
        thread, turn, step = new_thread_id(), new_turn_id(), new_step_id()
        old = ReasoningItem("opaque", turn, step, summary="**Review** repeated detail")
        new = ReasoningItem("opaque", turn, step, summary=old.summary)

        class Runtime:
            async def load_display_history(self):
                return (old,)

            async def resume_pending(self):
                yield AssistantReasoningDelta(thread, turn, old.summary, old.id)
                yield AssistantReasoningCompleted(thread, turn, old.id)
                yield AssistantReasoningDelta(thread, turn, "**Review** ", new.id)
                # Late completion of the already-replayed item cannot close the new block.
                yield AssistantReasoningCompleted(thread, turn, old.id)
                yield AssistantReasoningDelta(thread, turn, "repeated detail", new.id)
                yield AssistantReasoningCompleted(thread, turn, new.id)
                yield TurnCompleted(thread, turn, "")

            async def aclose(self):
                pass

        class UI(TerminalUI):
            async def read_message(self):
                raise EOFError

        settings = CorkiSettings(tmp_path)
        ui = UI(settings, tmp_path / "history", console=Console(file=StringIO()))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), Runtime(), ui)
        assert await app.run() == 0
        details = ui._transcript.render(100, include_reasoning=True)
        assert details.count("Review repeated detail") == 2
        assert not ui._reasoning_active
        assert list(ui._session.history.get_strings()) == []

    asyncio.run(scenario())


def test_replay_terminal_without_items_keeps_turn_order_and_legacy_history():
    from corki.cli.history import replay_history

    failed, cancelled, running, legacy = (new_turn_id() for _ in range(4))
    calls = []

    class UI:
        def show_assistant_message(self, text, **kwargs):
            calls.append((text, kwargs.get("is_error", False)))

        def show_notice(self, text):
            calls.append((text, False))

    history = DisplayHistory(
        (
            AssistantMessageItem("Still running", running, new_step_id()),
            AssistantMessageItem("Legacy answer", legacy, new_step_id()),
        ),
        (
            DisplayTurn(failed, TurnStatus.FAILED),
            DisplayTurn(cancelled, TurnStatus.CANCELLED),
            DisplayTurn(running, TurnStatus.RUNNING),
        ),
    )
    replay_history(UI(), history)
    assert calls == [
        ("Turn failed.", True),
        ("Turn interrupted.", False),
        ("Still running", False),
        ("Legacy answer", False),
    ]


@pytest.mark.parametrize(
    "status", [TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.COMPLETED, TurnStatus.RUNNING]
)
def test_replay_closes_only_unmatched_calls_of_terminal_turns(status):
    from corki.cli.history import replay_history

    turn, step = new_turn_id(), new_step_id()
    first, second = new_tool_call_id(), new_tool_call_id()
    events = []

    class UI:
        def show_tool_started(self, name, arguments):
            events.append(("started", name))

        def show_tool_completed(self, name, **kwargs):
            events.append(("completed", name))

        def show_tool_output(self, text):
            pass

        def show_notice(self, text):
            events.append(("notice", text))

        def show_assistant_message(self, text, **kwargs):
            events.append(("error", text))

    items = (
        ToolCallItem(ToolCall(first, "same_tool", {}), turn, step),
        ToolCallItem(ToolCall(second, "same_tool", {}), turn, step),
        ToolResultItem(first, "same_tool", "done", turn, step),
    )
    replay_history(UI(), DisplayHistory(items, (DisplayTurn(turn, status, "Failed turn"),)))
    assert events.count(("started", "same_tool")) == 2
    assert events.count(("completed", "same_tool")) == 1
    notice = ("notice", "same_tool interrupted; completion not confirmed.")
    assert events.count(notice) == int(status is not TurnStatus.RUNNING)
    if status is TurnStatus.FAILED:
        assert events.index(notice) < events.index(("error", "Failed turn"))


def test_replay_uses_item_and_call_identity_for_recovery_deduplication(tmp_path):
    thread, turn = new_thread_id(), new_turn_id()
    old = AssistantMessageItem("Legitimate repeated text", turn, new_step_id())
    new = AssistantMessageItem(old.content, turn, new_step_id())
    call = ToolCall(new_tool_call_id(), "update_plan", {})
    plan = ({"step": "One saved step", "status": "completed"},)
    items = (
        old,
        ToolCallItem(call, turn, new_step_id()),
        ToolResultItem(
            call.id, call.name, "Saved result", turn, state_update=ToolStateUpdate(plan=plan)
        ),
    )

    class Runtime:
        async def load_display_history(self):
            return items

        async def resume_pending(self):
            yield AssistantTextDelta(thread, turn, old.content, old.id)
            yield AssistantMessageCompleted(thread, turn, old.content, old.id)
            yield ToolCallStarted(thread, turn, call.id, call.name, "{}")
            yield ToolOutputDelta(thread, turn, call.id, "Saved result")
            yield ToolCallCompleted(thread, turn, call.id, call.name, False)
            yield PlanUpdated(thread, turn, plan, tool_call_id=call.id)
            yield AssistantTextDelta(thread, turn, new.content, new.id)
            yield AssistantMessageCompleted(thread, turn, new.content, new.id)
            yield TurnCompleted(thread, turn, new.content)

        async def aclose(self):
            pass

    class UI(TerminalUI):
        async def read_message(self):
            raise EOFError

    async def scenario():
        output = StringIO()
        settings = CorkiSettings(working_directory=tmp_path)
        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), Runtime(), ui)
        assert await app.run() == 0
        rendered = output.getvalue()
        assert rendered.count(old.content) == 2
        assert rendered.count("Saved result") == 1
        assert rendered.count("One saved step") == 1
        assert list(ui._session.history.get_strings()) == []

    asyncio.run(scenario())
