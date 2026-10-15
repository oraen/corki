"""Native host dependency selection and extension prompt selection are distinct."""

import asyncio
from types import SimpleNamespace

import pytest

from corki.mcp.input_requirements import collect_input_requirements_async
from corki.protocol import InputMention
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.skills.service import SkillService


@pytest.mark.parametrize("selection", ["plain", "path", "link"])
def test_ambiguous_host_skill_does_not_require_arbitrary_dependency(tmp_path, selection):
    for folder in ("first", "second"):
        path = tmp_path / ".corki/skills" / folder / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: guide\ndescription: fixture\n---\nBODY_" + folder)
        policy = path.parent / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text("dependencies:\n  tools:\n    - type: mcp\n      value: " + folder)
    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    path = tmp_path / ".corki/skills/second/SKILL.md"
    text = f"[$guide]({path})" if selection == "link" else "$guide"
    mentions = (InputMention("guide", str(path), "skill"),) if selection == "path" else ()
    user = UserMessageItem(text, new_turn_id(), mentions=mentions)

    async def scenario():
        # This only proves the core host dependency mismatch. Which sources
        # populate the extension prompt catalog needs a separate routing audit.
        result = await collect_input_requirements_async(
            {"turn_id": user.turn_id},
            (user,),
            cwd=tmp_path,
            skills=service,
            plugins=SimpleNamespace(plugins=()),
        )
        assert result["mcp_required_servers"] == (() if selection == "plain" else ("second",))

    asyncio.run(scenario())
