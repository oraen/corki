"""Native requirements must publish one effective authority to Runtime and tools."""

import asyncio
import json
import shlex
from dataclasses import replace

import pytest
from test_execution_policy_live_inheritance import Model, run, runtime_for
from test_execution_policy_live_inheritance import compiler as compiler

from corki.config import CorkiSettings
from corki.config.execution_requirements import ExecutionRequirementsLayer
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.permissions import ExecutionPermissions
from corki.execution.backend import resolve_execution_permissions, sandbox_command
from corki.execution.owned_process import run_owned
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import WarningEvent
from corki.protocol.items import ContextItem, ToolResultItem
from corki.protocol.session_source import SessionSource


def layer(policies, name="organization", **values):
    return ExecutionRequirementsLayer(
        name, json.dumps({"allowed_approval_policies": policies, **values})
    )


@pytest.mark.parametrize(
    "policies,valid",
    [
        (([], ["never"]), True),
        ((["never"], []), False),
        ((["unknown"], ["never"]), False),
        (([False], ["never"]), False),
    ],
)
def test_native_layer_validation_confirms_early_host_guard(tmp_path, compiler, policies, valid):
    async def scenario():
        request = {
            "legacy": {"type": "read-only"},
            "cwd": str(tmp_path),
            "policy_cwd": str(tmp_path),
            "command": ["/usr/bin/true"],
            "resolve_requirements": True,
            "requirements": [
                {"source": source, "value": {"allowed_approval_policies": values}}
                for source, values in zip(("low", "high"), policies, strict=True)
            ],
        }
        response = json.loads(
            await run_owned(
                [str(compiler)],
                (json.dumps(request) + "\n").encode(),
                cwd=tmp_path,
                output_limit=1_000_000,
            )
        )
        if valid:
            assert response["ok"]["effective_approval_policy"] == "never"
        else:
            assert "error" in response and "ok" not in response

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_runtime_applies_higher_managed_policy_before_effective_empty_check(
    tmp_path, compiler, mode
):
    async def scenario():
        model = Model(mode)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            model="fixture",
            tool_mode=mode,
            model_contexts=(ModelContextInfo(model="fixture"),),
            execution_permissions=ExecutionPermissions(
                compiler,
                tmp_path,
                '{"type":"read-only"}',
                requirements=(layer([], "lower-host"),),
            ),
        )
        managed = compose_mcp_requirements(
            [MCPRequirementsLayer("higher-system", 'allowed_approval_policies=["on-request"]')]
        )
        runtime = runtime_for(
            tmp_path, compiler, model, settings=settings, mcp_requirements=managed
        )
        try:
            await run(runtime, model, {"cmd": "printf composed-policy"})
            assert runtime._settings.execution_permissions.approval_policy_json == '"on-request"'
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert results and not results[-1].is_error and "composed-policy" in results[-1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_required_interactive_policy_reaches_real_approval_owner(tmp_path, compiler, mode):
    async def scenario():
        model = Model(mode)
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            model="fixture",
            tool_mode=mode,
            model_contexts=(ModelContextInfo(model="fixture"),),
            execution_permissions=ExecutionPermissions(
                compiler,
                tmp_path,
                '{"type":"read-only"}',
                requirements=(layer(["on-request"]),),
            ),
        )
        runtime = runtime_for(tmp_path, compiler, model, settings=settings)
        target = tmp_path / "approved-write"
        prompts = []

        async def approve(request):
            assert not target.exists()
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            await run(
                runtime,
                model,
                {
                    "cmd": "touch " + shlex.quote(str(target)),
                    "sandbox_permissions": "require_escalated",
                    "justification": "fixture scoped write",
                },
            )
            assert target.exists() and len(prompts) == 1
            assert runtime._settings.execution_permissions.approval_policy_json == '"on-request"'
            context = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            assert "Approval policy is currently never" not in context
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_only_internal_guardian_replaces_parent_approval_constraint(
    tmp_path, compiler, typed, mode
):
    async def scenario():
        requested = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"read-only"}',
            approval_policy_json='"on-request"',
            requirements=(layer(["on-request"]),),
        )
        parent, _ = await resolve_execution_permissions(requested)
        model = Model(mode)
        runtime = runtime_for(
            tmp_path,
            compiler,
            model,
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=parent,
                tool_mode=mode,
                model="fixture",
                model_contexts=(ModelContextInfo(model="fixture"),),
            ),
            session_source=SessionSource.internal("guardian")
            if typed
            else SessionSource.from_startup_arg("guardian"),
        )
        try:
            events = await run(runtime, model, {"cmd": "printf guardian-fixture"})
            resolved = runtime._settings.execution_permissions
            assert resolved.approval_policy_json == ('"never"' if typed else '"on-request"')
            assert resolved.approval_policy_constraint == ("guardian" if typed else "configured")
            results = [
                item for item in model.requests[-1].items if isinstance(item, ToolResultItem)
            ]
            assert results and not results[-1].is_error
            assert "guardian-fixture" in results[-1].content
            context = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            assert ("Approval policy is currently never" in context) is typed
            assert not [
                e
                for e in events
                if isinstance(e, WarningEvent) and "`approval_policy`" in e.message
            ]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("explicit", [False, True])
