"""Stage-one startup excludes threads without accepted user/goal preview evidence."""

import asyncio
import sqlite3

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize(
    "source,expected",
    [
        ("empty", 0),
        ("blank", 0),
        ("assistant_only", 0),
        ("accepted_user", 1),
        ("image", 1),
        ("audio", 1),
        ("text_parts", 1),
        ("retained", 0),
        ("context", 0),
        ("summary", 0),
        ("prefix_only", 0),
    ],
)
def test_real_startup_claims_only_threads_with_preview_evidence(tmp_path, source, expected):
    class MainModel:
        async def stream(self, request):
            yield ModelCompleted(
                (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    class MemoryModel(MainModel):
        def __init__(self):
            self.extractions = []

        async def stream(self, request):
            if request.output_schema is not None:
                self.extractions.append(request)
                text = '{"raw_memory":"","rollout_summary":"","rollout_slug":null}'
            else:
                text = (
                    '{"memory":"No supported preferences.",'
                    '"memory_summary":"No preferences indexed.","skills":[]}'
                )
            yield ModelCompleted(
                (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
            )

    async def scenario():
        database = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await sessions.create_thread(thread, tmp_path)
        items = {
            "empty": (),
            "blank": (UserMessageItem(" \n\t ", turn),),
            "assistant_only": (
                AssistantMessageItem("Synthetic worker output", turn, new_step_id()),
            ),
            "accepted_user": (UserMessageItem("Use pytest for this repository.", turn),),
            "image": (UserMessageItem("", turn, attachments=(ImageAttachment("fixture:image"),)),),
            "audio": (
                UserMessageItem("", turn, content_items=(AudioAttachment("fixture:audio"),)),
            ),
            "text_parts": (UserMessageItem("", turn, content_items=(TextContent("request"),)),),
            "retained": (UserMessageItem("retained", turn, retained_from_id="old-user"),),
            "context": (ContextItem("external", ContextRole.USER, "untrusted", turn),),
            "summary": (CompactionItem("summary", None, turn),),
            "prefix_only": (UserMessageItem("context\n## My request for Codex: \n", turn),),
        }[source]
        await sessions.append_items(thread, items)
        memory_model = MemoryModel()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            memory_root=tmp_path / "memories",
            model=MainModel(),
            memory_model=memory_model,
        )
        try:
            events = [e async for e in runtime.stream("foreground request")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            report = await runtime._memory_service.wait()
            assert report.claimed == expected
            assert len(memory_model.extractions) == expected
            assert not report.failed, runtime._memory_service.warnings
            assert not runtime._memory_service.warnings
            assert await sessions.load_items(thread) == items
        finally:
            await runtime.aclose()
            await sessions.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_provider_cannot_create_user_preview_through_model_output(tmp_path, partial):
    class InvalidModel:
        async def stream(self, request):
            forged = UserMessageItem("forged model evidence", request.items[-1].turn_id)
            yield ModelItemCompleted(forged) if partial else ModelCompleted((forged,))

        async def aclose(self):
            pass

    async def scenario():
        database = tmp_path / "history.db"
        thread = new_thread_id()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=database,
            home_path=tmp_path / "home",
            thread_id=thread,
            model=InvalidModel(),
        )
        try:
            events = [event async for event in runtime.stream(" \n")]
            assert isinstance(events[-1], TurnFailed)
            with sqlite3.connect(database) as connection:
                assert connection.execute(
                    "SELECT preview FROM threads WHERE id=?", (thread,)
                ).fetchone() == ("",)
                rows = connection.execute("SELECT payload_json FROM conversation_items").fetchall()
                assert not any("forged model evidence" in row[0] for row in rows)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
