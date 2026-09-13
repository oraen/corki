"""Installation uses the compiler's effective authority, never Python path heuristics."""

import asyncio
from types import SimpleNamespace

import pytest

from corki.execution import backend


@pytest.mark.parametrize("policy", ['"never"', '"on-request"'])
@pytest.mark.parametrize("full_write", [False, True])
def test_native_write_classification_and_effective_policy(
    monkeypatch, tmp_path, policy, full_write
):
    permissions = SimpleNamespace(policy_cwd=tmp_path)

    async def compile(actual, command, cwd, **kwargs):
        assert actual is permissions and cwd == tmp_path
        assert kwargs == {"resolve_requirements": True}
        return SimpleNamespace(
            full_disk_write_access=full_write,
            effective_approval_policy_json=policy,
        )

    monkeypatch.setattr(backend, "_compile", compile)
    effective, automatic = asyncio.run(backend.mcp_dependency_approval(permissions))
    assert effective == ("never" if policy == '"never"' else "on-request")
    assert automatic == (full_write and effective == "never")


def test_old_compiler_cannot_implicitly_grant_installation(monkeypatch, tmp_path):
    async def compile(*args, **kwargs):
        return SimpleNamespace(full_disk_write_access=None)

    monkeypatch.setattr(backend, "_compile", compile)
    with pytest.raises(ValueError, match="cannot classify MCP installation"):
        asyncio.run(backend.mcp_dependency_approval(SimpleNamespace(policy_cwd=tmp_path)))
