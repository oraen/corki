"""Dedicated memory domain errors versus storage failures in the owning Runtime."""

import asyncio
import errno
import json
import os
from pathlib import Path

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import LocalMemoryBackend
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolSpec
from corki.tools import ToolRegistry

FILENAME = "2026-09-08T12-00-00-request.md"


class MemoryModel:
    def __init__(self, calls):
        self.calls = calls
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            yield ModelCompleted(tuple(ToolCallItem(call, turn, step) for call in self.calls))
        else:
            yield ModelCompleted((AssistantMessageItem("handled", turn, step),))

    async def aclose(self):
        pass


def call(name, arguments):
    return ToolCall(new_tool_call_id(), name, arguments)


def runtime_for(tmp_path, model, *, registry=None, mode="direct", thread_id=None):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            memories_enabled=True,
            memories_generate=False,
            memories_background_enabled=False,
            memories_dedicated_tools=True,
            tool_mode=mode,
        ),
        database_path=tmp_path / "sessions.db",
        memory_root=tmp_path / "memories",
        thread_id=thread_id,
        registry=registry,
        model=model,
    )


FATAL_CASES = (
    "read_permission",
    "read_disappeared",
    "read_invalid_utf8",
    "read_metadata",
    "search_permission",
    "search_disappeared",
    "search_metadata",
    "search_scandir",
    "list_metadata",
    "list_scandir",
    "note_permission",
    "note_disappeared",
    "note_partial_write",
    "note_metadata",
    "note_mkdir",
)


