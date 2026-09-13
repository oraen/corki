"""Cancellation owns the worker and cannot publish a late or partial skill cache."""

import asyncio
import threading

import pytest

from corki.skills.io import check_skill_io, publish_skill_io, run_skill_io
from corki.skills.service import SkillService


async def entered(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 2)


@pytest.mark.parametrize("late_error", [False, True])
def test_repeated_cancel_joins_worker_and_prevents_publication(late_error):
    async def scenario():
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        published = []

        def read():
            try:
                started.set()
                assert release.wait(3), "worker was not released"
                if late_error:
                    raise OSError("late failure")
                publish_skill_io(lambda: published.append("late"))
            finally:
                finished.set()

        task = asyncio.create_task(run_skill_io(read))
        try:
            await entered(started)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert finished.is_set()
            assert published == []
            # A successor does not inherit the previous worker's cancelled token.
            assert await run_skill_io(lambda: (check_skill_io(), "next")[1]) == "next"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_worker_errors_and_internal_cancel_are_not_success():
    async def scenario():
        for error in (OSError("normal failure"), asyncio.CancelledError()):

            def read(error=error):
                raise error

            with pytest.raises(type(error)):
                await run_skill_io(read)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_snapshot_is_staged_until_rules_finish(tmp_path, monkeypatch, cancel):
    import corki.skills.service as module

    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    folder = tmp_path / ".corki/skills/guide"
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    path.write_text("---\nname: guide\ndescription: old\n---\nOLD")
    original = service.snapshot(tmp_path)
    original_key = service._cache_key
    path.write_text("---\nname: guide\ndescription: new description\n---\nNEW")
    started, release = threading.Event(), threading.Event()

    def rules(*args):
        started.set()
        assert release.wait(3)
        if not cancel:
            raise ValueError("rules failed")
        return frozenset()

    monkeypatch.setattr(module, "disabled_skill_paths", rules)

    async def scenario():
        task = asyncio.create_task(run_skill_io(service.snapshot, tmp_path))
        try:
            await entered(started)
            assert service._snapshot is original
            assert service._cache_key == original_key
            if cancel:
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError if cancel else ValueError):
                await asyncio.wait_for(task, 2)
            assert service._snapshot is original
            assert service._cache_key == original_key
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_walk_stops_before_metadata_parse(tmp_path, monkeypatch):
    import corki.skills.walk as module

    root = tmp_path / ".corki/skills"
    (root / "guide").mkdir(parents=True)
    (root / "guide/SKILL.md").write_text("---\nname: guide\ndescription: fixture\n---\nBODY")
    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    original = module.os.scandir
    started, release = threading.Event(), threading.Event()
    parsed = []

    def scan(path):
        if path == root:
            started.set()
            assert release.wait(3)
        return original(path)

    monkeypatch.setattr(module.os, "scandir", scan)
    monkeypatch.setattr("corki.skills.discovery.parse_skill", lambda *a, **kw: parsed.append(a))

    async def scenario():
        task = asyncio.create_task(run_skill_io(service.snapshot, tmp_path))
        try:
            await entered(started)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert parsed == []
            assert service._cache_key is None
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
