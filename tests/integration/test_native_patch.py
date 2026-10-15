"""Pinned core verification and native partial-commit semantics, through Runtime."""

import asyncio
import os
from pathlib import Path

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_permissions import _run
from test_filesystem_helper_runtime import workspace_policy

from corki.execution.backend import file_operation


def patch(body):
    return "*** Begin Patch\n" + body + "\n*** End Patch"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("operation", ["add", "move", "absolute", "padded"])
def test_native_patch_accepts_reference_overwrite_and_parser_behavior(
    tmp_path, compiler, nested, operation
):
    destination = tmp_path / "destination.txt"
    destination.write_text("existing\n")
    original = tmp_path / "source.txt"
    original.write_text("old\n")
    if operation == "move":
        body = "*** Update File: source.txt\n*** Move to: destination.txt\n@@\n-old\n+new"
    else:
        name = str(destination) if operation == "absolute" else "destination.txt"
        body = f"*** Add File: {name}\n+new"
    text = patch(body)
    if operation == "padded":
        text = "  " + text + "  \n"
    results = asyncio.run(
        _run(tmp_path, workspace_policy(compiler, tmp_path), "apply_patch", {"patch": text}, nested)
    )
    assert destination.read_text() == "new\n", results
    assert original.exists() is (operation != "move")
    assert any("Success. Updated the following files:" in result.content for result in results)


@pytest.mark.parametrize("case", ["missing_update", "duplicate", "invalid"])
def test_verification_finishes_before_any_write(tmp_path, compiler, case):
    bodies = {
        "missing_update": (
            "*** Add File: created.txt\n+first\n*** Update File: missing.txt\n@@\n-old\n+new"
        ),
        "duplicate": "*** Add File: created.txt\n+first\n*** Add File: created.txt\n+second",
        "invalid": "*** Add File: created.txt\n+first\n*** Unknown Directive",
    }
    with pytest.raises(ValueError, match="verification failed"):
        asyncio.run(
            file_operation(
                workspace_policy(compiler, tmp_path),
                tmp_path,
                "patch",
                {"patch": patch(bodies[case])},
            )
        )
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_failed_write_retains_prefix_and_reports_uncertain_delta(tmp_path, compiler, nested):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # All paths are authorized; an actual write fails after the first succeeds.
    # Core safety admission must reject outside paths BEFORE any writes instead.
    denied = workspace / "directory.txt"
    denied.mkdir()
    text = patch("*** Add File: created.txt\n+first\n*** Add File: directory.txt\n+denied")
    results = asyncio.run(
        _run(
            workspace, workspace_policy(compiler, workspace), "apply_patch", {"patch": text}, nested
        )
    )
    assert (workspace / "created.txt").read_text() == "first\n"
    assert denied.is_dir()
    error = "\n".join(result.content for result in results)
    assert "committed changes: 1" in error and "delta exact: false" in error
    assert "no rollback performed" in error and "created.txt" in error
    if not nested:
        assert results[-1].is_error


def test_whole_patch_permission_check_precedes_first_write(tmp_path, compiler):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    text = patch(f"*** Add File: created.txt\n+first\n*** Add File: {outside}\n+denied")
    with pytest.raises(ValueError, match="patch rejected"):
        asyncio.run(
            file_operation(
                workspace_policy(compiler, workspace), workspace, "patch", {"patch": text}
            )
        )
    assert not outside.exists() and not (workspace / "created.txt").exists()


def test_default_update_mode_normalizes_line_endings(tmp_path, compiler):
    path = tmp_path / "lines.txt"
    path.write_bytes(b"before\r\nkeep\r\n")
    asyncio.run(
        file_operation(
            workspace_policy(compiler, tmp_path),
            tmp_path,
            "patch",
            {"patch": patch("*** Update File: lines.txt\n@@\n-before\n+after\n keep")},
        )
    )
    assert path.read_bytes() == b"after\nkeep\n"


def test_old_read_only_helper_cannot_silently_accept_patch(tmp_path):
    old = os.environ.get("CORKI_TEST_PRE_NATIVE_PATCH_COMPILER")
    if not old:
        pytest.skip("requires real native-read-only compiler")
    with pytest.raises(ValueError, match="unknown field `native_patch`"):
        asyncio.run(
            file_operation(
                workspace_policy(Path(old), tmp_path),
                tmp_path,
                "patch",
                {"patch": patch("*** Add File: forbidden.txt\n+never")},
            )
        )
    assert not (tmp_path / "forbidden.txt").exists()
