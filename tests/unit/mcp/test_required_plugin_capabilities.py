"""Required capabilities use active installation identity, never a namespace alias."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.input_requirements import collect_input_requirements
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.protocol import InputMention
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.skills.service import PluginSkillRoot, SkillService


@pytest.mark.parametrize("selection", ["skill", "plugin"])
@pytest.mark.parametrize("case", ["installed", "local", "disabled", "error", "no_id", "wrong_id"])
def test_plugin_requirement_matches_active_identity(tmp_path, case, selection):
    identity = "fixture" if case == "local" else "fixture@market"
    manifest = PluginManifest(
        "fixture",
        None,
        None,
        tmp_path / "package",
        plugin_id=None if case == "local" else identity,
        enabled=case != "disabled",
        error="broken" if case == "error" else None,
        mcp_servers=(MCPServerSettings("namespaced_docs", "http", url="https://fixture.test"),),
        mcp_raw_names=(("namespaced_docs", "docs"),),
    )
    root = tmp_path / "package/skills"
    path = root / "guide/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: guide\ndescription: fixture\n---\nBODY")
    skill_id = None if case == "no_id" else "fixture@other" if case == "wrong_id" else identity
    skills = SkillService(
        home=tmp_path / "home",
        project_root=tmp_path,
        bundled_enabled=False,
        plugin_roots=(PluginSkillRoot("fixture", root, skill_id),),
    )
    user = UserMessageItem(
        "use selected",
        new_turn_id(),
        mentions=(
            InputMention("fixture:guide", str(path), "skill")
            if selection == "skill"
            else InputMention("fixture", f"plugin://{identity}"),
        ),
    )
    state = {"turn_id": user.turn_id}
    result = collect_input_requirements(
        state,
        (user,),
        cwd=tmp_path,
        skills=skills,
        plugins=SimpleNamespace(plugins=(LoadedPlugin(manifest),)),
    )
    active = case not in {"disabled", "error"} and (
        selection == "plugin" or case not in {"no_id", "wrong_id"}
    )
    assert result["mcp_required_servers"] == (("docs",) if active else ())
    assert result["mcp_required_plugins"] == ((identity,) if selection == "plugin" else ())
    # Durable requirements survive cold recovery without rereading changed skill files.
    recovered = collect_input_requirements(
        {**state, **result},
        (replace(user, content="changed"),),
        cwd=tmp_path,
        skills=None,
        plugins=SimpleNamespace(plugins=()),
    )
    assert recovered == result
