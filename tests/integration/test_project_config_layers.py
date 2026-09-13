"""Local project configuration and rules must share the host trust decision."""

import asyncio
import json
import shlex
from dataclasses import replace

import pytest
from test_execution_policy_live_inheritance import Model, run, runtime_for
from test_execution_policy_live_inheritance import compiler as compiler

from corki.config import CorkiSettings
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import WarningEvent
from corki.protocol.items import ContextItem


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_project_document_budget_is_loaded_only_from_trusted_layers(tmp_path, mode, trust):
    async def scenario():
        project = tmp_path / "project"
        folder = project / ".corki"
        folder.mkdir(parents=True)
        (folder / "config.toml").write_text("project_doc_max_bytes=0\n")
        (project / "AGENTS.md").write_text("PROJECT_LAYER_CONTEXT_MARKER")
        user = tmp_path / "user.toml"
        user.write_text(
            ""
            if trust is None
            else f"[projects.{json.dumps(str(project))}]\ntrust_level={json.dumps(trust)}\n"
        )
        settings = replace(
            CorkiSettings.for_directory(project, config_file=user, model="fixture"),
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        model = Model(mode)
        runtime = runtime_for(project, None, model, settings=settings)
        try:
            await run(runtime, model)
            text = "\n".join(
                item.content for item in model.requests[0].items if isinstance(item, ContextItem)
            )
            assert ("PROJECT_LAYER_CONTEXT_MARKER" in text) is (trust is None)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_admitted_layers_and_rules_remain_frozen_until_cold_reload(tmp_path, compiler, mode):
    async def scenario():
        project = tmp_path / "project"
        folder = project / ".corki"
        (folder / "rules").mkdir(parents=True)
        rule = folder / "rules/local.rules"
        rule.write_text('prefix_rule(pattern=["touch"], decision="allow")')
        (folder / "config.toml").write_text(
            '[provider]\nbase_url="https://ignored.invalid"\n[agent]\nmax_steps=5\n'
        )
        user = tmp_path / "user.toml"
        text = (
            'approval_policy="never"\n[execution]\n'
            f'compiler={json.dumps(str(compiler))}\nprofile={{type="read-only"}}\n'
            f'[projects.{json.dumps(str(project))}]\ntrust_level="trusted"\n'
        )
        user.write_text(text)

        def settings():
            return replace(
                CorkiSettings.for_directory(project, config_file=user, model="fixture"),
                tool_mode=mode,
                skills_enabled=False,
                model_contexts=(ModelContextInfo(model="fixture"),),
            )

        model, cold_model = Model(mode), Model(mode)
        runtime = runtime_for(project, compiler, model, settings=settings())
        cold = None
        try:
            first, second, third = (project / name for name in ("first", "second", "third"))
            events = await run(runtime, model, {"cmd": "touch " + shlex.quote(str(first))})
            assert first.exists()
            assert (
                len(
                    [
                        event
                        for event in events
                        if isinstance(event, WarningEvent)
                        and "Ignored unsupported project-local" in event.message
                    ]
                )
                == 1
            )
            user.write_text(text.replace('trust_level="trusted"', 'trust_level="untrusted"'))
            rule.write_text('prefix_rule(pattern=["touch"], decision="forbidden")')
            events = await run(runtime, model, {"cmd": "touch " + shlex.quote(str(second))})
            assert second.exists()
            assert not [event for event in events if isinstance(event, WarningEvent)]
            assert runtime._settings.max_steps == 5
            cold = runtime_for(project, compiler, cold_model, name="cold", settings=settings())
            await run(cold, cold_model, {"cmd": "touch " + shlex.quote(str(third))})
            assert not third.exists()
            assert cold._settings.max_steps is None
            assert not cold._settings.execution_permissions.exec_policy_snapshot.sources
        finally:
            if cold is not None:
                await cold.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
@pytest.mark.parametrize("has_config", [False, True])
def test_project_rules_share_layer_admission_even_without_config_file(
    tmp_path, compiler, mode, trust, has_config
):
    async def scenario():
        project = tmp_path / "project"
        folder = project / ".corki"
        (folder / "rules").mkdir(parents=True)
        (folder / "rules/local.rules").write_text(
            'prefix_rule(pattern=["touch"], decision="allow")'
        )
        if has_config:
            (folder / "config.toml").write_text("[agent]\nmax_steps=5\n")
        user = tmp_path / "user.toml"
        user.write_text(
            'approval_policy="never"\n[execution]\n'
            f'compiler={json.dumps(str(compiler))}\nprofile={{type="read-only"}}\n'
            + (
                ""
                if trust is None
                else f"[projects.{json.dumps(str(project))}]\ntrust_level={json.dumps(trust)}\n"
            )
        )
        settings = replace(
            CorkiSettings.for_directory(project, config_file=user, model="fixture"),
            tool_mode=mode,
            skills_enabled=False,
            model_contexts=(ModelContextInfo(model="fixture"),),
        )
        model = Model(mode)
        runtime = runtime_for(project, compiler, model, settings=settings)
        try:
            target = project / "written"
            await run(runtime, model, {"cmd": "touch " + shlex.quote(str(target))})
            assert target.exists() is (trust == "trusted")
            sources = runtime._settings.execution_permissions.exec_policy_snapshot.sources
            assert any(source.name.endswith("local.rules") for source in sources) is (
                trust == "trusted"
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
