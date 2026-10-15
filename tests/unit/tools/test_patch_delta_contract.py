"""Strict committed-change protocol and post-start failure boundaries."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from corki.execution import backend
from corki.execution.patch_result import PatchResult
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.patches import parse_patch_delta
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "add", "content": "new", "overwritten_content": "old"},
        {"kind": "delete", "content": "old"},
        {
            "kind": "update",
            "move_path": "/b",
            "old_content": "old",
            "new_content": "new",
            "overwritten_move_content": "dest",
        },
    ],
)
def test_ordered_repeated_paths_are_not_deduplicated(change):
    value = {
        "version": 1,
        "exact": True,
        "changes": [
            {"path": "/a", "change": change},
            {"path": "/a", "change": change},
        ],
    }
    assert parse_patch_delta(json.dumps(value)) == value


@pytest.mark.parametrize(
    "payload",
    [
        '{"version":1,"exact":true,"exact":false,"changes":[]}',
        '{"version":true,"exact":true,"changes":[]}',
        '{"version":1,"exact":1,"changes":[]}',
        '{"version":1,"exact":true,"changes":[],"planned":true}',
        '{"version":1,"exact":true,"changes":[{"path":"relative","change":{}}]}',
        '{"version":1,"exact":true,"changes":[{"path":"/a","change":{"kind":"add","content":"a"}}]}',
        '{"version":1,"exact":true,"changes":[{"path":"/a","change":{"kind":[],"content":"a"}}]}',
    ],
)
def test_invalid_committed_records_rejected(payload):
    with pytest.raises(ValueError):
        parse_patch_delta(payload)


@pytest.mark.parametrize("present", [False, True])
def test_cold_claim_preserves_delta_without_rewriting_legacy_rows(tmp_path, present):
    async def scenario():
        path = tmp_path / "ledger.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "apply_patch", {"patch": "unused"})
        assert await repository.claim_tool_call(thread, turn, call) is None
        result = ToolResult(
            call.id,
            call.name,
            "output",
            patch_delta_json=('{"version":1,"exact":false,"changes":[]}' if present else None),
        )
        await repository.complete_tool_call(thread, turn, result)
        with sqlite3.connect(path) as db:
            before = db.execute("SELECT result_json FROM tool_executions").fetchone()[0]
        assert ("patch_delta_json" in json.loads(before)) is present
        reopened = SQLiteSessionRepository(path)
        cached = await reopened.claim_tool_call(thread, turn, call)
        assert cached == result
        await reopened.complete_tool_call(thread, turn, cached)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT result_json FROM tool_executions").fetchone()[0] == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [ValueError("oversized output"), TimeoutError("lost process"), asyncio.CancelledError(), None],
)
def test_post_start_failure_marks_unknown_and_never_replays(tmp_path, monkeypatch, failure):
    async def scenario():
        async def compile(*args, **kwargs):
            return SimpleNamespace(command=["fixed-helper"], patch_authority={})

        calls = []

        async def run(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                review = {
                    "version": 1,
                    "requires_approval": False,
                    "patch": "patch",
                    "cwd": str(tmp_path),
                    "files": [str(tmp_path / "a")],
                    "changes": [
                        {"path": str(tmp_path / "a"), "change": {"kind": "add", "content": "a"}},
                    ],
                }
                return json.dumps({"ok": json.dumps(review)}).encode()
            if failure is not None:
                raise failure
            return b'{"ok":"invalid outcome"}'

        monkeypatch.setattr(backend, "_compile_native_file_helper", compile)
        monkeypatch.setattr(backend, "run_owned", run)
        if isinstance(failure, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await backend.patch_operation(None, tmp_path, {"patch": "patch"})
            assert len(calls) == 2
            return
        result = await backend.patch_operation(None, tmp_path, {"patch": "patch"})
        assert isinstance(result, PatchResult) and result.success is False
        assert parse_patch_delta(result.delta_json) == {"version": 1, "exact": False, "changes": []}
        assert "Do not automatically retry" in result.output
        assert len(calls) == 2

    asyncio.run(scenario())
