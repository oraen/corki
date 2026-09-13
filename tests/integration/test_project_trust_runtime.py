"""Exercise user-project trust through actual config parsing and Runtime context."""

import asyncio
import json
import shlex
from dataclasses import replace

import pytest
from test_execution_policy_live_inheritance import Model, run, runtime_for
from test_execution_policy_live_inheritance import compiler as compiler

from corki.config import CorkiSettings
from corki.protocol.context import ModelContextInfo
from corki.protocol.items import ContextItem


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_explicit_untrusted_project_excludes_agents_instructions(tmp_path, trust, mode):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("PROJECT_TRUST_FIXTURE_INSTRUCTIONS")
        home = tmp_path / "host-home"
        home.mkdir()
        (home / "AGENTS.md").write_text("HOST_TRUST_FIXTURE_INSTRUCTIONS")
        config = tmp_path / "host-config.toml"
        config.write_text(
            ""
            if trust is None
            else f"[projects.{json.dumps(str(project))}]\ntrust_level={json.dumps(trust)}\n"
        )
        settings = CorkiSettings.for_directory(project, config_file=config, model="fixture")
        settings = replace(
            settings,
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        model = Model(mode)
        runtime = runtime_for(project, None, model, settings=settings, home_path=home)
        try:
            await run(runtime, model)
            text = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            assert ("PROJECT_TRUST_FIXTURE_INSTRUCTIONS" in text) is (trust != "untrusted")
            assert "HOST_TRUST_FIXTURE_INSTRUCTIONS" in text
            assert home / "AGENTS.md" in await runtime.instruction_sources()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_configured_trust_controls_actual_permissions_and_approval(tmp_path, compiler, trust, mode):
    async def scenario():
        project = tmp_path / "project"
        project.mkdir()
        config = tmp_path / "host.toml"
        config.write_text(
            f"[execution]\ncompiler={json.dumps(str(compiler))}\n"
            + (
                ""
                if trust is None
                else f"[projects.{json.dumps(str(project))}]\ntrust_level={json.dumps(trust)}\n"
            )
        )
        settings = replace(
            CorkiSettings.for_directory(project, config_file=config, model="fixture"),
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        model = Model(mode)
        runtime = runtime_for(project, compiler, model, settings=settings)
        prompts = []

        async def approve(request):
            prompts.append(request)
            runtime.respond_execution_approval(request.request_id, "accept")

        runtime.set_execution_approval_handler(approve)
        try:
            target = project / "written"
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            resolved = runtime._settings.execution_permissions
            assert resolved.active_profile.id == (":read-only" if trust is None else ":workspace")
            assert json.loads(resolved.approval_policy_json) == (
                "untrusted" if trust == "untrusted" else "on-request"
            )
            assert len(prompts) == (1 if trust == "untrusted" else 0)
            assert target.exists() is (trust is not None)
            context = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            sandbox = "read-only" if trust is None else "workspace-write"
            assert f"`sandbox_mode` is `{sandbox}`" in context
            assert not runtime._process_manager.approvals.router._pending
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("profile", [":read-only", ":workspace"])
def test_explicit_policy_and_profile_override_untrusted_defaults(tmp_path, compiler, mode, profile):
    async def scenario():
        config = tmp_path / "host.toml"
        config.write_text(
            f'approval_policy="never"\ndefault_permissions={json.dumps(profile)}\n'
            f"[execution]\ncompiler={json.dumps(str(compiler))}\n"
            f'[projects.{json.dumps(str(tmp_path))}]\ntrust_level="untrusted"\n'
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config, model="fixture"),
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        model = Model(mode)
        runtime = runtime_for(tmp_path, compiler, model, settings=settings)
        try:
            target = tmp_path / "written"
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            resolved = runtime._settings.execution_permissions
            assert resolved.active_profile.id == profile
            assert json.loads(resolved.approval_policy_json) == "never"
            assert target.exists() is (profile == ":workspace")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("selection", ["alias", "repo_root", "empty_cwd"])
def test_selected_trust_is_shared_by_context_and_execution(tmp_path, compiler, mode, selection):
    async def scenario():
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
        cwd = root / "nested"
        cwd.mkdir()
        (cwd / "AGENTS.md").write_text("TRUST_SELECTED_CONTEXT")
        key = root
        if selection == "alias":
            alias = tmp_path / "alias"
            alias.symlink_to(cwd, target_is_directory=True)
            cwd = key = alias
        config = tmp_path / "host.toml"
        config.write_text(
            f"[execution]\ncompiler={json.dumps(str(compiler))}\n"
            f'[projects.{json.dumps(str(key))}]\ntrust_level="untrusted"\n'
            + (f"[projects.{json.dumps(str(cwd))}]\n" if selection == "empty_cwd" else "")
        )
        settings = replace(
            CorkiSettings.for_directory(cwd, config_file=config, model="fixture"),
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        assert settings.working_directory == cwd.resolve()
        model = Model(mode)
        runtime = runtime_for(root, compiler, model, settings=settings)
        try:
            # Editing the file after host parsing does not silently change a
            # running configuration's authority or instruction admission.
            config.write_text("")
            await run(runtime, model)
            await run(runtime, model)
            unknown = selection == "empty_cwd"
            for request in model.requests:
                text = "\n".join(
                    item.content for item in request.items if isinstance(item, ContextItem)
                )
                assert ("TRUST_SELECTED_CONTEXT" in text) is unknown
            resolved = runtime._settings.execution_permissions
            assert resolved.active_profile.id == (":read-only" if unknown else ":workspace")
            assert json.loads(resolved.approval_policy_json) == (
                "on-request" if unknown else "untrusted"
            )
            assert (
                CorkiSettings.for_directory(
                    cwd, config_file=config
                ).project_instructions.trust_level
                is None
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
