import asyncio
from pathlib import Path

from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


def test_memory_jobs_are_leased_idempotent_and_consolidated_once(tmp_path: Path) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    old_thread = new_thread_id()
    current_thread = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(old_thread, tmp_path)
        await sessions.create_thread(current_thread, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, old_thread, TurnStatus.COMPLETED, "question", "answer")
        )
        await sessions.append_items(
            old_thread,
            (
                UserMessageItem("question", turn_id),
                AssistantMessageItem("answer", turn_id, new_step_id()),
            ),
        )

        first = SQLiteMemoryRepository(database)
        second = SQLiteMemoryRepository(database)
        claims = await first.claim_extraction_jobs(
            current_thread_id=current_thread,
            max_age_days=10,
            min_idle_hours=0,
            limit=2,
            lease_seconds=3_600,
        )
        assert len(claims) == 1
        assert claims[0].thread_id == old_thread
        assert len(claims[0].items) == 2
        assert not await second.claim_extraction_jobs(
            current_thread_id=current_thread,
            max_age_days=10,
            min_idle_hours=0,
            limit=2,
            lease_seconds=3_600,
        )

        output = StageOneMemory(
            old_thread,
            tmp_path,
            claims[0].source_updated_at,
            "durable detail",
            "routing summary",
            "stable-task",
        )
        assert await first.complete_extraction(claims[0], output)
        # An already-successful source version is never sampled twice.
        assert not await first.claim_extraction_jobs(
            current_thread_id=current_thread,
            max_age_days=10,
            min_idle_hours=0,
            limit=2,
            lease_seconds=3_600,
        )

        consolidation = await first.claim_consolidation(lease_seconds=3_600)
        assert consolidation is not None
        assert await second.claim_consolidation(lease_seconds=3_600) is None
        selected = await first.load_consolidation_inputs(limit=256, max_unused_days=30)
        assert selected == (output,)
        assert await first.complete_consolidation(consolidation, selected)
        assert await first.claim_consolidation(lease_seconds=3_600) is None

    asyncio.run(scenario())


def test_expired_extraction_lease_can_be_reclaimed_but_old_owner_cannot_commit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(thread_id, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "remember", "done")
        )
        repository = SQLiteMemoryRepository(database)
        old = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=0,
            )
        )[0]
        replacement = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=60,
            )
        )[0]
        assert replacement.ownership_token != old.ownership_token
        value = StageOneMemory(
            thread_id,
            tmp_path,
            replacement.source_updated_at,
            "detail",
            "summary",
        )
        assert not await repository.complete_extraction(old, value)
        assert await repository.complete_extraction(replacement, value)

    asyncio.run(scenario())


def test_polluted_thread_is_not_eligible_for_extraction(tmp_path: Path) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(thread_id, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "external", "result")
        )
        repository = SQLiteMemoryRepository(database)
        await repository.mark_thread_mode(thread_id, "polluted")
        assert not await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=10,
            min_idle_hours=0,
            limit=2,
            lease_seconds=60,
        )

    asyncio.run(scenario())


def test_failed_extraction_obeys_backoff_then_can_be_reclaimed(tmp_path: Path) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(thread_id, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "remember", "done")
        )
        repository = SQLiteMemoryRepository(database)
        claim = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=60,
            )
        )[0]
        assert await repository.fail_extraction(claim, "temporary", retry_delay_seconds=60)
        assert not await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=10,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        with repository._connect() as connection:
            connection.execute(
                "UPDATE memory_jobs SET retry_at=0 WHERE kind='memory_stage1' AND job_key=?",
                (str(thread_id),),
            )
        replacement = await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=10,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        assert len(replacement) == 1
        assert replacement[0].ownership_token != claim.ownership_token

    asyncio.run(scenario())


def test_extraction_commit_rejects_thread_polluted_after_claim(tmp_path: Path) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(thread_id, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "question", "answer")
        )
        repository = SQLiteMemoryRepository(database)
        claim = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=60,
            )
        )[0]
        await repository.mark_thread_mode(thread_id, "polluted")
        output = StageOneMemory(
            thread_id,
            tmp_path,
            claim.source_updated_at,
            "must not publish",
            "must not publish",
        )

        assert not await repository.complete_extraction(claim, output)
        assert not await repository.load_consolidation_inputs(limit=10, max_unused_days=30)

    asyncio.run(scenario())


def test_extraction_commit_rejects_stale_source_and_requeues_latest_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corki.db"
    sessions = SQLiteSessionRepository(database)
    thread_id = new_thread_id()
    turn_id = new_turn_id()

    async def scenario() -> None:
        await sessions.create_thread(thread_id, tmp_path)
        await sessions.save_turn(
            TurnRecord(turn_id, thread_id, TurnStatus.COMPLETED, "question", "answer")
        )
        repository = SQLiteMemoryRepository(database)
        stale = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=10,
                min_idle_hours=0,
                limit=1,
                lease_seconds=60,
            )
        )[0]
        with repository._connect() as connection:
            connection.execute(
                "UPDATE threads SET updated_at="
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 minute') WHERE id=?",
                (str(thread_id),),
            )
        stale_output = StageOneMemory(
            thread_id,
            tmp_path,
            stale.source_updated_at,
            "stale detail",
            "stale summary",
        )

        assert not await repository.complete_extraction(stale, stale_output)
        replacement = await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=10,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        assert len(replacement) == 1
        assert replacement[0].source_updated_at != stale.source_updated_at

    asyncio.run(scenario())
