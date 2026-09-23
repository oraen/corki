"""A newer empty extraction invalidates prior inputs through the real Runtime."""

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
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize(
    "raw,summary", [("", ""), ("", "discarded summary"), ("discarded detail", "")]
)
def test_new_empty_source_retracts_old_inputs_then_reopen_deduplicates(tmp_path, raw, summary):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source, current, turn = new_thread_id(), new_thread_id(), new_turn_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn, source, TurnStatus.COMPLETED, "preference", "old")
        )
        await sessions.append_items(source, (UserMessageItem("Old preference.", turn),))
        version = datetime.now(UTC) - timedelta(hours=1)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE threads SET updated_at=? WHERE id=?", (version.isoformat(), source)
            )

        class MemoryModel:
            requests = []
            closed = 0

            async def stream(self, request):
                self.requests.append(request)
                assert bool(request.tools) == (request.output_schema is None)
                index = len(self.requests)
                if index == 1:
                    value = {
                        "raw_memory": "Old preference.",
                        "rollout_summary": "Old index.",
                        "rollout_slug": "old",
                    }
                elif index == 2:
                    value = {
                        "memory": "Old preference.",
                        "memory_summary": "Old route.",
                        "skills": [],
                    }
                elif index == 3:
                    value = {"raw_memory": raw, "rollout_summary": summary, "rollout_slug": None}
                elif index == 4:
                    inputs = inspect_worker_evidence(request)
                    assert "Old preference." in inputs["previous_memory"]
                    assert "Old preference." not in inputs["raw_memories"]
                    assert not tuple((root / "rollout_summaries").glob("*.md"))
                    value = {
                        "memory": "No supported preferences remain.",
                        "memory_summary": "No preferences indexed.",
                        "skills": [],
                    }
                else:
                    pytest.fail("unchanged source or workspace sampled again")
                try:
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                json.dumps(value), request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                finally:
                    self.closed += 1

        class MainModel:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        memory_model = MemoryModel()
        for index in range(3):
            if index:
                # This scenario exercises publication on the next eligible startup pass.
                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "UPDATE memory_jobs SET finished_at=0 WHERE job_key='global'"
                    )
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    memories_enabled=True,
                    memories_min_thread_idle_hours=0,
                ),
                database_path=database,
                thread_id=current,
                model=MainModel(),
                memory_model=memory_model,
                memory_root=root,
            )
            try:
                events = [event async for event in runtime.stream("continue")]
                assert isinstance(events[-1], TurnCompleted)
                report = await runtime._memory_service.wait()
                assert not report.failed and not runtime._memory_service.warnings
                if index == 0:
                    assert report.extracted == 1 and report.consolidated
                    assert "Old preference." in (root / "MEMORY.md").read_text()
                    with sqlite3.connect(database) as connection:
                        connection.execute(
                            "UPDATE threads SET updated_at=? WHERE id=?",
                            ((version + timedelta(seconds=1)).isoformat(), source),
                        )
                else:
                    assert report.empty == (1 if index == 1 else 0)
                    assert report.claimed == (1 if index == 1 else 0)
                    assert "Old preference." not in (root / "MEMORY.md").read_text()
                    assert not await runtime._memory_repository.load_consolidation_inputs(
                        limit=10, max_unused_days=30
                    )
                    assert not tuple((root / "rollout_summaries").glob("*.md"))
            finally:
                await runtime.aclose()
        assert len(memory_model.requests) == memory_model.closed == 4

    asyncio.run(scenario())
