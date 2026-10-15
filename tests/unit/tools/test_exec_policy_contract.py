"""Old/spoofed-incomplete bridge contracts cannot silently omit execution checks."""

import asyncio
import json
import sys

import pytest

from corki.config.permissions import ExecutionPermissions
from corki.tools.builtin.process import ProcessManager


@pytest.mark.parametrize("ack", [None, False, "true", 1])
@pytest.mark.parametrize("feature", ["escalation", "rules"])
def test_requested_exec_feature_requires_typed_compiler_ack_before_launch(
    tmp_path, monkeypatch, ack, feature
):
    from pathlib import Path

    from corki.execution import backend
    from corki.tools.builtin import process

    async def scenario():
        launches = []

        async def compiler(argv, payload, **kwargs):
            request = json.loads(payload)
            if feature == "escalation":
                assert request["sandbox_permissions"] == "require_escalated"
            else:
                assert request["exec_policy_options"]["prefix_rule"] == ["printf"]
            return json.dumps(
                {
                    "ok": {
                        "command": request["command"],
                        "revision": 1,
                        "profile": {"type": "disabled"},
                        "exec_policy_checked": True,
                        "shell_approval": True,
                        "exec_approval": None,
                        "model_escalation": ack,
                        "exec_policy_amendments": ack,
                    }
                }
            ).encode()

        async def spawn(*args, **kwargs):
            launches.append(args)
            raise AssertionError("model command must not start")

        monkeypatch.setattr(backend, "run_owned", compiler)
        monkeypatch.setattr(process, "_spawn", spawn)
        manager = ProcessManager()
        try:
            expected = (
                "model escalation" if feature == "escalation" else "execution rule amendments"
            )
            with pytest.raises(ValueError, match="does not support " + expected):
                await manager.execute(
                    "printf unused",
                    cwd=tmp_path,
                    yield_seconds=0,
                    permissions=ExecutionPermissions(
                        Path(sys.executable),
                        tmp_path,
                        '{"type":"disabled"}',
                        approval_policy_json='"on-request"',
                    ),
                    **(
                        {"sandbox_permissions": "require_escalated"}
                        if feature == "escalation"
                        else {"prefix_rule": ["printf"]}
                    ),
                )
            assert not launches and not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


def test_model_override_without_configured_backend_does_not_launch(tmp_path, monkeypatch):
    from corki.tools.builtin import process

    async def scenario():
        async def spawn(*args, **kwargs):
            raise AssertionError("model command must not start")

        monkeypatch.setattr(process, "_spawn", spawn)
        manager = ProcessManager()
        try:
            with pytest.raises(ValueError, match="requires a configured execution"):
                await manager.execute(
                    "printf unused",
                    cwd=tmp_path,
                    yield_seconds=0,
                    sandbox_permissions="require_escalated",
                )
            assert not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "patch",
    [
        {"shell_approval": None, "exec_approval": None},
        {"shell_approval": False, "exec_approval": None},
        {"shell_approval": "true", "exec_approval": None},
        {"shell_approval": 1, "exec_approval": None},
        {"shell_approval": True},
        {"shell_approval": True, "exec_approval": False},
        {"shell_approval": True, "exec_approval": {"reason": "missing fingerprint"}},
        {"shell_approval": True, "exec_approval": {"reason": 4, "policy_fingerprint": None}},
        {"shell_approval": True, "exec_approval": {"reason": None, "policy_fingerprint": [1]}},
        *[
            {
                "shell_approval": True,
                "exec_policy_amendments": True,
                "exec_approval": {"reason": None, "policy_fingerprint": None, **fields},
            }
            for fields in [
                {},
                {"canonical_command": None, "proposed_execpolicy_amendment": None},
                {"canonical_command": [], "proposed_execpolicy_amendment": None},
                {"canonical_command": [1], "proposed_execpolicy_amendment": None},
                {"canonical_command": ["printf"], "proposed_execpolicy_amendment": "printf"},
                {"canonical_command": ["printf"], "proposed_execpolicy_amendment": []},
            ]
        ],
    ],
)
def test_shell_review_contract_cannot_silently_become_executable(tmp_path, monkeypatch, patch):
    from pathlib import Path

    from corki.execution import backend
    from corki.tools.builtin import process

    async def scenario():
        launches = []

        async def compiler(argv, payload, **kwargs):
            request = json.loads(payload)
            assert request["approval_policy"] == "on-request"
            return json.dumps(
                {
                    "ok": {
                        "command": request["command"],
                        "revision": 1,
                        "profile": {"type": "disabled"},
                        "exec_policy_checked": True,
                        **patch,
                    }
                }
            ).encode()

        async def spawn(*args, **kwargs):
            launches.append(args)
            raise AssertionError("model command must not start")

        monkeypatch.setattr(backend, "run_owned", compiler)
        monkeypatch.setattr(process, "_spawn", spawn)
        manager = ProcessManager()
        try:
            with pytest.raises(ValueError, match="shell approval|invalid response"):
                await manager.execute(
                    "printf unused",
                    cwd=tmp_path,
                    yield_seconds=0,
                    permissions=ExecutionPermissions(
                        Path(sys.executable),
                        tmp_path,
                        '{"type":"disabled"}',
                        approval_policy_json='"on-request"',
                    ),
                )
            assert not launches and not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("ack", [None, False, "true", 1])
