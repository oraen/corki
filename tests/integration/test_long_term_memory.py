from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.tools import MCPTool
from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


class RuntimeModel:
    def __init__(self, actions: list[tuple[str, object]]) -> None:
        self.actions = actions
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        kind, value = self.actions.pop(0)
        turn_id = request.items[-1].turn_id
        if kind == "tool":
            call = ToolCall(
                ToolCallId("memory-external-call"),
                str(value),
                {"query": "docs"},
                raw_arguments=json.dumps({"query": "docs"}),
            )
            yield ModelCompleted((ToolCallItem(call, turn_id, new_step_id()),))
        else:
            yield ModelCompleted((AssistantMessageItem(str(value), turn_id, new_step_id()),))

    async def aclose(self) -> None:
        return None


class ExternalMCPTool(MCPTool):
    def __init__(self):
        class Client:
            async def call_tool(self, name, arguments):
                return {"content": [{"type": "text", "text": "external result"}]}

        super().__init__(
            "docs",
            {
                "name": "search",
                "description": "Return external documentation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            Client(),
        )


def test_custom_session_repository_requires_matching_memory_repository(tmp_path: Path) -> None:
    settings = CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        memories_enabled=True,
    )

    with pytest.raises(ValueError, match="explicit memory_repository"):
        LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "sessions.db",
            repository=object(),  # type: ignore[arg-type]
            model=RuntimeModel([]),
            registry=ToolRegistry(),
        )


def test_runtime_injects_summary_and_registers_optional_memory_tools(tmp_path: Path) -> None:
    memory_root = tmp_path / "memories"
    memory_root.mkdir()
    (memory_root / "memory_summary.md").write_text(
        "v1\n\nRemember the alpha repository convention.", encoding="utf-8"
    )
    registry = ToolRegistry()
    model = RuntimeModel([("answer", "done")])
    settings = CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        memories_enabled=True,
        memories_generate=False,
        memories_background_enabled=False,
        memories_dedicated_tools=True,
    )
    runtime = LangGraphRuntime.create(
        settings=settings,
        database_path=tmp_path / "sessions.db",
        model=model,
        registry=registry,
        memory_root=memory_root,
    )

    async def scenario() -> list[object]:
        events = [event async for event in runtime.stream("follow our convention")]
        await runtime.aclose()
        return events

    events = asyncio.run(scenario())
    assert isinstance(events[-1], TurnCompleted)
    memory_context = [
        item
        for item in model.requests[0].items
        if isinstance(item, ContextItem) and item.key == "memory.instructions"
    ]
    assert len(memory_context) == 1
    assert "alpha repository convention" in memory_context[0].content
    assert {
        "memories::add_ad_hoc_note",
        "memories::list",
        "memories::read",
        "memories::search",
    }.issubset({spec.name for spec in registry.specs()})


def test_external_mcp_use_marks_thread_ineligible_for_memory_generation(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    sessions = SQLiteSessionRepository(database)
    memories = SQLiteMemoryRepository(database)
    registry = ToolRegistry()
    registry.register(ExternalMCPTool())
    model = RuntimeModel([("tool", "mcp__docs::search"), ("answer", "used external docs")])
    settings = CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        memories_enabled=True,
        memories_generate=False,
        memories_background_enabled=False,
        memories_use=False,
        memories_disable_on_external_context=True,
    )
    runtime = LangGraphRuntime.create(
        settings=settings,
        database_path=database,
        repository=sessions,
        memory_repository=memories,
        model=model,
        registry=registry,
        memory_root=tmp_path / "memories",
    )
    thread_id = runtime.thread_id

    async def scenario() -> tuple[object, ...]:
        events = tuple([event async for event in runtime.stream("use external docs")])
        claims = await memories.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=10,
            min_idle_hours=0,
            limit=2,
            lease_seconds=60,
        )
        await runtime.aclose()
        return events, claims

    events, claims = asyncio.run(scenario())
    assert isinstance(events[-1], TurnCompleted)
    assert not claims
    assert thread_id != new_thread_id()


