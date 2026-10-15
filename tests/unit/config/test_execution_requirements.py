import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest

from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.permissions import ExecutionPermissions
from corki.execution import backend


@pytest.mark.parametrize(
    "contents",
    [
        'allowed_sandbox_modes="read-only"',
        "allowed_sandbox_modes=[1]",
        "[permissions.named]\nnetwork=false",
        "[permissions.filesystem]\nnetwork=false",
        '[permissions.filesystem]\ndeny_read="private"',
        "[permissions.filesystem]\ndeny_read=[1]",
        'allowed_approval_policies="never"',
        'allowed_approvals_reviewers=["user"]',
        'allowed_permission_profiles=["build"]',
        "[allowed_permission_profiles]\nbuild=1",
        "default_permissions=false",
        "permissions={build=false}",
    ],
)
def test_unimplemented_or_malformed_managed_domains_still_fail_closed(contents):
    with pytest.raises(ValueError, match="bad-layer"):
        compose_mcp_requirements((MCPRequirementsLayer("bad-layer", contents),))


def test_sources_and_fragments_are_immutable_and_not_merged_as_arrays(tmp_path):
    low = MCPRequirementsLayer("low", '[permissions.filesystem]\ndeny_read=["./private"]', tmp_path)
    high = MCPRequirementsLayer("high", "[permissions.filesystem]\ndeny_read=[]")
    snapshot = compose_mcp_requirements((low, high))
    assert len(snapshot.execution) == 2
    assert snapshot.execution[0].base_dir == tmp_path
    assert json.loads(snapshot.execution[0].value_json)["permissions"]["filesystem"][
        "deny_read"
    ] == ["./private"]
    with pytest.raises(FrozenInstanceError):
        snapshot.execution[0].value_json = "{}"


def test_old_compiler_cannot_acknowledge_managed_authority(tmp_path, monkeypatch):
    async def old_compiler(*args, **kwargs):
        return json.dumps(
            {
                "ok": {
                    "command": ["must-not-run"],
                    "revision": 1,
                    "profile": {"type": "disabled"},
                    "memory_derivation": True,
                }
            }
        ).encode()

    monkeypatch.setattr(backend, "run_owned", old_compiler)
    permissions = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path,
        '{"type":"read-only"}',
        (ExecutionRequirementsLayer("host", '{"allowed_sandbox_modes":["read-only"]}'),),
    )
    with pytest.raises(ValueError, match="does not support managed requirements"):
        asyncio.run(backend.resolve_execution_permissions(permissions))


def test_previous_named_compiler_cannot_silently_ignore_managed_catalog(tmp_path, monkeypatch):
    async def previous_compiler(*args, **kwargs):
        return json.dumps(
            {
                "ok": {
                    "command": ["must-not-run"],
                    "revision": 1,
                    "profile": {"type": "disabled"},
                    "memory_derivation": True,
                    "managed_requirements": True,
                    "named_selection": True,
                }
            }
        ).encode()

    monkeypatch.setattr(backend, "run_owned", previous_compiler)
    permissions = ExecutionPermissions(tmp_path / "compiler", tmp_path, '{"type":"selection"}')
    with pytest.raises(ValueError, match="does not support managed permission catalogs"):
        asyncio.run(backend.resolve_execution_permissions(permissions))
