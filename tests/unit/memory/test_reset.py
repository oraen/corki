"""Explicit reset scope and failure ordering, using only temporary memory stores."""

import asyncio
from pathlib import Path

import pytest

from corki.memory.reset import MemoryResetError, MemoryResetter


class Repository:
    def __init__(self):
        self.clears = 0

    async def clear_memory_data(self):
        self.clears += 1


def controller(tmp_path, repository, root=None, database=None):
    return MemoryResetter(
        roots=(root or tmp_path / "memories", tmp_path / "memories_extensions"),
        protected_paths=(tmp_path, tmp_path / "sessions/history.db"),
        repository=repository,
        database_path=database,
    )


def test_workspace_lease_io_failure_does_not_clear_memory(tmp_path, monkeypatch):
    repository = Repository()
    root = tmp_path / "memories"
    root.mkdir()
    marker = root / "keep.md"
    marker.write_text("keep")

    def denied(roots):
        raise PermissionError("cannot open lock file")

    monkeypatch.setattr("corki.memory.reset.WorkspaceLeases", denied)
    with pytest.raises(MemoryResetError, match="ownership") as captured:
        asyncio.run(controller(tmp_path, repository).reset())
    assert not captured.value.database_cleared
    assert repository.clears == 0 and marker.read_text() == "keep"


@pytest.mark.parametrize("failure", ["none", "db", "file", "root_link"])
def test_reset_scope_partial_failure_and_retry(tmp_path, monkeypatch, failure):
    async def scenario():
        repository = Repository()
        root = tmp_path / "memories"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        keep = outside / "keep"
        keep.write_text("outside")
        target = root / ".private"
        target.write_text("private")
        (root / "linked").symlink_to(outside, target_is_directory=True)
        (root / "linked-file").symlink_to(keep)
        resetter = controller(tmp_path, repository)
        unlink = Path.unlink
        if failure == "db":

            async def denied():
                raise RuntimeError("DB unavailable")

            monkeypatch.setattr(repository, "clear_memory_data", denied)
        elif failure == "file":

            def denied(path, *args, **kwargs):
                if path == target:
                    raise PermissionError("cannot delete")
                return unlink(path, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", denied)
        elif failure == "root_link":
            root.rename(tmp_path / "saved-memory")
            root.symlink_to(outside, target_is_directory=True)
        if failure == "none":
            assert await resetter.reset() == (root, tmp_path / "memories_extensions")
            assert root.is_dir() and not list(root.iterdir())
        else:
            with pytest.raises(MemoryResetError) as captured:
                await resetter.reset()
            assert captured.value.database_cleared is (failure != "db")
            assert keep.read_text() == "outside"
            if failure == "root_link":
                assert root.is_symlink()
            else:
                assert target.exists()
            if failure == "file":
                monkeypatch.setattr(Path, "unlink", unlink)
                await resetter.reset()
                assert not list(root.iterdir())
        assert keep.read_text() == "outside"
        await resetter.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["workspace", "database_parent", "filesystem", "relative"])
def test_broad_target_rejected_before_database_clear(tmp_path, target):
    root = {
        "workspace": tmp_path,
        "database_parent": tmp_path / "sessions",
        "filesystem": Path(tmp_path.anchor),
        "relative": Path("memories"),
    }[target]
    repository = Repository()
    with pytest.raises(MemoryResetError):
        asyncio.run(controller(tmp_path, repository, root).reset())
    assert repository.clears == 0


@pytest.mark.parametrize("failure", ["db", "close", "both"])
def test_owned_repository_cleanup_preserves_reset_phase_and_first_error(
    tmp_path, monkeypatch, failure
):
    class Owned(Repository):
        async def clear_memory_data(self):
            if failure in {"db", "both"}:
                raise ValueError("first database error")
            await super().clear_memory_data()

        async def close(self):
            if failure in {"close", "both"}:
                raise OSError("second cleanup error")

    repository = Owned()
    monkeypatch.setattr("corki.memory.reset.SQLiteMemoryRepository", lambda _: repository)
    with pytest.raises(MemoryResetError) as captured:
        asyncio.run(controller(tmp_path, None, database=tmp_path / "sessions/history.db").reset())
    assert captured.value.database_cleared is (failure == "close")
    assert isinstance(captured.value.__cause__, OSError if failure == "close" else ValueError)


def test_missing_custom_reset_capability_does_not_touch_files(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    keep = root / "keep"
    keep.write_text("keep")
    with pytest.raises(MemoryResetError):
        asyncio.run(controller(tmp_path, object()).reset())
    assert keep.read_text() == "keep"


@pytest.mark.parametrize("cancel_queued", [False, True])
def test_reset_requests_are_serialized_and_queued_cancellation_does_not_clear(
    tmp_path, cancel_queued
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Held(Repository):
            async def clear_memory_data(self):
                await super().clear_memory_data()
                entered.set()
                await release.wait()

        repository = Held()
        resetter = controller(tmp_path, repository)
        first = asyncio.create_task(resetter.reset())
        await entered.wait()
        second = asyncio.create_task(resetter.reset())
        await asyncio.sleep(0)
        assert repository.clears == 1
        if cancel_queued:
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        release.set()
        await first
        if not cancel_queued:
            await second
        assert repository.clears == (1 if cancel_queued else 2)
        await resetter.aclose()

    asyncio.run(scenario())
