"""Usage facts survive copied snapshots without copying model execution records."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("before", [None, 0, 1])
def test_fork_usage_tracks_selected_history_and_is_superseded(tmp_path, before):
    async def scenario():
        samples, summaries = [], []

        class Model:
            async def stream(self, request):
                if getattr(request.items[-1], "content", "") == "FORK_USAGE_SUMMARY":
                    summaries.append(request)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "USAGE_TRIGGERED_SUMMARY",
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                samples.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("answer", request.items[-1].turn_id, new_step_id()),),
                    usage=ModelUsage(input_tokens=10000 * len(samples), output_tokens=10)
                    if len(samples) <= 2
                    else ModelUsage(),
                )

            async def aclose(self):
                pass

        async def create(auto_limit=None, **kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    compact_prompt="FORK_USAGE_SUMMARY",
                    **({"auto_compact_tokens": auto_limit} if auto_limit is not None else {}),
                ),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create()
        source_facts = []
        try:
            for text in ("ONE", "TWO"):
                assert isinstance([e async for e in source.stream(text)][-1], TurnCompleted)
                source_facts.append(await source._repository.load_context_usage(source.thread_id))
            source_id = source.thread_id
        finally:
            await source.aclose()

        fork = await create(fork_from_thread_id=source_id, fork_before_user_message=before)
        try:
            assert [e async for e in fork.resume_pending()] == []
            fact = await fork._repository.load_context_usage(fork.thread_id)
            if before == 0:
                assert fact is None
            else:
                expected = source_facts[0 if before == 1 else 1]
                assert fact is not None
                assert (fact.total_tokens, fact.input_tokens) == (
                    expected.total_tokens,
                    expected.input_tokens,
                )
                assert fact.anchor_id != expected.anchor_id and fact.sample_id != expected.sample_id
                assert fact.anchor_id in {i.id for i in await fork.load_display_history()}
            with fork._repository._connect() as connection:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM model_steps WHERE thread_id=?", (fork.thread_id,)
                    ).fetchone()[0]
                    == 0
                )
            fork_id = fork.thread_id
        finally:
            await fork.aclose()

        cold = await create(thread_id=fork_id, auto_limit=15000 if before is None else None)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert await cold._repository.load_context_usage(cold.thread_id) == fact
            if before is None:
                child = await create(fork_from_thread_id=fork_id, fork_before_user_message=1)
                try:
                    assert [e async for e in child.resume_pending()] == []
                    child_fact = await child._repository.load_context_usage(child.thread_id)
                    assert child_fact.total_tokens == source_facts[0].total_tokens
                finally:
                    await child.aclose()
            assert len(samples) == 2
            assert isinstance([e async for e in cold.stream("NEW")][-1], TurnCompleted)
            assert len(summaries) == int(before is None)
            if before is None:
                assert "USAGE_TRIGGERED_SUMMARY" in str(samples[-1].items)
            # A newer response with unknown usage must not resurrect old inherited usage.
            assert await cold._repository.load_context_usage(cold.thread_id) is None
        finally:
            await cold.aclose()

    asyncio.run(scenario())
