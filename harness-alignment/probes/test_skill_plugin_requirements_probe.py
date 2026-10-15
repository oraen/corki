"""Installed plugin identity must survive skill selection into required MCP servers."""

import asyncio
from types import SimpleNamespace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.input_requirements import collect_input_requirements_async
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.protocol import InputMention
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.skills.service import PluginSkillRoot, SkillService


@pytest.mark.parametrize("selection", ["skill", "plugin"])
def test_installed_plugin_requirement_uses_full_identity(tmp_path, selection):
    roots, plugins = [], []
    for market in ("first", "second"):
        root = tmp_path / market
        path = root / "skills/guide/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: guide\ndescription: fixture\n---\nBODY")
        roots.append(PluginSkillRoot("fixture", root / "skills", f"fixture@{market}", root))
        plugins.append(
            LoadedPlugin(
                PluginManifest(
                    "fixture",
                    None,
                    None,
                    root,
                    plugin_id=f"fixture@{market}",
                    mcp_servers=(
                        MCPServerSettings(f"{market}_docs", "http", url="https://fixture.test"),
                    ),
                )
            )
        )
    skills = SkillService(
        home=tmp_path / "home",
        project_root=tmp_path,
        plugin_roots=tuple(roots),
        bundled_enabled=False,
    )
    mention = (
        InputMention("fixture:guide", str(tmp_path / "second/skills/guide/SKILL.md"), "skill")
        if selection == "skill"
        else InputMention("fixture", "plugin://fixture@second")
    )
    user = UserMessageItem("use the selected capability", new_turn_id(), mentions=(mention,))
    result = asyncio.run(
        collect_input_requirements_async(
            {"turn_id": user.turn_id},
            (user,),
            cwd=tmp_path,
            skills=skills,
            plugins=SimpleNamespace(plugins=tuple(plugins)),
        )
    )
    assert result["mcp_required_servers"] == ("second_docs",)
