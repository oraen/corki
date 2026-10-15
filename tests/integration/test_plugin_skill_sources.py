"""Plugin source format and identity must survive into real Runtime skill input."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.plugins.manifest_path import AGENT_SCHEMA
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.input_mentions import InputMention
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


def write_skill(root, relative, name, body):
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {body}\n---\n{body}")
    return path


@pytest.mark.parametrize("agent", [False, True], ids=["legacy", "agent"])
@pytest.mark.parametrize(
    "case",
    [
        "direct",
        "nested",
        "root",
        "outside",
        "inside",
        "two_markets",
        "format_reload",
        "file_link",
        "depth_limit",
        "hidden",
        "support",
        "entry_limit",
    ],
)
def test_plugin_skill_source_reaches_context(tmp_path, monkeypatch, agent, case):
    async def scenario():
        home = tmp_path / "home"
        packages = []
        for market in ["first", "second"] if case == "two_markets" else ["first"]:
            root = home / "plugins/cache" / market / "fixture/local"
            path = root / ("plugin.json" if agent else ".codex-plugin/plugin.json")
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"name": "fixture", **({"$schema": AGENT_SCHEMA} if agent else {})})
            )
            folder = (
                "skills/a/b/c/d/e/f/g/h"
                if case == "depth_limit"
                else "skills/.custom/guide"
                if case == "hidden"
                else "skills/references"
                if case == "support"
                else "skills/deep/nested"
                if case in {"nested", "format_reload"}
                else "skills"
                if case == "root"
                else "skills/guide"
            )
            skill = write_skill(root, folder, "guide", "BODY_" + market)
            if case == "file_link":
                target = root / "private/guide/SKILL.md"
                target.parent.mkdir(parents=True)
                skill.rename(target)
                skill.symlink_to(target)
            if case == "entry_limit":
                (root / "skills/00-first").write_text("not a skill")
                (root / "skills/01-second").write_text("not a skill")
                monkeypatch.setattr("corki.skills.walk.MAX_ENTRIES", 2)
            if case in {"outside", "inside"}:
                # Relocate the declaration and expose only a directory link from skills.
                target = tmp_path / "outside" if case == "outside" else root / "private"
                target.mkdir()
                (skill.parent).rename(target / "guide")
                skill.parent.symlink_to(target / "guide", target_is_directory=True)
            packages.append((market, root, skill))
        config = tmp_path / "config.toml"
        config.write_text(
            "".join(f'[plugins."fixture@{market}"]\nenabled=true\n' for market, _, _ in packages)
            + '[mcp.servers.reload_trigger]\ntransport="http"\n'
            'url="https://reload-trigger.invalid/mcp"\nenabled=false\n'
        )

        def reject_client(*args, **kwargs):
            raise AssertionError("disabled reload trigger must not create a connection")

        monkeypatch.setattr("corki.mcp.manager.create_client", reject_client)
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", reject_client)
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if not any(
                    isinstance(item, ToolResultItem)
                    and item.turn_id == turn
                    and item.tool_name == "skill_list"
                    for item in request.items
                ):
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "skill_list", {}), turn, new_step_id()
                            ),
                        )
                    )
                    return
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=replace(
                CorkiSettings.for_directory(tmp_path, config_file=config),
                execution_permissions=None,
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=home,
            database_path=tmp_path / "runtime.db",
        )
        try:
            mentions = tuple(
                InputMention("fixture:guide", str(skill), "skill") for _, _, skill in packages
            )
            for phase in range(2 if case == "format_reload" else 1):
                if phase:
                    if agent:
                        (root / "plugin.json").rename(root / "old-agent.json")
                        (root / ".codex-plugin").mkdir(exist_ok=True)
                        (root / ".codex-plugin/plugin.json").write_text('{"name":"fixture"}')
                    else:
                        (root / "plugin.json").write_text(
                            json.dumps({"name": "fixture", "$schema": AGENT_SCHEMA})
                        )
                    persistence = runtime._mcp_manager._approval_persistence
                    await persistence.persist(
                        ("reload_trigger", "fixture", "Fixture", "trigger"),
                        persistence.configuration,
                        None,
                    )
                    assert "apps" not in persistence.document
                    assert (
                        persistence.document["mcp"]["servers"]["reload_trigger"]["tools"][
                            "trigger"
                        ]["approval_mode"]
                        == "approve"
                    )
                events = [
                    e async for e in runtime.stream("Load the selected guides", mentions=mentions)
                ]
                assert isinstance(events[-1], TurnCompleted)
                selected = [
                    i
                    for i in requests[-1].items
                    if isinstance(i, ContextItem)
                    and i.key.startswith("extensions.skills.selected.")
                    and i.turn_id == requests[-1].items[-1].turn_id
                ]
                active_agent = not agent if phase else agent
                allowed = case not in {"file_link", "depth_limit", "hidden", "entry_limit"} and (
                    not active_agent or case in {"direct", "inside", "two_markets", "support"}
                )
                assert len(selected) == (len(packages) if allowed else 0)
                assert len({item.key for item in selected}) == len(selected)
                if allowed:
                    assert all(
                        "BODY_" + market in item.content
                        for (market, _, _), item in zip(packages, selected, strict=True)
                    )
                plugin_skills = [
                    s
                    for s in runtime._skill_service.snapshot(tmp_path).skills
                    if s.namespace == "fixture"
                ]
                assert all(skill.scope.value == "user" for skill in plugin_skills)
                assert [(s.qualified_name, s.plugin_id) for s in plugin_skills] == (
                    [("fixture:guide", f"fixture@{market}") for market, _, _ in packages]
                    if allowed
                    else []
                )
                observed = next(
                    item
                    for item in reversed(requests[-1].items)
                    if isinstance(item, ToolResultItem) and item.tool_name == "skill_list"
                )
                assert all(
                    s["scope"] == "user"
                    for s in json.loads(observed.content)["skills"]
                    if s.get("plugin_id")
                )
                assert [
                    (s["name"], s["plugin_id"])
                    for s in json.loads(observed.content)["skills"]
                    if s["name"].startswith("fixture:")
                ] == [(s.qualified_name, s.plugin_id) for s in plugin_skills]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
