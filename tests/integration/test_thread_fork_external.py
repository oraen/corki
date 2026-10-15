"""A fork borrows a source repository without inheriting its storage lifetime."""

import asyncio
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("ephemeral", [False, True])
@pytest.mark.parametrize("fault", [None, "transaction", "after_commit"])
def test_fork_borrows_external_source_without_persisting_ephemeral_target(
    tmp_path, ephemeral, fault, monkeypatch
):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
                yield ModelCompleted(
                    (AssistantMessageItem("answer", user.turn_id, new_step_id()),),
                    usage=ModelUsage(input_tokens=100, output_tokens=10),
                )

            async def aclose(self):
                pass

        async def create(database, **kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                database_path=database,
                home_path=tmp_path,
                model=Model(),
                registry=ToolRegistry(),
                **kwargs,
            )

        source = await create(tmp_path / "source" / "history.db")
        try:
            assert isinstance([e async for e in source.stream("SOURCE")][-1], TurnCompleted)
            original = await source.load_display_snapshot()
            source_base = (
                await source._repository.load_fork_snapshot(source.thread_id)
            ).base_instructions
            source_usage = await source._repository.load_context_usage(source.thread_id)
            target_path = tmp_path / "target" / "history.db"
            target = await create(
                target_path,
                ephemeral=ephemeral,
                fork_from_thread_id=source.thread_id,
                fork_source_repository=source._repository,
            )
            try:
                if fault:
                    repository = target._repository
                    original_append = repository._append_items_in_connection
                    original_fork = repository.fork_thread

                    def fail_append(*args):
                        original_append(*args)
                        raise OSError("external fork publication")

                    async def fail_commit(*args, **kwargs):
                        await original_fork(*args, **kwargs)
                        raise OSError("external fork publication")

                    with monkeypatch.context() as patch:
                        patch.setattr(
                            repository,
                            "_append_items_in_connection"
                            if fault == "transaction"
                            else "fork_thread",
                            fail_append if fault == "transaction" else fail_commit,
                        )
                        with pytest.raises(OSError, match="external fork publication"):
                            await target._ensure_ready()
                    if ephemeral:
                        assert target._writer is None
                    else:
                        assert not target._writer.held
                    assert await repository.thread_exists(target.thread_id) == (
                        fault == "after_commit"
                    )
                    with repository._connect() as connection:
                        base = connection.execute(
                            "SELECT model,instructions FROM thread_base_instructions "
                            "WHERE thread_id=?",
                            (str(target.thread_id),),
                        ).fetchone()
                    assert (tuple(base) if base is not None else None) == (
                        source_base if fault == "after_commit" else None
                    )
                    assert isinstance([e async for e in source.stream("LATER")][-1], TurnCompleted)
                    original = await source.load_display_snapshot()

                    async def unexpected_read(*args):
                        raise AssertionError("retry reread changed source")

                    monkeypatch.setattr(source._repository, "load_fork_snapshot", unexpected_read)
                assert [e async for e in target.resume_pending()] == []
                assert len(requests) == (2 if fault else 1)
                copied = await target.load_display_snapshot()
                assert {i.id for i in copied.items}.isdisjoint(i.id for i in original.items)
                assert [i.content for i in copied.items if isinstance(i, UserMessageItem)] == [
                    "SOURCE"
                ]
                usage = await target._repository.load_context_usage(target.thread_id)
                assert usage.total_tokens == source_usage.total_tokens
                assert usage.anchor_id != source_usage.anchor_id
                assert usage.anchor_id in {i.id for i in copied.items}
                with target._repository._connect() as connection:
                    for table in ("model_steps", "tool_executions", "hook_executions"):
                        assert (
                            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
                        )
                assert isinstance([e async for e in target.stream("BRANCH")][-1], TurnCompleted)
                assert [
                    i.content for i in requests[-1].items if isinstance(i, UserMessageItem)
                ] == [
                    "SOURCE",
                    "BRANCH",
                ]
                assert await source.load_display_snapshot() == original
                final_snapshot = await target.load_display_snapshot()
                target_id = target.thread_id
            finally:
                await target.aclose()
            assert target_path.parent.exists() is (not ephemeral)
            # Closing the borrower must not close the live source owner.
            assert isinstance([e async for e in source.stream("STILL_LIVE")][-1], TurnCompleted)
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                "SOURCE",
                *(["LATER"] if fault else []),
                "STILL_LIVE",
            ]
            await source.aclose()
            if not ephemeral:
                cold = await create(target_path, thread_id=target_id)
                try:
                    count = len(requests)
                    assert [e async for e in cold.resume_pending()] == []
                    assert len(requests) == count
                    assert await cold.load_display_snapshot() == final_snapshot
                    assert isinstance([e async for e in cold.stream("COLD")][-1], TurnCompleted)
                    assert [
                        i.content for i in requests[-1].items if isinstance(i, UserMessageItem)
                    ] == ["SOURCE", "BRANCH", "COLD"]
                finally:
                    await cold.aclose()
        finally:
            await source.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "unavailable"])
def test_external_fork_read_failure_never_publishes_target(tmp_path, monkeypatch, failure):
    import corki.storage.forks as forks

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("answer", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        async def create(name, **kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                database_path=tmp_path / name / "history.db",
                home_path=tmp_path,
                registry=ToolRegistry(),
                model=Model(),
                **kwargs,
            )

        source = await create("source")
        target = None
        release, finished = threading.Event(), threading.Event()
        try:
            assert isinstance([e async for e in source.stream("SOURCE")][-1], TurnCompleted)
            original = await source.load_display_snapshot()
            target = await create(
                "target",
                fork_from_thread_id=source.thread_id,
                fork_source_repository=source._repository,
            )
            entered = asyncio.Event()
            loop = asyncio.get_running_loop()
            read = forks.read_fork_snapshot

            def interrupted_read(*args):
                loop.call_soon_threadsafe(entered.set)
                try:
                    if failure == "unavailable":
                        raise OSError("source unavailable")
                    if not release.wait(5):
                        raise TimeoutError("test did not release source reader")
                    return read(*args)
                finally:
                    finished.set()

            with monkeypatch.context() as patch:
                patch.setattr(forks, "read_fork_snapshot", interrupted_read)
                task = asyncio.create_task(target._ensure_ready())
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    if failure == "cancel":
                        task.cancel()
                        await asyncio.sleep(0)
                        assert not task.done() and not finished.is_set()
                        release.set()
                    with pytest.raises(asyncio.CancelledError if failure == "cancel" else OSError):
                        await asyncio.wait_for(task, 5)
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
            assert finished.is_set()
            assert not target._writer.held
            assert not await target._repository.thread_exists(target.thread_id)
            assert len(requests) == 1
            assert await source.load_display_snapshot() == original
            assert [e async for e in target.resume_pending()] == []
            assert len(requests) == 1
            assert [
                i.content
                for i in await target.load_display_history()
                if isinstance(i, UserMessageItem)
            ] == ["SOURCE"]
        finally:
            release.set()
            if target is not None:
                await target.aclose()
            await source.aclose()

    asyncio.run(scenario())
