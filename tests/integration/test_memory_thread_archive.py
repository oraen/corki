"""Real startup source selection and retained derived memories across archive transitions."""

import asyncio
import sqlite3

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id


class Model:
    def __init__(self):
        self.extractions = []

    async def stream(self, request):
        text = '{"memory":"No preferences.","memory_summary":"No preferences.","skills":[]}'
        if request.output_schema is not None:
            self.extractions.append(request)
            text = '{"raw_memory":"","rollout_summary":"","rollout_slug":null}'
        yield ModelCompleted(
            (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


async def runtime_for(tmp_path, *, memories=False, memory_model=None):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            memories_enabled=memories,
            memories_min_thread_idle_hours=0,
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        model=Model(),
        memory_model=memory_model,
    )


def test_archived_sources_are_excluded_before_real_startup_scan_cap(tmp_path, monkeypatch):
    from corki.memory import extraction

    async def scenario():
        active, archived = await runtime_for(tmp_path), await runtime_for(tmp_path)
        current = None
        try:
            _ = [event async for event in active.stream("eligible active source")]
            _ = [event async for event in archived.stream("excluded archived source")]
            await archived.archive()
            with sqlite3.connect(tmp_path / "history.db") as db:
                db.execute(
                    "UPDATE threads SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','-2 hours') "
                    "WHERE id=?",
                    (active.thread_id,),
                )
            monkeypatch.setattr(extraction, "THREAD_SCAN_LIMIT", 1)
            memory_model = Model()
            current = await runtime_for(tmp_path, memories=True, memory_model=memory_model)
            assert isinstance(
                [event async for event in current.stream("foreground")][-1], TurnCompleted
            )
            report = await current._memory_service.wait()
            assert report.claimed == 1 and not report.failed
            assert len(memory_model.extractions) == 1
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute(
                    "SELECT job_key FROM memory_jobs WHERE kind='memory_stage1'"
                ).fetchall() == [(active.thread_id,)]
        finally:
            if current is not None:
                await current.aclose()
            await active.aclose()
            await archived.aclose()

    asyncio.run(scenario())


def test_archive_preserves_derived_inputs_and_unarchive_restarts_idle_window(tmp_path):
    async def scenario():
        source = await runtime_for(tmp_path)
        try:
            _ = [event async for event in source.stream("remember durable preference")]
            memory = SQLiteMemoryRepository(tmp_path / "history.db")
            claims = await memory.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=3600,
            )
            assert len(claims) == 1
            output = StageOneMemory(
                source.thread_id,
                tmp_path,
                claims[0].source_updated_at,
                "durable preference",
                "routing summary",
                "archive-test",
            )
            assert await memory.complete_extraction(claims[0], output)
            await source.archive()
            assert await memory.load_consolidation_inputs(limit=10, max_unused_days=30) == (output,)
            with sqlite3.connect(tmp_path / "history.db") as db:
                db.execute(
                    "UPDATE threads SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','-2 hours')"
                )
            await source.unarchive()
            assert not await memory.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=1,
                limit=1,
                lease_seconds=3600,
            )
            assert await memory.load_consolidation_inputs(limit=10, max_unused_days=30) == (output,)
        finally:
            await source.aclose()

    asyncio.run(scenario())
