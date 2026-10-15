import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolContext, ToolRegistry


class RecallModel:
    """Deterministic model used to inspect complete runtime requests."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        turn_id = request.items[-1].turn_id
        yield ModelCompleted(
            (AssistantMessageItem(f"answer-{len(self.requests)}", turn_id, new_step_id()),)
        )

    async def aclose(self) -> None:
        return None


def test_runtime_recalls_context_skill_and_continuous_memory_in_codex_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='recall-demo'\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("ALWAYS_RUN_RECALL_CHECK", encoding="utf-8")
    skill_dir = tmp_path / ".corki" / "skills" / "demo-recall"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-recall\ndescription: Verify skill recall.\n---\n\nSKILL_BODY_RECALLED\n",
        encoding="utf-8",
    )
    model = RecallModel()
    database = tmp_path / "runtime" / "sessions.db"
    repository = SQLiteSessionRepository(database)
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path),
        database_path=database,
        home_path=tmp_path / ".corki",
        model=model,
        repository=repository,
    )

    async def scenario() -> tuple[tuple[object, ...], tuple[object, ...]]:
        first = tuple([event async for event in runtime.stream("use $demo-recall now")])
        second = tuple([event async for event in runtime.stream("remember the prior answer")])
        stored = await repository.load_items(runtime.thread_id)
        await runtime.aclose()
        return (*first, *second), stored

    events, stored = asyncio.run(scenario())

    assert sum(isinstance(event, TurnCompleted) for event in events) == 2
    assert len(model.requests) == 2
    first_items = model.requests[0].items
    first_user_index = next(
        index
        for index, item in enumerate(first_items)
        if isinstance(item, UserMessageItem) and item.content == "use $demo-recall now"
    )
    context_before_user = first_items[:first_user_index]
    assert context_before_user
    assert all(isinstance(item, ContextItem) for item in context_before_user)
    combined_context = "\n".join(item.content for item in context_before_user)
    assert "ALWAYS_RUN_RECALL_CHECK" in combined_context
    assert "SKILL_BODY_RECALLED" not in combined_context
    selected_skill = first_items[first_user_index + 1 :]
    assert len(selected_skill) == 1 and isinstance(selected_skill[0], ContextItem)
    assert "SKILL_BODY_RECALLED" in selected_skill[0].content
    assert selected_skill[0].source_input_id == first_items[first_user_index].id

    second_items = model.requests[1].items
    remembered = [
        item.content
        for item in second_items
        if isinstance(item, (UserMessageItem, AssistantMessageItem))
    ]
    assert remembered == [
        "use $demo-recall now",
        "answer-1",
        "remember the prior answer",
    ]
    stored_user_index = next(
        index
        for index, item in enumerate(stored)
        if isinstance(item, UserMessageItem) and item.content == "use $demo-recall now"
    )
    assert all(isinstance(item, ContextItem) for item in stored[:stored_user_index])


@dataclass
class LateTool:
    calls: int = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            "late_tool",
            "A tool registered after sampling began.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        self.calls += 1
        return ToolResult(call.id, call.name, "should not execute")


class RegistryMutatingModel:
    def __init__(self, registry: ToolRegistry, late_tool: LateTool) -> None:
        self.registry = registry
        self.late_tool = late_tool
        self.requests: list[ModelRequest] = []
        self.mutation_error: str | None = None

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        turn_id = request.items[-1].turn_id
        if len(self.requests) == 1:
            try:
                self.registry.register(self.late_tool)
            except RuntimeError as exc:
                self.mutation_error = str(exc)
            yield ModelCompleted(
                (
                    ToolCallItem(
                        ToolCall(ToolCallId("late-1"), "late_tool", {}),
                        turn_id,
                        new_step_id(),
                    ),
                )
            )
            return
        yield ModelCompleted((AssistantMessageItem("done", turn_id, new_step_id()),))

    async def aclose(self) -> None:
        return None


def test_runtime_seals_handlers_and_rejects_unadvertised_tool_call(tmp_path: Path) -> None:
    registry = ToolRegistry()
    late_tool = LateTool()
    model = RegistryMutatingModel(registry, late_tool)
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path),
        database_path=tmp_path / "sessions.db",
        registry=registry,
        model=model,
    )

    async def scenario() -> tuple[object, ...]:
        events = tuple([event async for event in runtime.stream("try a late tool")])
        await runtime.aclose()
        return events

    events = asyncio.run(scenario())

    assert isinstance(events[-1], TurnCompleted)
    assert model.mutation_error == "tool registry is sealed for runtime execution"
    assert late_tool.calls == 0
    assert "late_tool" not in {spec.name for spec in model.requests[0].tools}
    assert "late_tool" not in {spec.name for spec in model.requests[1].tools}
    rejected = [item for item in model.requests[1].items if isinstance(item, ToolResultItem)]
    assert len(rejected) == 1
    assert rejected[0].is_error
    assert "not advertised for this step" in rejected[0].content
