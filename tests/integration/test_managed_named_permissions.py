import asyncio

import pytest
from test_named_execution_permissions import run

from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements


@pytest.mark.parametrize("managed_child", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_configured_and_managed_profiles_inherit_both_directions(tmp_path, managed_child, nested):
    (tmp_path / "allowed").mkdir()
    parent = """
[permissions.parent]
extends=":read-only"
[permissions.parent.filesystem.":workspace_roots"]
allowed="write"
"""
    child = """
[permissions.child]
extends="parent"
"""
    requirements = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                """
default_permissions="child"
[allowed_permission_profiles]
child=true
"""
                + (child if managed_child else parent),
            ),
        )
    )
    user = parent if managed_child else 'default_permissions="child"\n' + child
    permissions, results = asyncio.run(
        run(tmp_path, user, requirements=requirements, nested=nested)
    )
    assert permissions.active_profile.id == "child"
    # Native declaration metadata comes from the configured (not merged) table.
    assert permissions.active_profile.extends == (None if managed_child else "parent")
    assert (tmp_path / "allowed/inside.txt").read_text() == "created"
    assert not (tmp_path / "outside.txt").exists()
    assert any("outside.txt=denied" in item.content for item in results)


def test_disallowed_id_falls_back_before_compiling_permissions(tmp_path):
    (tmp_path / "allowed").mkdir()
    requirements = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                """
default_permissions="managed-read"
[allowed_permission_profiles]
managed-read=true
[permissions.managed-read]
extends=":read-only"
""",
            ),
        )
    )
    permissions, _ = asyncio.run(
        run(tmp_path, 'default_permissions=":danger-full-access"', requirements=requirements)
    )
    assert permissions.active_profile.id == "managed-read"
    assert not (tmp_path / "outside.txt").exists()


def test_standard_pair_supplies_workspace_default(tmp_path):
    (tmp_path / "allowed").mkdir()
    requirements = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                """
[allowed_permission_profiles]
":read-only"=true
":workspace"=true
""",
            ),
        )
    )
    permissions, _ = asyncio.run(run(tmp_path, "", requirements=requirements))
    assert permissions.active_profile.id == ":workspace"
    assert (tmp_path / "outside.txt").read_text() == "created"


@pytest.mark.parametrize(
    ("user", "managed", "message"),
    [
        ("", 'default_permissions=":read-only"', "requires allowed_permission_profiles"),
        ("", '[allowed_permission_profiles]\n":read-only"=true', "default_permissions must be set"),
        (
            "",
            'default_permissions=":workspace"\n[allowed_permission_profiles]\n":read-only"=true',
            "must be allowed",
        ),
        (
            "",
            '[allowed_permission_profiles]\n":read-only"=true\n":workspace"=true\nmissing=false',
            "undefined profile",
        ),
        (
            'default_permissions="same"\n[permissions.same]\nextends=":read-only"',
            '[permissions.same]\nextends=":workspace"',
            "conflicts with a config-defined",
        ),
        (
            'default_permissions="child"\n[permissions.parent]\nextends="child"',
            '[permissions.child]\nextends="parent"',
            "cycle",
        ),
    ],
)
def test_invalid_managed_catalog_fails_before_model(tmp_path, user, managed, message):
    requirements = compose_mcp_requirements((MCPRequirementsLayer("host", managed),))
    with pytest.raises(ValueError, match=message):
        asyncio.run(run(tmp_path, user, requirements=requirements))


def test_higher_managed_profile_table_keeps_lower_fields(tmp_path):
    (tmp_path / "allowed").mkdir()
    requirements = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "low",
                """
default_permissions="build"
[allowed_permission_profiles]
build=true
[permissions.build]
extends=":read-only"
description="old"
[permissions.build.filesystem.":workspace_roots"]
allowed="write"
""",
            ),
            MCPRequirementsLayer("high", '[permissions.build]\ndescription="new"'),
        )
    )
    permissions, _ = asyncio.run(run(tmp_path, "", requirements=requirements))
    assert permissions.active_profile.id == "build"
    assert (tmp_path / "allowed/inside.txt").read_text() == "created"
    assert not (tmp_path / "outside.txt").exists()


