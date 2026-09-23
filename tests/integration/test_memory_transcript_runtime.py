"""Discovery, compaction and background extraction share a durable source archive."""

import asyncio
import json

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.context import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["manual_twice", "pre_turn", "mid_turn"])
def test_original_archive_survives_compaction_without_reextracting_retained_copies(tmp_path, mode):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        executions, main_requests, summaries = [], [], []

        class Fact:
            spec = ToolSpec(
                "fact",
                "Read verification fact",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED,
            )

            async def execute(self, call, context):
                executions.append(call.id)
                return ToolResult(
                    call.id,
                    call.name,
                    "FACT OBSERVATION" + ("x" * 16000 if mode == "mid_turn" else ""),
                )

        class SourceModel:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == "SUMMARIZE ARCHIVE"
                ):
                    summaries.append(request)
                    assert not request.tools
                    item = AssistantMessageItem("COMPACTION ONLY", turn, step)
                else:
                    main_requests.append(request)
                    if len(main_requests) == 1:
                        assert "fact" not in {tool.name for tool in request.tools}
                        item = ToolCallItem(
                            ToolCall(
                                new_tool_call_id(), "tool_search", {"query": "verification fact"}
                            ),
                            turn,
                            step,
                        )
                    elif len(main_requests) == 2:
                        assert "fact" in {tool.name for tool in request.tools}
                        item = ToolCallItem(ToolCall(new_tool_call_id(), "fact", {}), turn, step)
                    else:
                        item = AssistantMessageItem(
                            "done"
                            + (
                                "x" * 16000
                                if mode == "pre_turn" and len(main_requests) == 3
                                else ""
                            ),
                            turn,
                            step,
                            phase="final_answer",
                        )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Fact())
        source = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=False,
                context_window_tokens=12000,
                auto_compact_tokens=4500,
                compact_prompt="SUMMARIZE ARCHIVE",
            ),
            database_path=database,
            model=SourceModel(),
            registry=registry,
            home_path=tmp_path / "home",
        )
        try:
            first = [event async for event in source.stream("ORIGINAL REQUEST")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            if mode == "manual_twice":
                for _ in range(2):
                    events = [event async for event in source.compact()]
                    assert isinstance(events[-1], TurnCompleted), events[-1]
            second = [event async for event in source.stream("NEW REQUEST")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert len(summaries) == (2 if mode == "manual_twice" else 1)
            assert sum(isinstance(e, ContextCompacted) for e in first) == (mode == "mid_turn")
            assert sum(isinstance(e, ContextCompacted) for e in second) == (mode == "pre_turn")
            history = await source._repository.load_items(source.thread_id)
            users = [item for item in history if isinstance(item, UserMessageItem)]
            originals = [item for item in users if item.retained_from_id is None]
            copies = [item for item in users if item.retained_from_id is not None]
            assert [item.content for item in originals] == ["ORIGINAL REQUEST", "NEW REQUEST"]
            assert len(copies) == len(summaries)
            assert all(
                item.retained_from_id == originals[0].id and item.id != originals[0].id
                for item in copies
            )
            assert {
                item.content
                for item in active_history(history)
                if isinstance(item, UserMessageItem)
            } == {"ORIGINAL REQUEST", "NEW REQUEST"}
            assert any(isinstance(item, CompactionItem) for item in main_requests[-1].items)
            assert sum(isinstance(item, ToolCallItem) for item in history) == 2
            assert sum(isinstance(item, ToolResultItem) for item in history) == 2
            assert len(executions) == 1
            source_id = source.thread_id
        finally:
            await source.aclose()

        class MemoryModel:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                assert bool(request.tools) == (request.output_schema is None)
                if len(self.requests) == 1:
                    transcript = (
                        request.items[0]
                        .content.split("<historical_transcript>\n", 1)[1]
                        .split("\n</historical_transcript>", 1)[0]
                    )
                    rows = json.loads(transcript)
                    assert [row["content"] for row in rows if row["type"] == "user_message"] == [
                        "ORIGINAL REQUEST",
                        "NEW REQUEST",
                    ]
                    calls = [row["call"] for row in rows if row["type"] == "tool_call"]
                    results = [row for row in rows if row["type"] == "tool_result"]
                    assert [call["id"] for call in calls] == [
                        result["call_id"] for result in results
                    ]
                    assert results[0]["discovered_tools"][0]["name"] == "fact"
                    assert results[1]["content"].startswith("FACT OBSERVATION")
                    assert all(
                        row["phase"] == "final_answer"
                        for row in rows
                        if row["type"] == "assistant_message"
                    )
                    assert "COMPACTION ONLY" not in transcript
                    assert not {"context", "compaction", "reasoning"}.intersection(
                        row["type"] for row in rows
                    )
                    value = {
                        "raw_memory": "verification fact",
                        "rollout_summary": "fact workflow",
                        "rollout_slug": "facts",
                    }
                else:
                    assert "verification fact" in inspect_worker_evidence(request)["raw_memories"]
                    value = {
                        "memory": "verification fact",
                        "memory_summary": "Facts index",
                        "skills": [],
                    }
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(value), request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

        class ReadyModel:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        memory_model = MemoryModel()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            model=ReadyModel(),
            memory_model=memory_model,
            memory_root=root,
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance(
                [event async for event in runtime.stream("initialize")][-1], TurnCompleted
            )
            report = await runtime._memory_service.wait()
            assert report.extracted == 1 and report.failed == 0 and report.consolidated, (
                runtime._memory_service.warnings
            )
            assert len(memory_model.requests) == 2
            rows = await runtime._memory_repository.load_consolidation_inputs(
                limit=10, max_unused_days=30
            )
            assert len(rows) == 1 and rows[0].thread_id == source_id
            assert "verification fact" in (root / "MEMORY.md").read_text()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
