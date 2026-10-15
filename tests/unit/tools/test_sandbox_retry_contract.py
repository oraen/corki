"""A malformed native plan or classifier response cannot authorize a replay."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from corki.config.permissions import ExecutionPermissions
from corki.execution import backend, retry
from corki.tools.builtin import process


@pytest.mark.parametrize(
    "defect",
    [
        "false-capability",
        "string-capability",
        "integer-capability",
        "missing-plan",
        "missing-capability",
        "changed-command",
        "unknown-sandbox",
        "mismatched-sandbox",
        "missing-approval",
        "bad-canonical",
        "bad-fingerprint",
        "forbidden-policy",
    ],
)
def test_invalid_retry_plan_fails_before_first_command(tmp_path, monkeypatch, defect):
    launches = []

    async def compile(argv, payload, **kwargs):
        request = json.loads(payload)
        value = {
            "command": request["command"],
            "revision": 1,
            "profile": {"type": "disabled"},
            "sandbox": "seatbelt",
            "exec_policy_checked": True,
            "shell_approval": True,
            "exec_approval": None,
            "sandbox_retry_supported": True,
            "sandbox_retry": {
                "sandbox": "seatbelt",
                "command": request["command"],
                "approval": {"canonical_command": request["command"], "policy_fingerprint": None},
            },
        }
        if defect.endswith("capability"):
            if defect == "missing-capability":
                del value["sandbox_retry_supported"]
            else:
                value["sandbox_retry_supported"] = {
                    "false-capability": False,
                    "string-capability": "true",
                    "integer-capability": 1,
                }[defect]
        elif defect == "missing-plan":
            del value["sandbox_retry"]
        elif defect == "changed-command":
            value["sandbox_retry"]["command"] = ["unrequested-program"]
        elif defect == "unknown-sandbox":
            value["sandbox_retry"]["sandbox"] = "unknown"
        elif defect == "mismatched-sandbox":
            value["sandbox_retry"]["sandbox"] = "seccomp"
        elif defect == "missing-approval":
            value["sandbox_retry"]["approval"] = None
        elif defect == "bad-canonical":
            value["sandbox_retry"]["approval"]["canonical_command"] = []
        elif defect == "bad-fingerprint":
            value["sandbox_retry"]["approval"]["policy_fingerprint"] = [True]
        return json.dumps({"ok": value}).encode()

    async def spawn(*args, **kwargs):
        launches.append(args)
        raise AssertionError("model command cannot start with a malformed retry plan")

    monkeypatch.setattr(backend, "run_owned", compile)
    monkeypatch.setattr(process, "_spawn", spawn)

    async def scenario():
        manager = process.ProcessManager()
        try:
            with pytest.raises(ValueError):
                await manager.execute(
                    "printf unused",
                    cwd=tmp_path,
                    yield_seconds=0,
                    permissions=ExecutionPermissions(
                        Path(sys.executable),
                        tmp_path,
                        '{"type":"read-only"}',
                        approval_policy_json='"never"'
                        if defect == "forbidden-policy"
                        else '"untrusted"',
                    ),
                )
            assert not launches and not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        {"error": "failed"},
        {"ok": {}},
        {"ok": {"revision": True, "sandbox_denial": True}},
        {"ok": {"revision": 1, "sandbox_denial": "true"}},
        {"ok": {"revision": 1, "sandbox_denial": 1}},
    ],
)
def test_invalid_classifier_ack_is_not_a_denial_or_retry(tmp_path, monkeypatch, response):
    async def classify(*args, **kwargs):
        return json.dumps(response).encode()

    monkeypatch.setattr(retry, "run_owned", classify)
    permissions = ExecutionPermissions(Path(sys.executable), tmp_path, '{"type":"read-only"}')
    with pytest.raises(ValueError):
        asyncio.run(retry.is_sandbox_denial(permissions, "seatbelt", 1, "Permission denied"))