def test_missing_or_untyped_admission_ack_prevents_process_creation(tmp_path, monkeypatch, ack):
    from corki.tools.builtin import process

    async def scenario():
        compiler = tmp_path / "compiler"
        compiler.write_text(
            f"#!{sys.executable}\n"
            "import json,sys\n"
            "request=json.loads(sys.stdin.readline())\n"
            "response={'command': request['command'], 'revision': 1, "
            "'profile': {'type':'disabled'}}\n"
            f"response.update(json.loads({json.dumps({'exec_policy_checked': ack})!r}))\n"
            "print(json.dumps({'ok':response}))\n"
        )
        compiler.chmod(0o700)
        spawned = []

        async def forbidden_spawn(*args, **kwargs):
            spawned.append(args)
            raise AssertionError("model process must not be created")

        monkeypatch.setattr(process, "_spawn", forbidden_spawn)
        manager = ProcessManager()
        try:
            with pytest.raises(ValueError, match="does not support exec policy admission"):
                await manager.execute(
                    "printf fixture",
                    cwd=tmp_path,
                    yield_seconds=0.1,
                    permissions=ExecutionPermissions(compiler, tmp_path, '{"type":"disabled"}'),
                )
            assert not spawned and not manager._sessions and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("ack", [None, False, "true", 1])
def test_startup_requires_typed_loading_ack(tmp_path, monkeypatch, ack):
    from corki.execution import backend

    async def compiler(argv, payload, **kwargs):
        request = json.loads(payload)
        assert request["load_exec_policy"]["config_folders"] == [str(tmp_path)]
        return json.dumps(
            {
                "ok": {
                    "revision": 1,
                    "command": request["command"],
                    "profile": {"type": "disabled"},
                    "managed_requirements": True,
                    "exec_policy_loaded": ack,
                    "exec_policy_sources": [],
                }
            }
        ).encode()

    monkeypatch.setattr(backend, "run_owned", compiler)
    permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
    with pytest.raises(ValueError, match="does not support exec policy loading"):
        asyncio.run(
            backend.resolve_execution_permissions(
                permissions, exec_policy_config_folders=(tmp_path,)
            )
        )


@pytest.mark.parametrize(
    "sources",
    [None, {}, [{"name": "/rules"}], [{"name": "relative.rules", "contents": ""}]],
)
def test_startup_rejects_malformed_rule_snapshot(tmp_path, monkeypatch, sources):
    from corki.execution import backend

    async def compiler(argv, payload, **kwargs):
        return json.dumps(
            {
                "ok": {
                    "revision": 1,
                    "command": [sys.executable],
                    "profile": {"type": "disabled"},
                    "managed_requirements": True,
                    "exec_policy_loaded": True,
                    "exec_policy_sources": sources,
                    "exec_policy_identity": None,
                }
            }
        ).encode()

    monkeypatch.setattr(backend, "run_owned", compiler)
    permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"disabled"}')
    with pytest.raises(ValueError):
        asyncio.run(
            backend.resolve_execution_permissions(
                permissions, exec_policy_config_folders=(tmp_path,)
            )
        )


@pytest.mark.parametrize("ack", [None, False, "true", 1])
def test_managed_rules_require_typed_compiler_ack(tmp_path, monkeypatch, ack):
    from corki.config.execution_requirements import ExecutionRequirementsLayer
    from corki.execution import backend

    async def compiler(argv, payload, **kwargs):
        return json.dumps({"ok": {"managed_exec_policy": ack}}).encode()

    monkeypatch.setattr(backend, "run_owned", compiler)
    permissions = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path,
        '{"type":"disabled"}',
        requirements=(
            ExecutionRequirementsLayer(
                "managed",
                json.dumps(
                    {
                        "rules": {
                            "prefix_rules": [
                                {"pattern": [{"token": "printf"}], "decision": "forbidden"}
                            ]
                        }
                    }
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="does not support managed exec policy"):
        asyncio.run(backend.resolve_execution_permissions(permissions))


@pytest.mark.parametrize("identity", [None, [], ["source"], ["source", 1], "opaque"])
def test_managed_rules_cannot_lose_effective_identity(tmp_path, monkeypatch, identity):
    from corki.config.execution_requirements import ExecutionRequirementsLayer
    from corki.execution import backend

    async def compiler(argv, payload, **kwargs):
        return json.dumps(
            {"ok": {"managed_exec_policy": True, "exec_policy_identity": identity}}
        ).encode()

    monkeypatch.setattr(backend, "run_owned", compiler)
    permissions = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path,
        '{"type":"disabled"}',
        requirements=(
            ExecutionRequirementsLayer(
                "managed",
                json.dumps(
                    {
                        "rules": {
                            "prefix_rules": [
                                {"pattern": [{"token": "printf"}], "decision": "forbidden"}
                            ]
                        }
                    }
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="invalid response"):
        asyncio.run(backend.resolve_execution_permissions(permissions))