def test_polluted_published_source_enqueues_and_cold_runtime_rebuilds_memory(tmp_path):
    import sqlite3
    from datetime import UTC, datetime

    from corki.memory.artifacts import sync_stage_one_artifacts, write_consolidated_artifacts
    from corki.memory.models import ConsolidatedMemory

    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        memories = SQLiteMemoryRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        version = datetime.now(UTC).isoformat()
        memory = StageOneMemory(source, tmp_path, version, "RETRACT_THIS_SOURCE", "Old evidence")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO memory_stage1_outputs(thread_id,cwd,source_updated_at,"
                "raw_memory,rollout_summary) VALUES (?,?,?,?,?)",
                (source, str(tmp_path), version, memory.raw_memory, memory.rollout_summary),
            )
        sync_stage_one_artifacts(root, (memory,))
        write_consolidated_artifacts(
            root, ConsolidatedMemory("RETRACT_THIS_SOURCE", "Old index", ())
        )
        claim = await memories.claim_consolidation(lease_seconds=60)
        assert claim is not None and await memories.complete_consolidation(claim, (memory,))
        registry = ToolRegistry()
        registry.register(ExternalMCPTool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_disable_on_external_context=True,
            ),
            database_path=database,
            thread_id=source,
            model=RuntimeModel([("tool", "mcp__docs::search"), ("answer", "done")]),
            registry=registry,
            memory_root=root,
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("read external docs")][-1], TurnCompleted
            )
            with sqlite3.connect(database) as connection:
                state = connection.execute(
                    "SELECT status,input_watermark,completed_watermark "
                    "FROM memory_jobs WHERE job_key='global'"
                ).fetchone()
                assert state[0] == "pending" and state[1] > state[2]
                assert (
                    connection.execute(
                        "SELECT memory_mode FROM threads WHERE id=?", (source,)
                    ).fetchone()[0]
                    == "polluted"
                )
        finally:
            await runtime.aclose()
        with sqlite3.connect(database) as connection:
            # Next eligible startup, without changing the production cooldown policy.
            connection.execute("UPDATE memory_jobs SET finished_at=0 WHERE job_key='global'")

        class MemoryModel:
            count = 0

            async def stream(self, request):
                self.count += 1
                assert request.tools and request.output_schema is None
                data = inspect_worker_evidence(request)
                assert "RETRACT_THIS_SOURCE" in data["previous_memory"]
                assert "RETRACT_THIS_SOURCE" not in data["raw_memories"]
                assert not tuple((root / "rollout_summaries").glob("*.md"))
                value = {
                    "memory": "No supported evidence remains.",
                    "memory_summary": "REBUILT_INDEX",
                    "skills": [],
                }
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

        main, background = (
            RuntimeModel([("answer", "ready"), ("answer", "summary"), ("answer", "recalled")]),
            MemoryModel(),
        )
        cold = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=database,
            model=main,
            memory_model=background,
            memory_root=root,
        )
        try:
            assert isinstance([e async for e in cold.stream("initialize")][-1], TurnCompleted)
            report = await cold._memory_service.wait()
            assert report.claimed == 0 and report.consolidated and not report.failed
            prefix = await cold._repository.load_items(cold.thread_id)
            assert isinstance([e async for e in cold.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in cold.stream("recall")][-1], TurnCompleted)
            assert (await cold._repository.load_items(cold.thread_id))[: len(prefix)] == prefix
            index = next(
                i
                for i in reversed(main.requests[-1].items)
                if isinstance(i, ContextItem) and i.key == "memory.instructions"
            )
            assert "REBUILT_INDEX" in index.content and background.count == 1
            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute(
                        "SELECT selected_for_phase2 FROM memory_stage1_outputs WHERE thread_id=?",
                        (source,),
                    ).fetchone()[0]
                    == 0
                )
            assert await sessions.load_items(source), "pollution is not archive erasure"
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_memory_citation_is_hidden_persisted_and_updates_usage(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    sessions = SQLiteSessionRepository(database)

    async def scenario() -> None:
        source_thread = new_thread_id()
        source_turn = new_turn_id()
        await sessions.create_thread(source_thread, tmp_path)
        await sessions.save_turn(
            TurnRecord(
                source_turn,
                source_thread,
                TurnStatus.COMPLETED,
                "old question",
                "old answer",
            )
        )
        await sessions.append_items(source_thread, (UserMessageItem("old question", source_turn),))
        memories = SQLiteMemoryRepository(database)
        claim = (
            await memories.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=60,
            )
        )[0]
        assert await memories.complete_extraction(
            claim,
            StageOneMemory(
                source_thread,
                tmp_path,
                claim.source_updated_at,
                "durable command",
                "command summary",
            ),
        )
        response = f"""Use the durable command.
<corki-memory-citation>
<citation_entries>
MEMORY.md:2-3|note=[durable command]
</citation_entries>
<thread_ids>
{source_thread}
</thread_ids>
</corki-memory-citation>"""
        model = RuntimeModel([("answer", response)])
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_use=False,
            ),
            database_path=database,
            repository=sessions,
            memory_repository=memories,
            model=model,
            memory_root=tmp_path / "memories",
        )
        events = tuple([event async for event in runtime.stream("recall it")])
        items = await sessions.load_items(runtime.thread_id)
        selected = await memories.load_consolidation_inputs(limit=10, max_unused_days=30)
        await runtime.aclose()

        assert isinstance(events[-1], TurnCompleted)
        assert events[-1].final_answer == "Use the durable command.\n"
        answer = next(item for item in items if isinstance(item, AssistantMessageItem))
        assert answer.content == "Use the durable command.\n"
        assert answer.memory_citation is not None
        assert answer.memory_citation.thread_ids == (source_thread,)
        assert selected[0].usage_count == 1

    asyncio.run(scenario())
