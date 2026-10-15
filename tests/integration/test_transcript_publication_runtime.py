"""A failed transcript copy never replays a committed Runtime effect."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, WarningEvent
from corki.protocol.ids import ThreadId, ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def test_failed_transcript_copy_survives_runtime_close_and_cold_retry(
    tmp_path, monkeypatch, caplog
):
    async def scenario():
        samples, effects = [], []

        class Tool:
            spec = ToolSpec("effect", "fixture", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "executed once")

        class Model:
            async def stream(self, request):
                samples.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall(ToolCallId("once"), "effect", {}), turn, step)
                    if len(samples) == 1
                    else AssistantMessageItem("done", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        database = tmp_path / "state.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            registry=registry,
            database_path=database,
            home_path=tmp_path,
        )
        try:
            await runtime._ensure_ready()
            thread = runtime.thread_id
            path = await runtime._repository.materialize_transcript(thread)
            before = path.read_bytes()

            def fail(*args):
                raise OSError("transcript copy unavailable")

            with monkeypatch.context() as patch:
                patch.setattr("corki.storage.transcripts.flush_transcript", fail)
                events = [e async for e in runtime.stream("execute")]
                assert isinstance(events[-1], TurnCompleted)
                warnings = [
                    e
                    for e in events
                    if isinstance(e, WarningEvent) and "transcript copy" in e.message
                ]
                assert len(warnings) == 1
                assert "transcript copy" in warnings[0].message
                assert len(samples) == 2 and effects == ["once"]
                assert path.read_bytes() == before
                assert await runtime._repository.transcript_publication_pending(thread)
                assert not await runtime._repository.transcript_publication_pending(
                    ThreadId("unrelated")
                )
                with runtime._repository._connect() as connection:
                    expected = connection.execute(
                        "SELECT kind, payload_json FROM conversation_items "
                        "WHERE thread_id=? ORDER BY sequence",
                        (thread,),
                    ).fetchall()
                    assert connection.execute("SELECT * FROM transcript_pending").fetchall()
                await runtime.aclose()
            assert "Local transcript publication failed" in caplog.text
        finally:
            await runtime.aclose()

        cold = SQLiteSessionRepository(database)
        try:
            await cold.create_thread(ThreadId("unrelated"), tmp_path)
            rows = [json.loads(line) for line in path.read_text().splitlines()][1:]
            assert [(row["type"], row["payload"]) for row in rows] == [
                (row["kind"], json.loads(row["payload_json"])) for row in expected
            ]
            with cold._connect() as connection:
                assert connection.execute("SELECT * FROM transcript_pending").fetchall() == []
            assert not await cold.transcript_publication_pending(thread)
            assert len(samples) == 2 and effects == ["once"]
        finally:
            await cold.close()

        resumed = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=database,
            home_path=tmp_path,
            thread_id=thread,
        )
        try:
            events = [e async for e in resumed.stream("continue after repair")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(
                isinstance(e, WarningEvent) and "transcript copy" in e.message for e in events
            )
            assert len(samples) == 3 and effects == ["once"]
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["success", "failure", "cancel"])
def test_transcript_diagnostic_failure_preserves_turn_result(tmp_path, monkeypatch, caplog, ending):
    async def scenario():
        samples, inspected = [], []

        class Model:
            async def stream(self, request):
                samples.append(request)
                if ending == "failure":
                    raise ModelError("original model failure", retryable=False)
                if ending == "cancel":
                    raise asyncio.CancelledError
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def fail_query(thread):
            inspected.append(thread)
            raise OSError("diagnostic read failed")

        monkeypatch.setattr(runtime._repository, "transcript_publication_pending", fail_query)
        events = []
        try:

            async def consume():
                async for event in runtime.stream("run"):
                    events.append(event)

            if ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await consume()
            else:
                await consume()
            expected = {"success": TurnCompleted, "failure": TurnFailed, "cancel": TurnCancelled}
            assert isinstance(events[-1], expected[ending])
            assert (
                sum(isinstance(e, (TurnCompleted, TurnFailed, TurnCancelled)) for e in events) == 1
            )
            if ending == "failure":
                assert events[-1].error == "original model failure"
            assert len(samples) == 1 and inspected == [runtime.thread_id]
            assert "Could not inspect transcript publication state" in caplog.text
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
