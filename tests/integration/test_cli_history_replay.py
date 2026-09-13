"""Cold CLI replays canonical local history, not a model window or tool execution."""

import asyncio
import json
from dataclasses import replace
from io import StringIO

import pytest
from rich.console import Console

from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelItemCompleted
from corki.protocol.ids import new_item_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    RemoteHistoryItem,
    ToolCallItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import TextContent, ToolCall
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("legacy_abort", [False, True])
def test_cold_cli_marks_unfinished_tool_without_synthesizing_result(tmp_path, legacy_abort):
    class Model:
        async def stream(self, request):
            raise AssertionError("history replay must not sample or execute tools")
            yield

        async def aclose(self):
            pass

    class UI(TerminalUI):
        async def read_message(self):
            raise EOFError

    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        database = tmp_path / "unresolved.db"
        warm = LangGraphRuntime.create(
            settings=settings, database_path=database, model=Model(), home_path=tmp_path
        )
        await warm._ensure_ready()
        thread, turn = warm.thread_id, new_turn_id()
        items = (
            UserMessageItem("Saved operation", turn),
            ToolCallItem(ToolCall(new_tool_call_id(), "unresolved_tool", {}), turn, new_step_id()),
        )
        if legacy_abort:
            items += (TurnAbortedItem("Interrupted", turn),)
        else:
            await warm._repository.save_turn(
                TurnRecord(
                    turn, thread, TurnStatus.FAILED, "Saved operation", error="Saved failure"
                )
            )
        await warm._repository.append_items(thread, items)
        await warm.aclose()
        cold = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=Model(),
            thread_id=thread,
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(
            settings,
            tmp_path / "history",
            console=Console(file=output, width=100, color_system=None),
        )
        assert await CorkiApplication(settings, CorkiPaths.from_home(tmp_path), cold, ui).run() == 0
        notice = "unresolved_tool interrupted; completion not confirmed."
        terminal = "Turn interrupted." if legacy_abort else "Saved failure"
        for rendered in (output.getvalue(), ui._transcript.render(100)):
            assert rendered.count(notice) == 1
            assert rendered.index(notice) < rendered.index(terminal)
        repository = SQLiteSessionRepository(database)
        try:
            assert await repository.load_items(thread) == items
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_cold_cli_restores_unsuccessful_turn_terminal(tmp_path, partial, cancelled):
    class Model:
        calls = 0

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                if partial:
                    yield ModelItemCompleted(
                        AssistantMessageItem("Partial saved answer", turn, step)
                    )
                if cancelled:
                    raise asyncio.CancelledError
                raise ModelError("Saved failure marker")
            assert self.calls == 2, "cold replay must not sample"
            yield ModelCompleted((AssistantMessageItem("Later valid answer", turn, step),))

        async def aclose(self):
            pass

    class UI(TerminalUI):
        async def read_message(self):
            raise EOFError

    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        database = tmp_path / "terminal-history.db"
        model = Model()
        warm = LangGraphRuntime.create(
            settings=settings, database_path=database, model=model, home_path=tmp_path
        )
        try:
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    _ = [event async for event in warm.stream("First unsuccessful request")]
            else:
                _ = [event async for event in warm.stream("First unsuccessful request")]
            _ = [event async for event in warm.stream("Later successful request")]
            original = await warm._repository.load_items(warm.thread_id)
            thread = warm.thread_id
        finally:
            await warm.aclose()
        cold = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=model,
            thread_id=thread,
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(
            settings,
            tmp_path / "input-history",
            console=Console(file=output, width=100, color_system=None),
        )
        assert await CorkiApplication(settings, CorkiPaths.from_home(tmp_path), cold, ui).run() == 0
        marker = "Turn interrupted." if cancelled else "Saved failure marker"
        for rendered in (output.getvalue(), ui._transcript.render(100)):
            assert rendered.count(marker) == 1
            assert (
                rendered.index("First unsuccessful request")
                < rendered.index(marker)
                < rendered.index("Later successful request")
            )
            assert rendered.count("Partial saved answer") == int(partial)
            assert rendered.count("Later valid answer") == 1
        assert model.calls == 2
        repository = SQLiteSessionRepository(database)
        try:
            assert await repository.load_items(thread) == original
        finally:
            await repository.close()

    asyncio.run(scenario())