def test_managed_default_controls_tools_context_and_startup_warning(
    tmp_path, compiler, trust, mode, explicit
):
    async def scenario():
        user = tmp_path / "user.toml"
        user.write_text(
            ('approval_policy="on-request"\n' if explicit else "")
            + f"[execution]\ncompiler={json.dumps(str(compiler))}\n"
            + (
                ""
                if trust is None
                else f"[projects.{json.dumps(str(tmp_path))}]\ntrust_level={json.dumps(trust)}\n"
            )
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=user, model="fixture"),
            skills_enabled=False,
            tool_mode=mode,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        assert settings.execution_permissions.approval_policy_explicit is explicit
        managed = compose_mcp_requirements(
            [MCPRequirementsLayer("organization", 'allowed_approval_policies=["never"]')]
        )
        model = Model(mode)
        runtime = runtime_for(
            tmp_path, compiler, model, settings=settings, mcp_requirements=managed
        )
        try:
            target = tmp_path / "written"
            events = await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            resolved = runtime._settings.execution_permissions
            assert resolved.approval_policy_json == '"never"'
            assert resolved.requested_approval_policy_json == (
                '"untrusted"' if trust == "untrusted" and not explicit else '"on-request"'
            )
            assert target.exists() is (trust is not None)
            context = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            assert "Approval policy is currently never" in context
            warnings = [
                e.message
                for e in events
                if isinstance(e, WarningEvent) and "`approval_policy`" in e.message
            ]
            assert len(warnings) == int(explicit)
            if explicit:
                assert "Never" in warnings[0] and "organization" in warnings[0]
            assert not [
                e
                for e in await run(runtime, model)
                if isinstance(e, WarningEvent) and "`approval_policy`" in e.message
            ]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True])
def test_readmission_uses_retained_request_and_commands_never_fallback(
    tmp_path, compiler, explicit
):
    async def scenario():
        original = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"read-only"}',
            requirements=(layer(["never"]),),
            approval_policy_json='"on-request"',
            approval_policy_explicit=explicit,
        )
        admitted, warnings = await resolve_execution_permissions(original)
        assert admitted.approval_policy_json == '"never"' and bool(warnings) is explicit
        changed = replace(admitted, requirements=(layer(["on-request"]),))
        with pytest.raises(ValueError, match="approval_policy"):
            await sandbox_command(changed, ["/usr/bin/true"], tmp_path)
        admitted_again, warnings = await resolve_execution_permissions(changed)
        assert admitted_again.approval_policy_json == '"on-request"' and not warnings
        unrestricted, _ = await resolve_execution_permissions(replace(admitted, requirements=()))
        assert unrestricted.approval_policy_json == '"on-request"'

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True])
def test_native_layer_precedence_and_full_granular_equality(tmp_path, compiler, explicit):
    async def scenario():
        granular = {
            "granular": {"sandbox_approval": False, "rules": True, "mcp_elicitations": False}
        }
        requested = {
            "granular": {"sandbox_approval": True, "rules": True, "mcp_elicitations": False}
        }
        permissions = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"read-only"}',
            requirements=(layer(["never"], "lower"), layer([granular, "on-request"], "higher")),
            approval_policy_json=json.dumps(requested),
            approval_policy_explicit=explicit,
        )
        result, warnings = await resolve_execution_permissions(permissions)
        effective = json.loads(result.approval_policy_json)
        assert effective["granular"]["sandbox_approval"] is False
        assert effective["granular"]["rules"] is True
        assert effective["granular"]["skill_approval"] is False
        assert bool(warnings) is explicit
        exact, warnings = await resolve_execution_permissions(
            replace(permissions, approval_policy_json=json.dumps(granular))
        )
        assert exact.approval_policy_json == result.approval_policy_json and not warnings

    asyncio.run(scenario())


@pytest.mark.parametrize("allowed", [[], ["unknown"], [True]])
def test_invalid_managed_approval_stops_before_model(tmp_path, compiler, allowed, monkeypatch):
    async def scenario():
        model = Model("direct")

        def forbidden(*args, **kwargs):
            raise AssertionError("invalid policy must fail before process ownership allocation")

        monkeypatch.setattr("corki.core.runtime.ProcessManager", forbidden)
        with pytest.raises(ValueError, match="allowed_approval_policies"):
            settings = CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=ExecutionPermissions(
                    compiler, tmp_path, '{"type":"read-only"}', requirements=(layer(allowed),)
                ),
            )
            runtime_for(tmp_path, compiler, model, settings=settings)
        assert not model.requests

    asyncio.run(scenario())


@pytest.mark.parametrize("allowed", ["never", "on-request"])
def test_profile_fallback_guard_uses_effective_approval(tmp_path, compiler, allowed):
    async def scenario():
        permissions = ExecutionPermissions(
            compiler,
            tmp_path,
            '{"type":"danger-full-access"}',
            requirements=(layer([allowed], allowed_sandbox_modes=["read-only"]),),
            approval_policy_json='"on-request"' if allowed == "never" else '"never"',
        )
        if allowed == "never":
            with pytest.raises(ValueError, match="approval"):
                await resolve_execution_permissions(permissions)
        else:
            result, warnings = await resolve_execution_permissions(permissions)
            assert result.approval_policy_json == '"on-request"'
            assert json.loads(result.profile_json)["type"] == "managed"
            assert len(warnings) == 2

    asyncio.run(scenario())
