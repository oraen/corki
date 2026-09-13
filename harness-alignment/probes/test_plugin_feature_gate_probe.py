"""Native's static plugins feature gate must stop package capabilities at startup."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("gate", ["file_disabled", "host_disabled", "default"])
def test_plugin_feature_gate_precedes_python_registration_and_skill_discovery(tmp_path, mode, gate):
    async def scenario():
        home = tmp_path / "home"
        plugin = home / "plugins/gate_fixture"
        manifest = plugin / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "gate_fixture",
                    "entrypoint": "plugin.py:register",
                }
            )
        )
        (plugin / "plugin.py").write_text(
            "from pathlib import Path\n"
            "def register(api):\n"
            "    Path(__file__).with_name('registered').write_text('registered once')\n"
            "    api.register_tool(name='echo', description='gate fixture', "
            "parameters={'type':'object'}, handler=lambda args, ctx: 'echo')\n"
        )
        skill = plugin / "skills/guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: guide\ndescription: Gate fixture guide\n---\nGuide.\n")
        config = tmp_path / "config.toml"
        config.write_text("[features]\nplugins=false\n" if gate == "file_disabled" else "")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "done",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            execution_permissions=None,
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_search_mode="disabled" if mode == "code_mode" else mode,
            tool_mode="code_mode_only" if mode == "code_mode" else "direct",
        )
        registry = ToolRegistry()
        runtime = LangGraphRuntime.create(
            settings=settings,
            registry=registry,
            model=Model(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
            load_plugins=gate != "host_disabled",
        )
        try:
            events = [event async for event in runtime.stream("inspect available capabilities")]
            observed = {
                "registered": (plugin / "registered").exists(),
                "callable": registry.get("plugin__gate_fixture__echo") is not None,
                "skill": runtime._skill_service.snapshot(tmp_path).resolve("gate_fixture:guide")
                is not None,
            }
        finally:
            await runtime.aclose()
        assert requests and isinstance(events[-1], TurnCompleted), events[-1]
        assert observed == dict.fromkeys(observed, gate == "default")

    asyncio.run(scenario())
