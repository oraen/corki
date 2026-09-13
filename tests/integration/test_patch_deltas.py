"""Actual native mutations survive Runtime completion and the durable tool ledger."""

import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_approval_cancel import observe
from test_filesystem_helper_runtime import workspace_policy
from test_patch_approvals import Model, create_runtime, patch

from corki.protocol.events import ToolCallCompleted, TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.storage.sqlite import _result_from_json


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("case", ["overwrite", "move", "ordered", "partial"])
def test_actual_delta_reaches_host_and_cold_ledger_not_model(tmp_path, compiler, mode, case):
    async def scenario():
        (tmp_path / "a.txt").write_text("source-before\n")
        (tmp_path / "b.txt").write_text("target-before\n")
        (tmp_path / "directory").mkdir()
        bodies = {
            "overwrite": "*** Add File: b.txt\n+after",
            "move": "*** Update File: a.txt\n*** Move to: b.txt\n@@\n-source-before\n+after",
            "ordered": (
                "*** Delete File: b.txt\n*** Update File: a.txt\n"
                "*** Move to: b.txt\n@@\n-source-before\n+after"
            ),
            "partial": "*** Add File: b.txt\n+after\n*** Add File: directory\n+failure",
        }
        model = Model(mode)
        model.patch = patch(bodies[case])
        runtime = create_runtime(
            tmp_path, compiler, model, policy=workspace_policy(compiler, tmp_path)
        )
        try:
            events = await observe(runtime, "apply patch")
            assert isinstance(events[-1], TurnCompleted)
            assert (tmp_path / "b.txt").read_text() == "after\n"
            completed = [
                event
                for event in events
                if isinstance(event, ToolCallCompleted) and event.tool_name == "apply_patch"
            ]
            assert len(completed) == 1
            event = completed[0]
            assert event.is_error is (case == "partial")
            delta = json.loads(event.patch_delta_json)
            assert delta["exact"] is (case != "partial")
            assert delta["version"] == 1
            changes = delta["changes"]
            if case in {"overwrite", "partial"}:
                assert changes == [
                    {
                        "path": str(tmp_path / "b.txt"),
                        "change": {
                            "kind": "add",
                            "content": "after\n",
                            "overwritten_content": "target-before\n",
                        },
                    }
                ]
            else:
                assert len(changes) == (2 if case == "ordered" else 1)
                if case == "ordered":
                    assert changes[0] == {
                        "path": str(tmp_path / "b.txt"),
                        "change": {
                            "kind": "delete",
                            "content": "target-before\n",
                        },
                    }
                assert changes[-1] == {
                    "path": str(tmp_path / "a.txt"),
                    "change": {
                        "kind": "update",
                        "move_path": str(tmp_path / "b.txt"),
                        "old_content": "source-before\n",
                        "new_content": "after\n",
                        "overwritten_move_content": None
                        if case == "ordered"
                        else "target-before\n",
                    },
                }
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results
            assert all("overwritten_content" not in repr(result) for result in results)
            assert all("target-before" not in repr(result) for result in results)
        finally:
            await runtime.aclose()
        with sqlite3.connect(tmp_path / "state.db") as database:
            rows = database.execute("SELECT result_json FROM tool_executions").fetchall()
        persisted = [_result_from_json(row[0]) for row in rows]
        record = next(result for result in persisted if result.tool_name == "apply_patch")
        assert record.patch_delta_json == event.patch_delta_json
        assert record.is_error is event.is_error
        assert record.dispatch_error is False

    asyncio.run(scenario())


def test_old_approval_compiler_rejects_delta_contract_before_write(tmp_path):
    old = os.environ.get("CORKI_TEST_PRE_PATCH_DELTA_COMPILER")
    if not old:
        pytest.skip("requires real native patch approval compiler")
    from corki.execution.backend import file_operation

    with pytest.raises(ValueError, match="unknown field `native_patch_delta`"):
        asyncio.run(
            file_operation(
                workspace_policy(Path(old), tmp_path),
                tmp_path,
                "patch",
                {"patch": patch("*** Add File: never.txt\n+never")},
            )
        )
    assert not (tmp_path / "never.txt").exists()


def test_oversized_native_delta_is_unknown_after_actual_write(tmp_path, compiler):
    from corki.execution.backend import patch_operation

    target = tmp_path / "a.txt"
    target.write_text("previous content\n" * 1000)
    result = asyncio.run(
        patch_operation(
            workspace_policy(compiler, tmp_path),
            tmp_path,
            {"patch": patch("*** Add File: a.txt\n+after")},
            output_limit=3000,
        )
    )
    assert target.read_text() == "after\n"
    assert result.success is False
    assert json.loads(result.delta_json) == {"version": 1, "exact": False, "changes": []}
    assert "outcome unknown" in result.output and "Do not automatically retry" in result.output
