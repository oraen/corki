"""Incomplete native acknowledgements must fail before any model command runs."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.permissions import ExecutionPermissions
from corki.execution import backend
from corki.tools.builtin import process
from corki.tools.builtin.process import ProcessManager


@pytest.mark.parametrize("operation", ["startup", "command"])
@pytest.mark.parametrize(
    "defect", ["absent", None, False, "true", 1, "missing_policy", "invalid_policy", "drift"]
)
def test_managed_approval_ack_is_typed_and_required(tmp_path, monkeypatch, operation, defect):
    async def scenario():
        launches = []

        async def compiler(argv, payload, **kwargs):
            request = json.loads(payload)
            assert request["approval_policy"] == "on-request"
            assert request["approval_policy_explicit"] is True
            assert request["approval_policy_constraint"] == "configured"
            value = {
                "command": request["command"],
                "revision": 1,
                "profile": {"type": "disabled"},
                "shell_approval": True,
                "exec_policy_checked": True,
                "exec_approval": None,
                "managed_requirements": True,
                "managed_catalog": True,
                "managed_approval": True,
                "effective_approval_policy": "on-request",
            }
            if defect == "absent":
                del value["managed_approval"]
            elif defect == "missing_policy":
                del value["effective_approval_policy"]
            elif defect == "invalid_policy":
                value["effective_approval_policy"] = {"granular": {"rules": "true"}}
            elif defect == "drift":
                value["effective_approval_policy"] = "never"
            else:
                value["managed_approval"] = defect
            return json.dumps({"ok": value}).encode()

        async def spawn(*args, **kwargs):
            launches.append(args)
            raise AssertionError("command must not launch")

        monkeypatch.setattr(backend, "run_owned", compiler)
        monkeypatch.setattr(process, "_spawn", spawn)
        permissions = ExecutionPermissions(
            Path(sys.executable),
            tmp_path,
            '{"type":"disabled"}',
            requirements=(
                ExecutionRequirementsLayer(
                    "organization", '{"allowed_approval_policies":["never"]}'
                ),
            ),
            approval_policy_json='"on-request"',
        )
        manager = ProcessManager()
        try:
            if defect == "drift" and operation == "startup":
                result, _ = await backend.resolve_execution_permissions(permissions)
                assert result.approval_policy_json == '"never"'
                assert result.requested_approval_policy_json == '"on-request"'
            else:
                with pytest.raises(ValueError):
                    if operation == "startup":
                        await backend.resolve_execution_permissions(permissions)
                    else:
                        await manager.execute(
                            "printf unused", cwd=tmp_path, yield_seconds=0, permissions=permissions
                        )
            assert not launches and not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_policy_explicit", 1),
        ("approval_policy_constraint", "model"),
        ("requested_approval_policy_json", '"invalid"'),
        ("approval_policy_json", '"on-request"'),
        ("requested_approval_policy_json", '"on-request"'),
    ],
)
@pytest.mark.parametrize("mode", ["memory", "guardian"])
def test_memory_constraint_cannot_be_relaxed_in_host_snapshot(tmp_path, field, value, mode):
    kwargs = {"approval_policy_constraint": mode, field: value}
    with pytest.raises(ValueError):
        ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"disabled"}', **kwargs)
