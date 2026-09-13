"""Actual Runtime requests exercise memory text, retrieval, and failure boundaries."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import LocalMemoryBackend, SQLiteMemoryRepository, StageOneMemory
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("cite", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
def test_retrieval_does_not_count_as_citation_usage(tmp_path, cite, streamed):
    async def scenario():
        database = tmp_path / "sessions.db"
        sessions = SQLiteSessionRepository(database)
        memories = SQLiteMemoryRepository(database)
        source, source_turn = new_thread_id(), new_turn_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.save_turn(
            TurnRecord(source_turn, source, TurnStatus.COMPLETED, "question", "answer")
        )
        await sessions.append_items(source, (UserMessageItem("question", source_turn),))
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
            claim, StageOneMemory(source, tmp_path, claim.source_updated_at, "fact", "summary")
        )
        await sessions.close()
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "memory_summary.md").write_text("Read MEMORY.md for the project command.")
        (root / "MEMORY.md").write_text(f"project command from {source}\n")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                if index in (2, 3):
                    observation = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert not observation.is_error and str(source) in observation.content
                    rows = await memories.load_consolidation_inputs(limit=10, max_unused_days=30)
                    assert len(rows) == 1 and (rows[0].usage_count or 0) == 0
                if index <= 2:
                    name, arguments = (
                        ("memories::search", {"queries": ["project command"]})
                        if index == 1
                        else ("memories::read", {"path": "MEMORY.md"})
                    )
                    item = ToolCallItem(ToolCall(new_tool_call_id(), name, arguments), turn, step)
                else:
                    answer = "Use the command." if index == 3 else "After reopen."
                    if cite and index == 3:
                        answer += (
                            "\n<oai-mem-citation>\n<citation_entries>\n"
                            "MEMORY.md:1-1|note=[command]\n</citation_entries>\n"
                            f"<rollout_ids>\n{source}\n</rollout_ids>\n</oai-mem-citation>"
                        )
                    item = AssistantMessageItem(answer, turn, step)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    memories_enabled=True,
                    memories_generate=False,
                    memories_background_enabled=False,
                    memories_dedicated_tools=True,
                ),
                database_path=database,
                home_path=tmp_path / "home",
                memory_root=root,
                memory_repository=SQLiteMemoryRepository(database),
                model=Model(),
                thread_id=thread,
            )

        runtime = await create()
        try:
            events = [event async for event in runtime.stream("Recall the command")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
            thread = runtime.thread_id
            archive = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            assert isinstance([event async for event in cold.stream("Next")][-1], TurnCompleted)
            assert len(requests) == 4
            assert (await cold._repository.load_items(thread))[: len(archive)] == archive
            rows = await memories.load_consolidation_inputs(limit=10, max_unused_days=30)
            assert len(rows) == 1 and (rows[0].usage_count or 0) == int(cite)
        finally:
            await cold.aclose()
            await memories.close()

    asyncio.run(scenario())


def test_summary_search_read_and_rejected_searches_reach_the_model(tmp_path):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        original = "HEAD\r\nmy/project.name-v2\r\nTAIL"
        (root / "MEMORY.md").write_bytes(original.encode())
        (root / "memory_summary.md").write_text(
            "INDEXHEAD " + "padding " * 100 + " TAIL:MEMORY.md", encoding="utf-8"
        )
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("PRIVATE_EXTERNAL_CONTENT", encoding="utf-8")

        class Model:
            requests = []

            async def stream(self, request):
                self.requests.append(request)
                index = len(self.requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if index == 1:
                    fragment = next(
                        i
                        for i in request.items
                        if isinstance(i, ContextItem) and i.key == "memory.instructions"
                    )
                    assert "INDEXHEAD" in fragment.content and "TAIL:MEMORY.md" in fragment.content
                    assert (
                        "tokens truncated" in fragment.content
                        and "padding " * 100 not in fragment.content
                    )
                    name, args = (
                        "memories::search",
                        {"queries": ["my project_name v2"], "normalized": True},
                    )
                elif index == 2:
                    assert not results[-1].is_error
                    match = json.loads(results[-1].content)["matches"][0]
                    assert match["path"] == "MEMORY.md" and match["match_line_number"] == 2
                    name, args = (
                        "memories::read",
                        {"path": match["path"], "line_offset": 2, "max_lines": 1},
                    )
                elif index == 3:
                    assert json.loads(results[-1].content)["content"] == "my/project.name-v2\r\n"
                    name, args = "memories::read", {"path": "MEMORY.md", "max_tokens": 3}
                elif index == 4:
                    assert results[-1].is_error and "max_tokens" in results[-1].content
                    name, args = "memories::search", {"queries": ["---"], "normalized": True}
                elif index == 5:
                    assert results[-1].is_error and "empty" in results[-1].content
                    root.rename(tmp_path / "preserved-memory")
                    root.symlink_to(outside, target_is_directory=True)
                    name, args = "memories::search", {"queries": ["PRIVATE"]}
                else:
                    assert index == 6
                    assert results[-1].is_error and "symbolic link" in results[-1].content
                    assert "PRIVATE_EXTERNAL_CONTENT" not in str(request.items)
                    yield ModelCompleted((AssistantMessageItem("retrieval complete", turn, step),))
                    return
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step),)
                )

            async def aclose(self):
                pass

        model = Model()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
                memories_summary_token_limit=12,
            ),
            database_path=tmp_path / "sessions.db",
            memory_root=root,
            model=model,
        )
        try:
            assert isinstance(
                [e async for e in runtime.stream("recover the project command")][-1], TurnCompleted
            )
            assert len(model.requests) == 6
            stored = await runtime._repository.load_items(runtime.thread_id)
            observations = [i for i in stored if isinstance(i, ToolResultItem)]
            assert [i.is_error for i in observations] == [False, False, True, True, True]
            assert (tmp_path / "preserved-memory/MEMORY.md").read_bytes() == original.encode()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