def test_cold_cli_replays_two_turns_and_plan_without_sampling_or_rewriting(tmp_path):
    class Model:
        calls = 0
        closed = False

        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls % 2:
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                new_tool_call_id(),
                                "update_plan",
                                {
                                    "explanation": "Saved plan explanation",
                                    "plan": [{"step": "Saved checklist", "status": "completed"}],
                                },
                            ),
                            turn,
                            step,
                        ),
                    )
                )
            else:
                yield ModelCompleted((AssistantMessageItem("Repeated valid answer", turn, step),))

        async def aclose(self):
            self.closed = True

    class UI(TerminalUI):
        async def read_message(self):
            raise EOFError

    async def scenario():
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        model = Model()
        database = tmp_path / "sessions.db"
        runtime = LangGraphRuntime.create(
            settings=settings, database_path=database, model=model, home_path=tmp_path
        )
        for text in ("First saved request", "Second saved request"):
            _ = [event async for event in runtime.stream(text)]
        repository, thread = runtime._repository, runtime.thread_id
        original = await repository.load_items(thread)
        user = next(i for i in original if isinstance(i, UserMessageItem))
        await repository.append_items(
            thread,
            (
                CompactionItem(
                    "PRIVATE_MODEL_SUMMARY",
                    original[-1].id,
                    original[-1].turn_id,
                    replacement_item_count=1,
                ),
                replace(user, id=new_item_id(), retained_from_id=user.id),
            ),
        )
        before = await repository.load_items(thread)
        await runtime.aclose()
        cold_model = Model()
        cold = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            thread_id=thread,
            model=cold_model,
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), cold, ui)
        assert await app.run() == 0
        rendered = output.getvalue()
        assert rendered.count("First saved request") == 1
        assert rendered.count("Second saved request") == 1
        assert rendered.count("Repeated valid answer") == 2
        assert rendered.count("✔ Saved checklist") == 2
        assert "PRIVATE_MODEL_SUMMARY" not in rendered
        assert cold_model.calls == 0 and cold_model.closed
        assert model.calls == 4
        assert await repository.load_items(thread) == before
        assert list(ui._session.history.get_strings()) == []
        assert ui._transcript.render(100).count("Repeated valid answer") == 2

    asyncio.run(scenario())


def test_cold_cli_shows_plain_archive_and_text_parts_without_exposing_private_payloads(tmp_path):
    class Model:
        async def stream(self, request):
            raise AssertionError("display replay must not sample")
            yield

        async def aclose(self):
            pass

    class UI(TerminalUI):
        async def read_message(self):
            raise EOFError

    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        from corki.protocol.ids import new_thread_id

        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        items = (
            UserMessageItem("", "old", content_items=(TextContent("Text-only parts user"),)),
            *(
                RemoteHistoryItem(
                    json.dumps(
                        {
                            "type": "message",
                            "role": role,
                            "content": [
                                {
                                    "type": "input_text" if role == "user" else "output_text",
                                    "text": f"Archived {role} content",
                                },
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/png;base64,PRIVATE_IMAGE",
                                },
                            ],
                            "internal_chat_message_metadata_passthrough": {
                                "turn_id": "PRIVATE_METADATA"
                            },
                        }
                    ),
                    "old",
                )
                for role in ("user", "assistant")
            ),
            RemoteHistoryItem(
                '{"type":"agent_message","author":"a","recipient":"b","content":[{"type":"encrypted_content","encrypted_content":"PRIVATE_CIPHER"}]}',
                "old",
            ),
        )
        await repository.append_items(thread, items)
        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            repository=repository,
            thread_id=thread,
            model=Model(),
            home_path=tmp_path,
        )
        output = StringIO()
        ui = UI(settings, tmp_path / "input-history", console=Console(file=output, width=100))
        app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), runtime, ui)
        assert await app.run() == 0
        for rendered in (output.getvalue(), ui._transcript.render(100)):
            for text in (
                "Text-only parts user",
                "Archived user content",
                "Archived assistant content",
            ):
                assert rendered.count(text) == 1
            assert rendered.count("[Historical message contains attachments]") == 2
            assert "PRIVATE_" not in rendered
        assert await repository.load_items(thread) == items
        assert list(ui._session.history.get_strings()) == []

    asyncio.run(scenario())
