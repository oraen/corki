"""An unknown/untrusted worktree is not authority to run in-process Python."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("source", ["project_unknown", "project_untrusted", "home", "host"])
def test_implicit_project_plugin_cannot_execute_before_source_admission(tmp_path, source):
    async def scenario():
        project, home = tmp_path / "project", tmp_path / "home"
        project.mkdir()
        (project / ".git").mkdir()
        root = home / "plugins" if source == "home" else project / ".corki/plugins"
        package = root / "source_fixture"
        manifest = package / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "source_fixture", "entrypoint": "plugin.py:register"})
        )
        (package / "plugin.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('imported').write_text('import ran')\n"
            "def register(api):\n"
            "    Path(__file__).with_name('registered').write_text('register ran')\n"
        )
        config = tmp_path / "config.toml"
        config.write_text(
            ""
            if source == "project_unknown"
            else f'[projects.{json.dumps(str(project))}]\ntrust_level="untrusted"\n'
        )
        settings = replace(
            CorkiSettings.for_directory(project, config_file=config),
            execution_permissions=None,
        )
        if source == "host":
            settings = replace(settings, plugin_dirs=(root,))
        assert settings.project_instructions.trust_level == (
            None if source == "project_unknown" else "untrusted"
        )

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )
        try:
            events = [event async for event in runtime.stream("inspect this project")]
        finally:
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        observed = [(package / name).exists() for name in ("imported", "registered")]
        assert observed == [source in {"home", "host"}] * 2, (source, observed)

    asyncio.run(scenario())
