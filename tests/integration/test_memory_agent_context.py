import asyncio
import json

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.config.skills import SkillRule
from corki.context import ContextBuilder
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def skill(root, name):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: Procedure {name}\n---\nBODY_{name}")


@pytest.mark.parametrize("mode", ["visible", "hidden", "rule", "disabled"])
def test_worker_preserves_base_context_and_scoped_skills_without_promoting_history(tmp_path, mode):
    async def scenario():
        project, home, compatibility = tmp_path / "project", tmp_path / "home", tmp_path / "compat"
        project.mkdir()
        (project / ".git").mkdir()
        (project / "AGENTS.md").write_text("PARENT_ONLY_RULE")
        skill(project / ".corki/skills", "configured")
        skill(project / ".agents/skills", "repo-only")
        skill(home / "skills", "userguide")
        # This is a repository ancestor, not the independent global home
        # instructions provider. The new memory .git boundary must exclude it.
        ancestor = home / "workspace"
        (ancestor / ".git").mkdir(parents=True)
        (ancestor / "AGENTS.md").write_text("MEMORY_ANCESTOR_RULE")
        skill(ancestor / ".agents/skills", "memory-ancestor")
        skill(compatibility / ".agents/skills", "compatible")
        root = ancestor / "memories"
        root.mkdir()
        skill(root / ".agents/skills", "memory-local")
        (root / "AGENTS.md").write_text("MEMORY_RULE")
        source = root / "extensions/team/source.txt"
        source.parent.mkdir(parents=True)
        source.write_text("HISTORICAL_DO_NOT_RUN $userguide")
        requests = []
        skill_names = ("configured", "userguide", "compatible", "memory-local", "memory-ancestor")

        class Main:
            async def stream(self, request):
                assert any(
                    isinstance(i, ContextItem)
                    and i.content_kind == "managed_config.developer_instructions"
                    and "HOST_POLICY_PROOF" in i.content
                    for i in request.items
                )
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory:
            async def stream(self, request):
                requests.append(request)
                assert request.instructions == ContextBuilder().base_instructions()
                items = [i for i in request.items if isinstance(i, ContextItem)]
                policies = [
                    i for i in items if i.content_kind == "managed_config.developer_instructions"
                ]
                assert len(policies) == 1
                assert policies[0].content == (
                    "<managed_developer_instructions>\nHOST_POLICY_PROOF\n"
                    "</managed_developer_instructions>"
                )
                assert policies[0].role.value == "developer" and policies[0].separate_message
                assert any(i.key == "environment.primary" for i in items)
                assert any(i.key == "project.agents" and "MEMORY_RULE" in i.content for i in items)
                assert all("MEMORY_ANCESTOR_RULE" not in i.content for i in items)
                if len(requests) >= 3:
                    rules = next(i for i in reversed(items) if i.key == "project.agents")
                    # File edits do not invalidate the worker's creation snapshot.
                    assert "UPDATED_MEMORY_RULE" not in (rules.snapshot_content or rules.content)
                assert all("PARENT_ONLY_RULE" not in i.content for i in items)
                assert not any(i.key.startswith("extensions.skills.selected.") for i in items)
                catalog = next(
                    (i.content for i in items if i.key == "extensions.skills.catalog"), ""
                )
                if mode in {"hidden", "disabled"}:
                    assert not catalog
                else:
                    assert all(name in catalog for name in skill_names[1:-1])
                    assert ("configured" in catalog) == (mode != "rule")
                assert "repo-only" not in catalog
                assert "memory-ancestor" not in catalog
                user = next(i for i in request.items if isinstance(i, UserMessageItem))
                assert "Memory Writing Agent: Phase 2" in user.content
                assert "HISTORICAL_DO_NOT_RUN" not in user.content
                data = json.dumps(inspect_worker_evidence(request))
                assert "HISTORICAL_DO_NOT_RUN" in data
                turn, step = user.turn_id, new_step_id()
                if len(requests) > 1:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    previous = skill_names[len(requests) - 2]
                    denied = (
                        mode == "disabled"
                        or previous == "memory-ancestor"
                        or (mode == "rule" and previous == "configured")
                    )
                    assert result.is_error == denied
                    assert (f"BODY_{previous}" in result.content) == (not denied)
                if len(requests) <= len(skill_names):
                    if len(requests) == 2:
                        (root / "AGENTS.md").write_text("MEMORY_RULE UPDATED_MEMORY_RULE")
                    name = skill_names[len(requests) - 1]
                    call = ToolCall(new_tool_call_id(), "skill_read", {"name": name})
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    text = json.dumps(
                        {"memory": "verified", "memory_summary": "index", "skills": []}
                    )
                    yield ModelCompleted((AssistantMessageItem(text, turn, step),))

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=project,
                memories_consolidation_model="fixture",
                memories_enabled=True,
                skills_enabled=mode != "disabled",
                skills_include_instructions=mode != "hidden",
                skills_config=(SkillRule(False, name="configured"),) if mode == "rule" else (),
            ),
            database_path=tmp_path / "sessions.db",
            home_path=home,
            compatibility_home=compatibility,
            memory_root=root,
            model=Main(),
            memory_model=Memory(),
            mcp_requirements=compose_mcp_requirements(
                (
                    MCPRequirementsLayer(
                        "host-fixture", 'additional_developer_instructions = "HOST_POLICY_PROOF"'
                    ),
                )
            ),
        )
        try:
            assert isinstance([e async for e in runtime.stream("parent task")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert len(requests) == len(skill_names) + 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_consolidation_does_not_reload_or_expose_parent_plugins(tmp_path):
    async def scenario():
        home = tmp_path / "home"
        plugin = home / "plugins/sample"
        manifest = plugin / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "sample",
                    "version": "1.0.0",
                    "skills": "./skills",
                    "entrypoint": "plugin.py:register",
                }
            )
        )
        skill(plugin / "skills", "plugin-unique-skill")
        (plugin / "plugin.py").write_text(
            "from pathlib import Path\n"
            "def register(api):\n"
            "    with Path(__file__).with_name('loads').open('a') as log:\n"
            "        log.write('loaded\\n')\n"
            "    api.register_tool(name='echo', description='Echo', "
            "parameters={'type':'object'}, handler=lambda args, ctx: args)\n"
        )

        class Model:
            async def stream(self, request):
                if request.model != "main":
                    assert all(not tool.name.startswith("plugin__") for tool in request.tools)
                    assert all("plugin-unique-skill" not in i.content for i in request.items)
                text = json.dumps({"memory": "verified", "memory_summary": "index", "skills": []})
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, model="main", memories_enabled=True),
            database_path=tmp_path / "sessions.db",
            home_path=home,
            model=Model(),
            memory_model=Model(),
        )
        try:
            assert len(runtime._plugin_manager.plugins) == 1
            assert isinstance([e async for e in runtime.stream("main")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            assert (plugin / "loads").read_text() == "loaded\n"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