@pytest.mark.parametrize("case", FATAL_CASES)
def test_storage_failure_stops_direct_turn_without_resampling(tmp_path, monkeypatch, case):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        target = root / "MEMORY.md"
        note_path = root / "extensions/ad_hoc/notes" / FILENAME
        target.write_bytes(b"memory fact\n")
        prefix = case.split("_", 1)[0]
        name = "memories::add_ad_hoc_note" if prefix == "note" else f"memories::{prefix}"
        arguments = (
            {"filename": FILENAME, "note": "verbatim requested note"}
            if prefix == "note"
            else {"queries": ["fact"], "path": "MEMORY.md"}
            if prefix == "search"
            else {"path": "MEMORY.md"}
        )
        if case.endswith("scandir"):
            arguments.pop("path")
        model = MemoryModel((call(name, arguments),))
        runtime = runtime_for(tmp_path, model)
        injected = []
        if case == "read_invalid_utf8":
            target.write_bytes(b"\xff\xfe")
        elif case.endswith("metadata"):
            original = Path.lstat
            metadata_target = note_path.parent if prefix == "note" else target

            def lstat(path, *args, **kwargs):
                if path == metadata_target:
                    injected.append(case)
                    raise PermissionError(errno.EACCES, "injected metadata failure")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(Path, "lstat", lstat)
        elif case == "note_mkdir":
            note_path.parent.rename(tmp_path / "saved-notes")
            original = Path.mkdir

            def mkdir(path, *args, **kwargs):
                if path == note_path.parent:
                    injected.append(case)
                    raise PermissionError(errno.EACCES, "injected note directory create failure")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(Path, "mkdir", mkdir)
        elif case.endswith("scandir"):
            original_scandir, original_iterdir = os.scandir, Path.iterdir

            def scandir(path):
                if Path(path) == root:
                    injected.append(case)
                    raise PermissionError(errno.EACCES, "injected directory failure")
                return original_scandir(path)

            def iterdir(path):
                if path == root:
                    injected.append(case)
                    raise PermissionError(errno.EACCES, "injected directory failure")
                return original_iterdir(path)

            monkeypatch.setattr(os, "scandir", scandir)
            monkeypatch.setattr(Path, "iterdir", iterdir)
        elif prefix == "note":
            original_open, original_fdopen = os.open, os.fdopen

            def open_note(path, flags, *args, **kwargs):
                if Path(path) == note_path:
                    injected.append(case)
                    if case == "note_permission":
                        raise PermissionError(errno.EACCES, "injected note create failure")
                    if case == "note_disappeared":
                        raise FileNotFoundError(errno.ENOENT, "injected parent disappeared")
                return original_open(path, flags, *args, **kwargs)

            class PartialWriter:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self

                def write(self, content):
                    self.handle.write(content[:8])
                    self.handle.flush()
                    raise OSError(errno.ENOSPC, "injected full disk after partial note")

                def __exit__(self, *args):
                    self.handle.close()

            def fdopen(fd, mode, *args, **kwargs):
                handle = original_fdopen(fd, mode, *args, **kwargs)
                return PartialWriter(handle) if mode == "wb" else handle

            monkeypatch.setattr(os, "open", open_note)
            if case == "note_partial_write":
                monkeypatch.setattr(os, "fdopen", fdopen)
        else:
            original = Path.read_bytes

            def read_bytes(path):
                if path == target:
                    injected.append(case)
                    error = FileNotFoundError if case.endswith("disappeared") else PermissionError
                    raise error("injected failure after successful metadata lookup")
                return original(path)

            monkeypatch.setattr(Path, "read_bytes", read_bytes)
        try:
            events = [event async for event in runtime.stream("use memory; remember if requested")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert events[-1].error_kind == "tool" and not events[-1].retryable
            assert "I/O error while reading memories" in events[-1].error
            assert len(model.requests) == 1
            assert (
                sum(isinstance(e, (TurnFailed, TurnCompleted, TurnCancelled)) for e in events) == 1
            )
            items = await runtime._repository.load_items(runtime.thread_id)
            assert all(item.is_error for item in items if isinstance(item, ToolResultItem))
            if case != "read_invalid_utf8":
                assert injected
            if prefix == "note":
                assert len(injected) == 1, (
                    "uncertain note creation must not be automatically retried"
                )
                if case == "note_partial_write":
                    assert note_path.read_bytes() == b"verbatim"
                else:
                    assert not note_path.exists()
        finally:
            await runtime.aclose()
        if prefix == "note":
            # Reopening cannot retry a terminal failed Turn, even if a note
            # contains partial bytes and its call did not commit a result.
            monkeypatch.undo()
            reopened_model = MemoryModel(model.calls)
            reopened = runtime_for(tmp_path, reopened_model, thread_id=runtime.thread_id)
            try:
                assert [e async for e in reopened.resume_pending()] == []
                assert not reopened_model.requests and len(injected) == 1
                if case == "note_partial_write":
                    assert note_path.read_bytes() == b"verbatim"
            finally:
                await reopened.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "missing_read",
        "missing_list",
        "missing_search",
        "hidden",
        "escape",
        "directory",
        "line_offset",
        "empty_query",
        "duplicate_note",
        "note_directory_file",
    ],
)
def test_semantic_memory_errors_remain_durable_observations(tmp_path, case):
    async def scenario():
        root = tmp_path / "memories"
        backend = LocalMemoryBackend(root)
        (root / "MEMORY.md").write_text("fact\n", encoding="utf-8")
        name, arguments = "memories::read", {"path": "MEMORY.md"}
        if case.startswith("missing_"):
            name = "memories::" + case.removeprefix("missing_")
            arguments = {"path": "missing.md"}
            if name == "memories::search":
                arguments["queries"] = ["fact"]
        elif case in {"hidden", "escape", "directory"}:
            arguments["path"] = {
                "hidden": ".hidden",
                "escape": "../outside",
                "directory": "skills",
            }[case]
        elif case == "line_offset":
            arguments["line_offset"] = 40
        elif case == "empty_query":
            name, arguments = "memories::search", {"queries": [" "]}
        else:
            name, arguments = (
                "memories::add_ad_hoc_note",
                {"filename": FILENAME, "note": "original"},
            )
            if case == "duplicate_note":
                backend.add_note(FILENAME, "original")
        model = MemoryModel((call(name, arguments),))
        runtime = runtime_for(tmp_path, model)
        # Replace after composition, so this tests a tool-time path error rather
        # than initialization failure. Keep the original directory recoverable.
        if case == "note_directory_file":
            directory = root / "extensions/ad_hoc/notes"
            directory.rename(tmp_path / "saved-notes")
            directory.write_text("not a directory", encoding="utf-8")
        try:
            events = [event async for event in runtime.stream("use memory")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 2
            observed = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert len(observed) == 1 and observed[0].is_error
            items = await runtime._repository.load_items(runtime.thread_id)
            assert [i for i in items if isinstance(i, ToolResultItem)] == observed
            if case == "duplicate_note":
                assert (root / "extensions/ad_hoc/notes" / FILENAME).read_text() == "original"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_search_skips_binary_content_without_hiding_valid_matches(tmp_path):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "binary.md").write_bytes(b"\xff")
        (root / "valid.md").write_text("searchable fact", encoding="utf-8")
        model = MemoryModel((call("memories::search", {"queries": ["fact"]}),))
        runtime = runtime_for(tmp_path, model)
        try:
            events = [e async for e in runtime.stream("find fact")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(i for i in model.requests[-1].items if isinstance(i, ToolResultItem))
            assert not result.is_error
            assert [m["path"] for m in json.loads(result.content)["matches"]] == ["valid.md"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["list", "read", "search"])
def test_nul_path_io_conversion_is_not_a_semantic_missing_path(tmp_path, operation):
    async def scenario():
        arguments = {"path": "invalid\x00path"}
        if operation == "search":
            arguments["queries"] = ["fact"]
        model = MemoryModel((call(f"memories::{operation}", arguments),))
        runtime = runtime_for(tmp_path, model)
        try:
            events = [e async for e in runtime.stream("read the requested memory path")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert events[-1].error_kind == "tool" and not events[-1].retryable
            assert "I/O error while reading memories" in events[-1].error
            assert len(model.requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_exclusive_memory_failure_or_cancel_does_not_start_next_tool(tmp_path, monkeypatch, cancel):
    async def scenario():
        started, closed, reading = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Sibling:
            spec = ToolSpec(
                "sibling", "Wait", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL
            )

            async def execute(self, call, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        async def read(backend, *args, **kwargs):
            reading.set()
            if cancel:
                await asyncio.Event().wait()
            raise OSError(errno.EIO, "injected memory backend lost")

        monkeypatch.setattr(LocalMemoryBackend, "read", read)
        registry = ToolRegistry()
        registry.register(Sibling())
        model = MemoryModel((call("memories::read", {"path": "MEMORY.md"}), call("sibling", {})))
        runtime = runtime_for(tmp_path, model, registry=registry)
        events = []

        async def consume():
            async for event in runtime.stream("run both"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(reading.wait(), 2)
            if cancel:
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCancelled if cancel else TurnFailed), events[-1]
            assert not started.is_set() and not closed.is_set() and len(model.requests) == 1
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["list", "search"])
@pytest.mark.parametrize(
    "fault", ["directory_gone", "entry_gone", "entry_denied", "iteration_denied"]
)
def test_enumeration_skips_only_vanished_paths_and_closes_iterator(
    tmp_path, monkeypatch, operation, fault
):
    async def scenario():
        root = tmp_path / "memories"
        LocalMemoryBackend(root)
        (root / "a.md").write_text("fact a", encoding="utf-8")
        (root / "b.md").write_text("fact b", encoding="utf-8")
        arguments = {"queries": ["fact"]} if operation == "search" else {}
        model = MemoryModel((call(f"memories::{operation}", arguments),))
        runtime = runtime_for(tmp_path, model)
        original = os.scandir
        opened, closed = [], []

        class Entry:
            def __init__(self, entry):
                self.entry = entry
                self.path = entry.path

            def stat(self, **kwargs):
                if self.entry.name == "a.md" and fault in {"entry_gone", "entry_denied"}:
                    error = FileNotFoundError if fault == "entry_gone" else PermissionError
                    raise error("injected entry metadata failure")
                return self.entry.stat(**kwargs)

        class Entries:
            def __init__(self, iterator):
                self.iterator = iterator

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.iterator.close()
                closed.append(True)

            def __iter__(self):
                if fault == "iteration_denied":
                    raise PermissionError("injected next entry failure")
                for entry in self.iterator:
                    yield Entry(entry)

        def scandir(path):
            if Path(path) != root:
                return original(path)
            opened.append(True)
            if fault == "directory_gone":
                raise FileNotFoundError("injected vanished directory")
            return Entries(original(path))

        monkeypatch.setattr(os, "scandir", scandir)
        try:
            events = [e async for e in runtime.stream("look up memory")]
            assert opened == [True]
            assert closed == ([] if fault == "directory_gone" else [True])
            if fault.endswith("denied"):
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert events[-1].error_kind == "tool" and len(model.requests) == 1
            else:
                assert isinstance(events[-1], TurnCompleted), events[-1]
                result = next(i for i in model.requests[-1].items if isinstance(i, ToolResultItem))
                assert not result.is_error
                rows = json.loads(result.content)["matches" if operation == "search" else "entries"]
                files = [row["path"] for row in rows if row["path"].endswith(".md")]
                assert files == ([] if fault == "directory_gone" else ["b.md"])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")
def test_nested_memory_io_error_is_catchable_in_real_code_mode(tmp_path, monkeypatch):
    async def scenario():
        async def read(backend, *args, **kwargs):
            raise OSError(errno.EIO, "injected nested memory storage failure")

        monkeypatch.setattr(LocalMemoryBackend, "read", read)
        source = (
            "try {await tools.memories__read({path:'MEMORY.md'});} "
            "catch(e) {text('caught:'+e);} text('after catch');"
        )
        nested = ToolCall(
            new_tool_call_id(), "exec", None, raw_arguments=source, input_kind="freeform"
        )
        model = MemoryModel((nested,))
        runtime = runtime_for(tmp_path, model, mode="code_mode_only")
        try:
            events = [e async for e in runtime.stream("read memory in code")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            result = next(
                i
                for i in model.requests[-1].items
                if isinstance(i, ToolResultItem) and i.tool_name == "exec"
            )
            assert "caught:" in result.content and "after catch" in result.content
            assert "I/O error while reading memories" in result.content
            assert len(model.requests) == 2 and not runtime._code_mode.cells
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
