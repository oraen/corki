"""Host skills use unique names and path precedence in both body and MCP requirements."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol import InputMention
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("catalog", [False, True])
@pytest.mark.parametrize("selection", ["plain", "path", "link_and_plain", "two_links"])
def test_host_selection_drives_context_and_requirements(tmp_path, catalog, selection):
    async def scenario():
        paths = {}
        for name in ("first", "second"):
            path = tmp_path / ".corki/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: guide\ndescription: fixture\n---\nBODY_" + name)
            policy = path.parent / "agents/openai.yaml"
            policy.parent.mkdir()
            policy.write_text("dependencies:\n  tools:\n    - type: mcp\n      value: " + name)
            paths[name] = path
        text = (
            f"[$guide]({paths['second']}) [$guide]({paths['first']})"
            if selection == "two_links"
            else f"[$guide]({paths['second']}) $guide"
            if selection == "link_and_plain"
            else "$guide"
        )
        mentions = (
            (InputMention("guide", str(paths["second"]), "skill"),) if selection == "path" else ()
        )
        expected = (
            []
            if selection == "plain"
            else ["first", "second"]
            if selection == "two_links"
            else ["second"]
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_include_instructions=catalog,
                execution_permissions=None,
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        try:
            events = [e async for e in runtime.stream(text, mentions=mentions)]
            assert isinstance(events[-1], TurnCompleted)
            bodies = [
                i
                for i in requests[0].items
                if isinstance(i, ContextItem) and i.key.startswith("extensions.skills.selected.")
            ]
            assert len(bodies) == len(expected)
            assert all(
                "BODY_" + name in item.content for name, item in zip(expected, bodies, strict=True)
            )
            state = await runtime._compiled.aget_state(runtime._graph_config(events[-1].turn_id))
            assert tuple(state.values["mcp_required_servers"]) == tuple(expected)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
