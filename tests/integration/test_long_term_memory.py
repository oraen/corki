from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolContext, ToolRegistry


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


class ExternalMCPTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mcp__docs__search",
            description="Return external documentation.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        return ToolResult(call.id, call.name, "external result")


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
    assert {"memory_add_note", "memory_list", "memory_read", "memory_search"}.issubset(
        {spec.name for spec in registry.specs()}
    )


def test_external_mcp_use_marks_thread_ineligible_for_memory_generation(tmp_path: Path) -> None:
    database = tmp_path / "sessions.db"
    sessions = SQLiteSessionRepository(database)
    memories = SQLiteMemoryRepository(database)
    registry = ToolRegistry()
    registry.register(ExternalMCPTool())
    model = RuntimeModel([("tool", "mcp__docs__search"), ("answer", "used external docs")])
    settings = CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        memories_enabled=True,
        memories_generate=False,
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
        assert events[-1].final_answer == "Use the durable command."
        answer = next(item for item in items if isinstance(item, AssistantMessageItem))
        assert answer.content == "Use the durable command."
        assert answer.memory_citation is not None
        assert answer.memory_citation.thread_ids == (source_thread,)
        assert selected[0].usage_count == 1

    asyncio.run(scenario())
