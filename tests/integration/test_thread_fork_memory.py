"""Copied threads own independent memory claims, versions, and completion authority."""

import asyncio
from dataclasses import replace

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import UserMessageItem


def test_fork_memory_claim_does_not_inherit_parent_owner_or_watermark(tmp_path):
    async def scenario():
        database = tmp_path / "state.db"

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                model=Model(),
                database_path=database,
                home_path=tmp_path,
                **kwargs,
            )

        async def claim(repository):
            return await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=10,
                lease_seconds=60,
            )

        source = await create()
        try:
            assert isinstance([e async for e in source.stream("INHERITED")][-1], TurnCompleted)
            source_id = source.thread_id
            original = await source.load_display_snapshot()
        finally:
            await source.aclose()
        memories = SQLiteMemoryRepository(database)
        (parent_claim,) = await claim(memories)
        assert parent_claim.thread_id == source_id

        branch = await create(fork_from_thread_id=source_id)
        try:
            assert [e async for e in branch.resume_pending()] == []
            with branch._repository._connect() as connection:
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM memory_jobs WHERE job_key=?", (branch.thread_id,)
                    ).fetchone()[0]
                    == 0
                )
            other = SQLiteMemoryRepository(database)
            (child_claim,) = await claim(other)
            assert child_claim.thread_id == branch.thread_id
            assert child_claim.ownership_token != parent_claim.ownership_token
            assert [i.content for i in child_claim.items if isinstance(i, UserMessageItem)] == [
                "INHERITED"
            ]
            assert {i.id for i in child_claim.items}.isdisjoint(i.id for i in parent_claim.items)
            assert not await memories.complete_extraction(
                replace(parent_claim, thread_id=branch.thread_id),
                None,
            )
            assert not await claim(other)
            for owner in (child_claim, parent_claim):
                assert await other.complete_extraction(
                    owner,
                    StageOneMemory(
                        owner.thread_id,
                        tmp_path,
                        owner.source_updated_at,
                        "detail",
                        "summary",
                    ),
                )
            assert not await claim(SQLiteMemoryRepository(database))
            assert not await other.complete_extraction(child_claim, None)
            assert isinstance([e async for e in branch.stream("BRANCH_ONLY")][-1], TurnCompleted)
            (advanced,) = await claim(SQLiteMemoryRepository(database))
            assert advanced.thread_id == branch.thread_id
            assert advanced.source_updated_at != child_claim.source_updated_at
            assert advanced.ownership_token != child_claim.ownership_token
            assert [i.content for i in advanced.items if isinstance(i, UserMessageItem)] == [
                "INHERITED",
                "BRANCH_ONLY",
            ]
            assert not await other.complete_extraction(child_claim, None)
            assert await other.complete_extraction(advanced, None)
            assert not await claim(other)
            assert await branch._repository.load_display_snapshot(source_id) == original
        finally:
            await branch.aclose()

    asyncio.run(scenario())
