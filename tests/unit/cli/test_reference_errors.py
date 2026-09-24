import asyncio
import sys

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from corki.cli import reference_completion
from corki.cli.command_completion import CommandCompleter
from corki.cli.reference_completion import FileSearchUnavailable, files
from corki.execution.owned_process import run_owned


@pytest.fixture(autouse=True)
def legacy_search_backend(monkeypatch):
    from corki.cli import native_file_search

    monkeypatch.setattr(native_file_search, "executable", lambda: None)


def test_nonzero_exit_remains_failure_unless_explicitly_allowed(tmp_path):
    async def scenario():
        arguments = [sys.executable, "-I", "-c", "raise SystemExit(1)"]
        with pytest.raises(ValueError, match="status 1"):
            await run_owned(arguments, b"", cwd=tmp_path, output_limit=100)
        assert (
            await run_owned(
                arguments, b"", cwd=tmp_path, output_limit=100, accepted_exit_codes=(0, 1)
            )
            == b""
        )

    asyncio.run(scenario())


def test_missing_search_executable_is_distinct_from_empty_directory(tmp_path, monkeypatch):
    assert asyncio.run(files(tmp_path)) == ()
    monkeypatch.setattr(reference_completion.shutil, "which", lambda _: None)
    with pytest.raises(FileSearchUnavailable, match="not installed"):
        asyncio.run(files(tmp_path))


@pytest.mark.parametrize("count", [10000, 10001])
def test_large_index_filters_before_candidate_limit(tmp_path, monkeypatch, count):
    async def output(*args, **kwargs):
        for index in range(count):
            kwargs["output_consumer"](f"file-{index}\0".encode())
        kwargs["output_consumer"](b"last-needle\0")
        return b""

    monkeypatch.setattr(reference_completion, "run_owned", output)
    assert len(asyncio.run(files(tmp_path))) == 100
    assert [row.label for row in asyncio.run(files(tmp_path, "needle"))] == ["last-needle"]


def test_derived_directory_candidates_are_filtered_before_limit(tmp_path, monkeypatch):
    async def output(*args, **kwargs):
        for index in range(5001):
            kwargs["output_consumer"](f"parent-{index}/child/file\0".encode())
        return b""

    monkeypatch.setattr(reference_completion, "run_owned", output)
    rows = asyncio.run(files(tmp_path, "parent-5000/child"))
    assert [(r.label, r.description) for r in rows] == [
        ("parent-5000/child", "Directory"),
        ("parent-5000/child/file", "File"),
    ]


def test_error_is_scoped_to_current_document_and_does_not_leak(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise OSError("private path and token")

    monkeypatch.setattr(reference_completion, "run_owned", fail)

    async def scenario():
        completer = CommandCompleter(tmp_path)
        document = Document("@abc", 4)
        assert [c async for c in completer.get_completions_async(document, CompleteEvent())] == []
        error = completer.reference_error_for(document)
        assert "failed" in error and "private" not in error and "token" not in error
        assert completer.reference_error_for(Document("@abcd", 5)) is None
        completer.reset_reference_error()
        assert completer.reference_error is None

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_obsolete_search_cannot_publish_error(tmp_path, monkeypatch, cancel):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail(cwd, query):
            entered.set()
            await release.wait()
            raise FileSearchUnavailable("old error")

        monkeypatch.setattr(reference_completion, "files", fail)
        completer = CommandCompleter(tmp_path)

        async def collect():
            return [
                c
                async for c in completer.get_completions_async(Document("@old", 4), CompleteEvent())
            ]

        task = asyncio.create_task(collect())
        try:
            await entered.wait()
            if cancel:
                task.cancel()
            else:
                completer.reset_reference_error()
            release.set()
            result = await asyncio.gather(task, return_exceptions=True)
            if cancel:
                assert isinstance(result[0], asyncio.CancelledError)
            else:
                assert result == [[]]
            assert completer.reference_error is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_empty_at_query_retains_catalog_without_starting_file_scan(tmp_path, monkeypatch):
    from pathlib import Path

    from corki.skills.models import SkillMetadata, SkillScope

    async def unexpected(*args):
        raise AssertionError("empty @ must not start a filesystem scan")

    monkeypatch.setattr(reference_completion, "files", unexpected)

    async def scenario():
        completer = CommandCompleter(tmp_path)

        async def load():
            path = Path("/local/review/SKILL.md")
            return (SkillMetadata("review", "Review", path, path.parent, SkillScope.USER),), ()

        completer.references = load
        rows = [
            row async for row in completer.get_completions_async(Document("@"), CompleteEvent())
        ]
        assert len(rows) == 1 and rows[0].reference.selector.path == "/local/review/SKILL.md"
        assert completer.reference_error is None

    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", ["@", "plain text"])
def test_stale_catalog_does_not_start_files_after_query_changes(tmp_path, monkeypatch, replacement):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        scans = []
        completer = CommandCompleter(tmp_path)

        async def load():
            entered.set()
            await release.wait()
            return (), ()

        async def scan(*args):
            scans.append(args)
            return ()

        completer.references = load
        monkeypatch.setattr(reference_completion, "files", scan)

        async def collect(text):
            return [
                row
                async for row in completer.get_completions_async(Document(text), CompleteEvent())
            ]

        old = asyncio.create_task(collect("@old"))
        try:
            async with asyncio.timeout(3):
                await entered.wait()
                completer.references = None
                assert await collect(replacement) == []
                release.set()
                assert await old == []
                assert scans == [] and completer.reference_error is None
        finally:
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)

    asyncio.run(scenario())
