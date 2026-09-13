"""Copied forks are independent runtimes, never resumed source operations."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("before", [None, 0, 1, 99])
@pytest.mark.parametrize("fault", [None, "transaction", "after_commit"])
def test_fork_copies_selected_history_without_mutating_source(tmp_path, before, fault, monkeypatch):
    async def scenario():
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
                yield ModelCompleted(
                    (AssistantMessageItem(f"ANSWER:{user.content}", user.turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create()
        try:
            for text in ("ONE", "TWO"):
                assert isinstance([e async for e in source.stream(text)][-1], TurnCompleted)
            original = await source.load_display_snapshot()
            source_id = source.thread_id
        finally:
            await source.aclose()

        requests.clear()
        fork = await create(fork_from_thread_id=source_id, fork_before_user_message=before)
        try:
            if fault:
                repository = fork._repository
                original_append = repository._append_items_in_connection
                original_fork = repository.fork_thread
                failure = OSError("fork publication boundary")

                def fail_append(*args):
                    original_append(*args)
                    raise failure

                async def fail_after_commit(*args, **kwargs):
                    await original_fork(*args, **kwargs)
                    raise failure

                with monkeypatch.context() as patch:
                    patch.setattr(
                        repository,
                        "_append_items_in_connection" if fault == "transaction" else "fork_thread",
                        fail_append if fault == "transaction" else fail_after_commit,
                    )
                    with pytest.raises(OSError) as caught:
                        await fork._ensure_ready()
                    assert caught.value is failure
                assert not fork._writer.held
                assert not requests
                assert await repository.thread_exists(fork.thread_id) == (fault == "after_commit")
                assert await repository.load_display_snapshot(source_id) == original
            # A cold source is sufficient; creating a fork must not sample or run source work.
            assert fork.thread_id != source_id
            assert [e async for e in fork.resume_pending()] == []
            assert not requests
            copied = await fork.load_display_snapshot()
            expected = ["ONE", "TWO"][:before] if before is not None else ["ONE", "TWO"]
            assert [i.content for i in copied.items if isinstance(i, UserMessageItem)] == expected
            assert [i.content for i in copied.items if isinstance(i, AssistantMessageItem)] == [
                f"ANSWER:{text}" for text in expected
            ]
            assert {i.id for i in copied.items}.isdisjoint(i.id for i in original.items)
            assert {t.id for t in copied.turns}.isdisjoint(t.id for t in original.turns)
            assert [t.status.value for t in copied.turns] == (
                ["cancelled"] if before == 0 else ["completed"] * len(expected)
            )
            if before == 0:
                # Codex retains the prefix before the first user, including initial context.
                assert copied.items
                assert all(not isinstance(i, UserMessageItem) for i in copied.items)
                with fork._repository._connect() as connection:
                    row = connection.execute(
                        "SELECT user_input,final_answer FROM turns WHERE thread_id=?",
                        (fork.thread_id,),
                    ).fetchone()
                assert tuple(row) == ("", None)
            assert isinstance([e async for e in fork.stream("BRANCH")][-1], TurnCompleted)
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                *expected,
                "BRANCH",
            ]
            assert await fork._repository.load_display_snapshot(source_id) == original
            fork_id = fork.thread_id
        finally:
            await fork.aclose()

        requests.clear()
        cold = await create(thread_id=fork_id)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert not requests
            assert isinstance([e async for e in cold.stream("COLD")][-1], TurnCompleted)
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                *expected,
                "BRANCH",
                "COLD",
            ]
            assert await cold._repository.load_display_snapshot(source_id) == original
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_fork_cut_preserves_earlier_input_in_same_turn(tmp_path):
    from corki.protocol.ids import new_turn_id
    from corki.sessions import TurnRecord, TurnStatus

    async def scenario():
        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create()
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.COMPLETED,
                    "FIRST",
                    final_answer="LAST",
                )
            )
            await source._repository.append_items(
                source.thread_id,
                (
                    UserMessageItem("FIRST", turn),
                    AssistantMessageItem("EARLIER", turn, new_step_id()),
                    UserMessageItem("STEERED", turn),
                    AssistantMessageItem("LAST", turn, new_step_id()),
                ),
            )
            original = await source.load_display_snapshot()
            fork = await create(fork_from_thread_id=source.thread_id, fork_before_user_message=1)
            try:
                assert [e async for e in fork.resume_pending()] == []
                history = await fork.load_display_snapshot()
                assert [i.content for i in history.items] == ["FIRST", "EARLIER"]
                assert history.turns[0].status == TurnStatus.CANCELLED
                with fork._repository._connect() as connection:
                    row = connection.execute(
                        "SELECT final_answer FROM turns WHERE thread_id=?", (fork.thread_id,)
                    ).fetchone()
                assert row[0] is None
                assert await source.load_display_snapshot() == original
            finally:
                await fork.aclose()
        finally:
            await source.aclose()

    asyncio.run(scenario())


def test_fork_cut_does_not_treat_later_compaction_copy_as_unfinished_turn(tmp_path):
    from dataclasses import replace

    from corki.protocol.ids import new_item_id, new_thread_id, new_turn_id
    from corki.protocol.items import CompactionItem
    from corki.sessions import TurnRecord, TurnStatus
    from corki.storage.sqlite import SQLiteSessionRepository

    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "state.db")
        source, first, second = new_thread_id(), new_turn_id(), new_turn_id()
        await repository.create_thread(source, tmp_path)
        one, two = UserMessageItem("ONE", first), UserMessageItem("TWO", second)
        for turn, text in ((first, "ONE"), (second, "TWO")):
            await repository.save_turn(TurnRecord(turn, source, TurnStatus.COMPLETED, text))
        await repository.append_items(
            source,
            (
                one,
                two,
                CompactionItem("SUMMARY", two.id, second, replacement_item_count=1),
                replace(one, id=new_item_id(), retained_from_id=one.id),
            ),
        )

        class Model:
            async def stream(self, request):
                raise AssertionError("fork preparation must not sample")
                yield

            async def aclose(self):
                pass

        fork = await LangGraphRuntime.acreate(
            settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            fork_from_thread_id=source,
            fork_before_user_message=1,
        )
        try:
            assert [e async for e in fork.resume_pending()] == []
            history = await fork.load_display_snapshot()
            assert [i.content for i in history.items] == ["ONE"]
            assert [turn.status for turn in history.turns] == [TurnStatus.COMPLETED]
        finally:
            await fork.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("before", [None, 0, 99])
@pytest.mark.parametrize("legacy", [False, True])
def test_fork_of_running_source_never_resumes_its_tool(tmp_path, before, legacy):
    from corki.protocol.ids import ToolCallId, new_turn_id
    from corki.protocol.items import ToolCallItem, ToolResultItem, TurnAbortedItem
    from corki.protocol.tools import ToolCall
    from corki.sessions import TurnRecord, TurnStatus

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(**kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                **kwargs,
            )

        source = await create()
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            call = ToolCall(ToolCallId("source-effect"), "external_effect", {})
            if not legacy:
                await source._repository.save_turn(
                    TurnRecord(turn, source.thread_id, TurnStatus.RUNNING, "PENDING")
                )
            await source._repository.append_items(
                source.thread_id,
                (
                    UserMessageItem("PENDING", turn),
                    ToolCallItem(call, turn, new_step_id()),
                ),
            )
            await source._repository.claim_tool_call(source.thread_id, turn, call)
            original = await source.load_display_snapshot()
            branch = await create(
                fork_from_thread_id=source.thread_id,
                fork_before_user_message=before,
            )
            try:
                assert [e async for e in branch.resume_pending()] == []
                assert not requests
                copied = await branch.load_display_snapshot()
                if before is None:
                    assert len(copied.turns) == int(not legacy)
                    if not legacy:
                        assert copied.turns[0].status == TurnStatus.CANCELLED
                    assert any(isinstance(i, TurnAbortedItem) for i in copied.items)
                    if legacy:
                        again = await create(
                            fork_from_thread_id=branch.thread_id,
                            fork_before_user_message=99,
                        )
                        try:
                            assert [e async for e in again.resume_pending()] == []
                            twice = await again.load_display_snapshot()
                            assert twice.turns == ()
                            assert len(twice.items) == len(copied.items)
                            assert sum(isinstance(i, TurnAbortedItem) for i in twice.items) == 1
                            assert any(isinstance(i, UserMessageItem) for i in twice.items)
                        finally:
                            await again.aclose()
                else:
                    assert copied.items == () and copied.turns == ()
                assert isinstance([e async for e in branch.stream("NEXT")][-1], TurnCompleted)
                historical_calls = [i for i in requests[-1].items if isinstance(i, ToolCallItem)]
                results = [i for i in requests[-1].items if isinstance(i, ToolResultItem)]
                if before is None:
                    assert len(historical_calls) == len(results) == 1
                    assert historical_calls[0].call.id != call.id
                    assert results[0].call_id == historical_calls[0].call.id
                    assert results[0].is_error and results[0].content == "aborted"
                else:
                    assert historical_calls == results == []
                assert await branch._repository.latest_running_turn(branch.thread_id) is None
                assert await source.load_display_snapshot() == original
            finally:
                await branch.aclose()
        finally:
            await source.aclose()

    asyncio.run(scenario())
