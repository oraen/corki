"""Narrow user permissions must still bootstrap the fixed filesystem helper."""

import asyncio
import json

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_permissions import _run

from corki.config.permissions import ExecutionPermissions
from corki.execution.backend import file_operation


def workspace_policy(compiler, root):
    return ExecutionPermissions(
        compiler,
        root,
        json.dumps(
            {
                "type": "managed",
                "network": "restricted",
                "file_system": {
                    "type": "restricted",
                    "entries": [
                        {
                            "access": "write",
                            "path": {"type": "path", "path": str(root)},
                        }
                    ],
                },
            }
        ),
    )


PATCH = "*** Begin Patch\n*** Add File: allowed.txt\n+allowed\n*** End Patch"


def test_fixed_file_helper_bootstraps_with_workspace_only_read_access(tmp_path, compiler):
    policy = workspace_policy(compiler, tmp_path)
    result = asyncio.run(file_operation(policy, tmp_path, "patch", {"patch": PATCH}))
    assert result == "Success. Updated the following files:\nA allowed.txt\n"
    assert (tmp_path / "allowed.txt").read_text() == "allowed\n"


@pytest.mark.parametrize("nested", [False, True])
def test_narrow_runtime_can_read_project_instructions_then_patch(tmp_path, compiler, nested):
    (tmp_path / "AGENTS.md").write_text("Keep all model changes inside this workspace.")
    policy = workspace_policy(compiler, tmp_path)
    results = asyncio.run(_run(tmp_path, policy, "apply_patch", {"patch": PATCH}, nested))
    assert (tmp_path / "allowed.txt").read_text() == "allowed\n", results
