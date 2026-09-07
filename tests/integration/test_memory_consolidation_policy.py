"""New/edited file-only workspaces and failed startup passes in the real Runtime."""

import asyncio
import json
import time

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id


class MainModel:
    async def stream(self, request):
        yield ModelCompleted(
            (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


@pytest.mark.parametrize("change", ["detail", "delete_note", "invalid_summary"])
def test_empty_database_initialization_cooldown_and_file_only_changes(
    tmp_path, monkeypatch, change
):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        clock = [time.time()]
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: clock[0])
        current = new_thread_id()

        class MemoryModel:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                assert not request.tools
                payload = json.loads(request.items[0].content)
                assert "No raw memories yet." in payload["raw_memories"]
                if len(self.requests) == 2 and change == "detail":
                    assert "edited detail" in payload["previous_memory"]
                value = {
                    "memory": f"memory {len(self.requests)}",
                    "memory_summary": "index",
                    "skills": [],
                }
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

        model = MemoryModel()

        async def run():
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, memories_enabled=True
                ),
                database_path=database,
                thread_id=current,
                model=MainModel(),
                memory_model=model,
                memory_root=root,
            )
            try:
                events = [event async for event in runtime.stream("work")]
                assert isinstance(events[-1], TurnCompleted)
                report = await runtime._memory_service.wait()
                assert report.claimed == 0 and report.failed == 0
                return report
            finally:
                await runtime.aclose()

        if change == "delete_note":
            note = root / "extensions/ad_hoc/notes/note.md"
            note.parent.mkdir(parents=True)
            note.write_text("Preference to reconsider.", encoding="utf-8")
        assert (await run()).consolidated
        if change == "detail":
            (root / "MEMORY.md").write_text("v1\n\nedited detail", encoding="utf-8")
        elif change == "delete_note":
            note.unlink()
        else:
            (root / "memory_summary.md").write_text("invalid summary", encoding="utf-8")
        assert not (await run()).consolidated
        assert len(model.requests) == 1
        clock[0] += 6 * 3600
        assert (await run()).consolidated
        assert len(model.requests) == 2
        clock[0] += 6 * 3600
        assert (await run()).consolidation_skipped
        assert len(model.requests) == 2

    asyncio.run(scenario())


def test_repeated_startup_does_not_reenqueue_failed_unchanged_work(tmp_path, monkeypatch):
    async def scenario():
        clock = [time.time()]
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: clock[0])
        current = new_thread_id()
        root = tmp_path / "memories"
        note = root / "extensions/ad_hoc/notes/note.md"
        note.parent.mkdir(parents=True)
        note.write_text("Keep this pending input.", encoding="utf-8")

        class BrokenMemoryModel:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                raise OSError("injected consolidation failure")
                yield

        model = BrokenMemoryModel()
        for index in range(3):
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    memories_enabled=True,
                    memories_retry_delay_seconds=60,
                ),
                database_path=tmp_path / "sessions.db",
                thread_id=current,
                model=MainModel(),
                memory_model=model,
                memory_root=root,
            )
            try:
                assert isinstance(
                    [event async for event in runtime.stream("work")][-1], TurnCompleted
                )
                report = await runtime._memory_service.wait()
                assert report.failed == (0 if index == 1 else 1)
                assert model.calls == (2 if index == 2 else 1)
            finally:
                await runtime.aclose()
            if index == 1:
                clock[0] += 60

    asyncio.run(scenario())