def test_worker_override_keeps_catalog_without_reselecting_parent(tmp_path):
    import json
    import shlex
    import sys
    from dataclasses import replace

    from test_named_execution_permissions import settings

    from corki.execution.backend import resolve_execution_permissions
    from corki.memory.agent import run_agent
    from corki.memory.permissions import MemoryPermissionSnapshot
    from corki.models import ModelCompleted
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
    from corki.protocol.tools import ToolCall

    configured = replace(
        settings(
            tmp_path,
            """
default_permissions="user-read"
[permissions.user-read]
extends=":read-only"
""",
        ),
        skills_enabled=False,
    )
    requirements = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                """
default_permissions="user-read"
[allowed_permission_profiles]
user-read=true
""",
            ),
        )
    )
    observed = []

    class Model:
        async def stream(self, request):
            turn, step = request.items[-1].turn_id, new_step_id()
            results = [item for item in request.items if isinstance(item, ToolResultItem)]
            if not results:
                command = shlex.join(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        "from pathlib import Path; Path('worker-ok.txt').write_text('owned'); "
                        "print('WORKER_WRITE_OK')",
                    ]
                )
                item = ToolCallItem(
                    ToolCall(new_tool_call_id(), "exec_command", {"cmd": command, "login": False}),
                    turn,
                    step,
                )
            else:
                observed.extend(results)
                item = AssistantMessageItem(
                    json.dumps({"memory": "done", "memory_summary": "done", "skills": []}),
                    turn,
                    step,
                )
            yield ModelCompleted((item,))

        async def aclose(self):
            pass

    async def scenario():
        parent, _ = await resolve_execution_permissions(
            replace(configured.execution_permissions, requirements=requirements.execution)
        )
        assert parent.active_profile.id == "user-read"
        assert parent.select_from_config and parent.catalog_json
        result = await run_agent(
            settings=replace(configured, memories_consolidation_model="fixture"),
            model=Model(),
            sampled={},
            workspace_diff="",
            instructions="exercise child policy",
            home_path=tmp_path / "home",
            parent_permissions=MemoryPermissionSnapshot(parent, requirements),
        )
        assert json.loads(result)["memory"] == "done"
        assert any("WORKER_WRITE_OK" in item.content for item in observed), observed
        assert all(not item.is_error for item in observed)

    asyncio.run(scenario())


@pytest.mark.parametrize("restrict_mode", [False, True])
def test_readmission_uses_retained_configuration_and_current_constraints(tmp_path, restrict_mode):
    from dataclasses import replace

    from test_named_execution_permissions import settings

    from corki.execution.backend import resolve_execution_permissions

    configured = settings(
        tmp_path,
        'default_permissions="build"\n[permissions.build]\nextends=":workspace"',
    ).execution_permissions
    initial = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                '[allowed_permission_profiles]\nbuild=true\n":workspace"=true\n":read-only"=true',
            ),
        )
    )
    changed = compose_mcp_requirements(
        (
            MCPRequirementsLayer(
                "host",
                'allowed_sandbox_modes=["read-only"]'
                if restrict_mode
                else 'default_permissions=":read-only"\n[allowed_permission_profiles]\n'
                '":read-only"=true\nbuild=false',
            ),
        )
    )

    async def scenario():
        parent, _ = await resolve_execution_permissions(
            replace(configured, requirements=initial.execution)
        )
        assert parent.active_profile.id == "build"
        assert parent.catalog_json == configured.profile_json
        repeated, _ = await resolve_execution_permissions(parent)
        assert repeated == parent
        restricted, warnings = await resolve_execution_permissions(
            replace(parent, requirements=changed.execution)
        )
        assert warnings
        if restrict_mode:
            assert restricted.active_profile is None
        else:
            assert restricted.active_profile.id == ":read-only"
        # Even a constraint fallback must not replace the retained declaration:
        # a new host admission uses current constraints and the original selection.
        restored, _ = await resolve_execution_permissions(
            replace(restricted, requirements=initial.execution)
        )
        assert restored == parent

    asyncio.run(scenario())
