"""Real local compaction remains reconstructible across copied and resumed forks."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context.history import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    UserMessageItem,
    new_step_id,
)
from corki.tools import ToolRegistry


@pytest.mark.parametrize("rounds", [1, 2])
def test_real_compaction_survives_fork_and_cold_resume(tmp_path, rounds):
    async def scenario():
        requests, summaries = [], []
        prompt = "SUMMARIZE_FORK_TEST"

        class Model:
            async def stream(self, request):
                user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
                if user.content == prompt:
                    summaries.append(request)
                    answer = f"SUMMARY_{len(summaries)}"
                else:
                    requests.append(request)
                    answer = f"ANSWER:{user.content}"
                yield ModelCompleted((AssistantMessageItem(answer, user.turn_id, new_step_id()),))

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    compact_prompt=prompt,
                ),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create()
        try:
            for n in range(rounds):
                assert isinstance([e async for e in source.stream(f"INPUT_{n}")][-1], TurnCompleted)
                assert isinstance([e async for e in source.compact()][-1], TurnCompleted)
            original = await source.load_display_snapshot()
            source_id = source.thread_id
            assert len(summaries) == rounds
            assert sum(isinstance(i, CompactionItem) for i in original.items) == rounds
        finally:
            await source.aclose()

        requests.clear()
        fork = await create(fork_from_thread_id=source_id)
        try:
            assert [e async for e in fork.resume_pending()] == []
            copied = await fork.load_display_snapshot()
            assert len(copied.items) == len(original.items)
            mapping = {
                old.id: new.id for old, new in zip(original.items, copied.items, strict=True)
            }
            assert set(mapping).isdisjoint(mapping.values())
            groups = {}
            for old, new in zip(original.items, copied.items, strict=True):
                assert type(old) is type(new)
                if isinstance(old, UserMessageItem):
                    assert new.content == old.content
                    assert new.retained_from_id == mapping.get(old.retained_from_id)
                if isinstance(old, CompactionItem):
                    assert new.through_item_id == mapping[old.through_item_id]
                    assert (new.summary, new.replacement_item_count, new.summary_insert_index) == (
                        old.summary,
                        old.replacement_item_count,
                        old.summary_insert_index,
                    )
                if isinstance(old, ContextItem):
                    assert (new.key, new.content, new.snapshot_content, new.snapshot_state) == (
                        old.key,
                        old.content,
                        old.snapshot_content,
                        old.snapshot_state,
                    )
                    assert new.source_input_id == mapping.get(old.source_input_id)
                    if old.message_group_id is not None:
                        assert new.message_group_id != old.message_group_id
                        assert groups.setdefault(old.message_group_id, new.message_group_id) == (
                            new.message_group_id
                        )
                        assert (new.message_group_index, new.message_group_size) == (
                            old.message_group_index,
                            old.message_group_size,
                        )
            visible = active_history(copied.items)
            assert [i.summary for i in visible if isinstance(i, CompactionItem)] == [
                f"SUMMARY_{rounds}"
            ]
            assert isinstance([e async for e in fork.stream("BRANCH")][-1], TurnCompleted)
            assert len(requests) == 1 and len(summaries) == rounds
            assert f"SUMMARY_{rounds}" in str(requests[-1].items)
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                *[f"INPUT_{n}" for n in range(rounds)],
                "BRANCH",
            ]
            assert await fork._repository.load_display_snapshot(source_id) == original
            assert isinstance([e async for e in fork.compact()][-1], TurnCompleted)
            assert len(summaries) == rounds + 1
            fork_id = fork.thread_id
        finally:
            await fork.aclose()

        cold = await create(thread_id=fork_id)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert isinstance([e async for e in cold.stream("COLD")][-1], TurnCompleted)
            assert len(requests) == 2 and len(summaries) == rounds + 1
            assert f"SUMMARY_{rounds + 1}" in str(requests[-1].items)
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                *[f"INPUT_{n}" for n in range(rounds)],
                "BRANCH",
                "COLD",
            ]
            assert await cold._repository.load_display_snapshot(source_id) == original
        finally:
            await cold.aclose()

    asyncio.run(scenario())
