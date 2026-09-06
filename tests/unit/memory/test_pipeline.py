from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from corki.config import CorkiSettings
from corki.memory import LocalMemoryBackend, LongTermMemoryService, SQLiteMemoryRepository
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


class ScriptedMemoryModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        response = self.responses.pop(0)
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    response,
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )

    async def aclose(self) -> None:
        self.closed = True


async def _seed_thread(database: Path, workspace: Path) -> tuple[object, object]:
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()
    await sessions.create_thread(thread_id, workspace)
    await sessions.save_turn(
        TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "remember command", "use pytest")
    )
    await sessions.append_items(
        thread_id,
        (
            UserMessageItem("remember command", turn_id),
            AssistantMessageItem("use pytest", turn_id, new_step_id()),
        ),
    )
    return thread_id, turn_id


def test_two_phase_pipeline_extracts_consolidates_redacts_and_skips_unchanged(
    tmp_path: Path,
) -> None:
    database = tmp_path / "sessions.db"
    root = tmp_path / "memories"

    async def scenario() -> None:
        old_thread, _ = await _seed_thread(database, tmp_path)
        model = ScriptedMemoryModel(
            [
                json.dumps(
                    {
                        "raw_memory": "Run pytest. api_key=super-secret-value",
                        "rollout_summary": "pytest verifies this project",
                        "rollout_slug": "Pytest Workflow",
                    }
                ),
                json.dumps(
                    {
                        "memory": f"# Testing\nRun pytest.\nthread_id: {old_thread}",
                        "memory_summary": "## Project\nTesting: pytest",
                        "skills": [
                            {
                                "name": "verify-project",
                                "description": "Verify this project",
                                "content": "Run pytest and inspect failures.",
                            }
                        ],
                    }
                ),
            ]
        )
        settings = CorkiSettings(
            working_directory=tmp_path,
            memories_enabled=True,
            memories_min_thread_idle_hours=0,
            memories_retry_delay_seconds=1,
        )
        repository = SQLiteMemoryRepository(database)
        service = LongTermMemoryService(
            settings=settings,
            repository=repository,
            model=model,
            root=root,
        )
        first = await service.run_once(new_thread_id())
        assert first.claimed == 1
        assert first.extracted == 1
        assert first.consolidated
        assert "[REDACTED]" in (root / "raw_memories.md").read_text(encoding="utf-8")
        assert "super-secret-value" not in (root / "raw_memories.md").read_text(encoding="utf-8")
        assert (root / "MEMORY.md").read_text(encoding="utf-8").startswith("v1\n")
        assert (root / "memory_summary.md").read_text(encoding="utf-8").startswith("v1\n")
        assert (root / "skills" / "verify-project" / "SKILL.md").is_file()
        assert model.requests[0].output_schema_name == "corki_memory_extraction"
        assert model.requests[0].output_schema is not None
        assert model.requests[0].output_schema["additionalProperties"] is False
        assert model.requests[1].output_schema_name == "corki_memory_consolidation"
        assert model.requests[1].output_schema is not None
        assert set(json.loads(model.requests[1].items[0].content)) == {
            "previous_memory",
            "previous_summary",
            "raw_memories",
            "ad_hoc_notes",
        }

        second = await service.run_once(new_thread_id())
        assert second.claimed == 0
        assert second.consolidation_skipped
        assert len(model.requests) == 2
        await service.aclose()

    asyncio.run(scenario())


def test_failed_consolidation_preserves_previously_published_memory(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    root = tmp_path / "memories"

    async def scenario() -> None:
        await _seed_thread(database, tmp_path)
        root.mkdir(parents=True)
        (root / "MEMORY.md").write_text("v1\n\nold memory\n", encoding="utf-8")
        (root / "memory_summary.md").write_text("v1\n\nold summary\n", encoding="utf-8")
        model = ScriptedMemoryModel(
            [
                json.dumps(
                    {
                        "raw_memory": "new detail",
                        "rollout_summary": "new summary",
                        "rollout_slug": None,
                    }
                ),
                "not-json",
            ]
        )
        settings = CorkiSettings(
            working_directory=tmp_path,
            memories_enabled=True,
            memories_min_thread_idle_hours=0,
            memories_retry_delay_seconds=60,
        )
        service = LongTermMemoryService(
            settings=settings,
            repository=SQLiteMemoryRepository(database),
            model=model,
            root=root,
        )
        report = await service.run_once(new_thread_id())
        assert report.failed == 1
        assert not report.consolidated
        assert (root / "MEMORY.md").read_text(encoding="utf-8") == "v1\n\nold memory\n"
        assert (root / "memory_summary.md").read_text(encoding="utf-8") == ("v1\n\nold summary\n")
        assert service.warnings

    asyncio.run(scenario())


def test_empty_extraction_is_completed_without_running_consolidation(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"

    async def scenario() -> None:
        await _seed_thread(database, tmp_path)
        model = ScriptedMemoryModel(
            [json.dumps({"raw_memory": "", "rollout_summary": "", "rollout_slug": None})]
        )
        service = LongTermMemoryService(
            settings=CorkiSettings(
                working_directory=tmp_path,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            repository=SQLiteMemoryRepository(database),
            model=model,
            root=tmp_path / "memories",
        )

        first = await service.run_once(new_thread_id())
        second = await service.run_once(new_thread_id())

        assert first.empty == 1
        assert not first.consolidated
        assert second.claimed == 0
        assert len(model.requests) == 1
        await service.aclose()

    asyncio.run(scenario())


def test_ad_hoc_note_alone_triggers_global_consolidation(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    SQLiteSessionRepository(database)
    root = tmp_path / "memories"
    LocalMemoryBackend(root).add_note(
        "2026-09-06T10-00-00-prefer-brief.md", "Prefer concise explanations."
    )

    async def scenario() -> None:
        model = ScriptedMemoryModel(
            [
                json.dumps(
                    {
                        "memory": "# Preferences\nPrefer concise explanations.",
                        "memory_summary": "## Preferences\nconcise answers",
                        "skills": [],
                    }
                )
            ]
        )
        service = LongTermMemoryService(
            settings=CorkiSettings(working_directory=tmp_path, memories_enabled=True),
            repository=SQLiteMemoryRepository(database),
            model=model,
            root=root,
        )

        report = await service.run_once(new_thread_id())

        assert report.claimed == 0
        assert report.consolidated
        assert "Prefer concise explanations" in model.requests[0].items[0].content
        assert "concise answers" in (root / "memory_summary.md").read_text(encoding="utf-8")
        await service.aclose()

    asyncio.run(scenario())
