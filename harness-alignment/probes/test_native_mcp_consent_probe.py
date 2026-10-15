"""Direct native classifications supplement the Runtime install/skip assertions."""

import asyncio
import json

import pytest

from corki.config.permissions import ExecutionPermissions
from corki.execution.backend import mcp_dependency_approval
from corki.execution.bundled import bundled_compiler


@pytest.mark.parametrize("kind", ["unrestricted", "root", "narrow", "read_only", "external"])
@pytest.mark.parametrize("policy", ["never", "on-request"])
def test_native_consent_classification_returns_a_decision(tmp_path, kind, policy):
    root = {"access": "write", "path": {"type": "special", "value": {"kind": "root"}}}
    entries = [root]
    if kind == "narrow":
        entries.append(
            {"access": "read", "path": {"type": "path", "path": str(tmp_path / "read-only")}}
        )
    if kind == "read_only":
        root["access"] = "read"
    profile = {
        "type": "managed",
        "network": "restricted",
        "file_system": {"type": "unrestricted"}
        if kind == "unrestricted"
        else {"type": "restricted", "entries": entries},
    }
    if kind == "external":
        profile = {"type": "external", "network": "restricted"}
    permissions = ExecutionPermissions(
        bundled_compiler(),
        tmp_path,
        json.dumps(profile),
        approval_policy_json=json.dumps(policy),
    )
    # Exceptions are failures here, not interpreted as a denial/skip.
    effective, automatic = asyncio.run(mcp_dependency_approval(permissions))
    assert effective == policy
    assert automatic == (policy == "never" and kind in {"unrestricted", "root", "external"})
