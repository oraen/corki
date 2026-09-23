"""Owned extraction commits its snapshot; current mode gates later selection."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("change", ["disabled", "polluted", "new_version"])
@pytest.mark.parametrize("empty", [False, True])
def test_owned_extraction_survives_source_change_without_bypassing_selection(
    tmp_path, change, empty
):
    async def scenario():
        database, root = tmp_path / "history.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source, turn = new_thread_id(), new_turn_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.append_items(source, (UserMessageItem("CLAIMED_SOURCE", turn),))
        version = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        newer = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        with sqlite3.connect(database) as db:
            db.execute("UPDATE threads SET updated_at=? WHERE id=?", (version, source))
        extracted, release = asyncio.Event(), asyncio.Event()
        consolidations = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                if request.output_schema is not None:
                    extracted.set()
                    await release.wait()
                    value = {
                        "raw_memory": "" if empty else "EXTRACTED_SNAPSHOT",
                        "rollout_summary": "snapshot",
                        "rollout_slug": None,
                    }
                else:
                    inputs = inspect_worker_evidence(request)
                    consolidations.append(inputs)
                    assert ("EXTRACTED_SNAPSHOT" in inputs["raw_memories"]) is (
                        change == "new_version" and not empty
                    )
                    value = {"memory": "valid output", "memory_summary": "routing", "skills": []}
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(extracted.wait(), 2)
            if change == "disabled":
                await runtime.set_thread_memory_mode("disabled", thread_id=source)
            else:
                with sqlite3.connect(database) as db:
                    if change == "new_version":
                        db.execute("UPDATE threads SET updated_at=? WHERE id=?", (newer, source))
                    else:
                        db.execute("UPDATE threads SET memory_mode=? WHERE id=?", (change, source))
            release.set()
            report = await asyncio.wait_for(runtime._memory_service.wait(), 3)
            assert report.failed == 0 and report.consolidated
            assert report.empty == int(empty) and report.extracted == int(not empty)
            with sqlite3.connect(database) as db:
                assert db.execute(
                    "SELECT status, last_success_source_updated_at "
                    "FROM memory_jobs WHERE job_key=?",
                    (source,),
                ).fetchone() == ("succeeded", version)
                rows = db.execute(
                    "SELECT source_updated_at, raw_memory "
                    "FROM memory_stage1_outputs WHERE thread_id=?",
                    (source,),
                ).fetchall()
                assert rows == ([] if empty else [(version, "EXTRACTED_SNAPSHOT")])
            if change != "new_version" and not empty:
                repository = runtime._memory_repository
                assert not await repository.load_consolidation_inputs(limit=10, max_unused_days=30)
                await runtime.set_thread_memory_mode("enabled", thread_id=source)
                selected = await repository.load_consolidation_inputs(limit=10, max_unused_days=30)
                assert len(selected) == 1 and selected[0].raw_memory == "EXTRACTED_SNAPSHOT"
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_runtime_reextracts_submillisecond_new_source(tmp_path):
    async def scenario():
        database, root = tmp_path / "history.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.append_items(source, (UserMessageItem("OLD_SOURCE", new_turn_id()),))
        old = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=100_000)
        old_version = old.isoformat()
        advanced_version = (old + timedelta(microseconds=1)).isoformat()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE threads SET updated_at=? WHERE id=?", (old_version, str(source))
            )
        extracted = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                if request.output_schema is not None:
                    extracted.append(request)
                    value = {
                        "raw_memory": f"VERSION_{len(extracted)}",
                        "rollout_summary": "source summary",
                        "rollout_slug": None,
                    }
                else:
                    value = {"memory": "detail", "memory_summary": "summary", "skills": []}
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        thread = None
        for expected in (1, 2):
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    memories_enabled=True,
                    memories_min_thread_idle_hours=0,
                ),
                database_path=database,
                home_path=tmp_path / "home",
                memory_root=root,
                thread_id=thread,
                model=Main(),
                memory_model=Memory(),
            )
            thread = runtime.thread_id
            try:
                events = [event async for event in runtime.stream("work")]
                assert isinstance(events[-1], TurnCompleted)
                report = await runtime._memory_service.wait()
                assert report.failed == 0 and report.extracted == 1
                with sqlite3.connect(database) as connection:
                    assert connection.execute(
                        "SELECT source_updated_at, raw_memory FROM memory_stage1_outputs "
                        "WHERE thread_id=?",
                        (str(source),),
                    ).fetchone() == (
                        old_version if expected == 1 else advanced_version,
                        f"VERSION_{expected}",
                    )
            finally:
                await runtime.aclose()
            if expected == 1:
                await sessions.append_items(source, (UserMessageItem("NEW_SOURCE", new_turn_id()),))
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "UPDATE threads SET updated_at=? WHERE id=?",
                        (advanced_version, str(source)),
                    )
        assert len(extracted) == 2

    asyncio.run(scenario())
