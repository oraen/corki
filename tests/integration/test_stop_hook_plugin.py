"""Legacy package snapshots execute only after separate user approval."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id


@pytest.mark.parametrize("form", ["default", "path", "paths", "inline", "inlines"])
@pytest.mark.parametrize("approved", [False, True])
def test_plugin_stop_command_controls_actual_runtime(tmp_path, form, approved):
    async def scenario():
        root = tmp_path / "bundle"
        (root / "hooks").mkdir(parents=True)
        script = root / "check.py"
        script.write_text(
            "import json,os,sys\nfrom pathlib import Path\np=json.load(sys.stdin)\n"
            "assert os.environ['PLUGIN_ROOT']==os.environ['CLAUDE_PLUGIN_ROOT']\n"
            "assert os.environ['PLUGIN_DATA']==os.environ['CLAUDE_PLUGIN_DATA']\n"
            "Path('hook-calls').open('a').write(str(p['stop_hook_active'])+'\\n')\n"
            "print(json.dumps({} if p['stop_hook_active'] else "
            "{'decision':'block','reason':'PLUGIN_CHECK'}))\n"
        )
        command = shlex.quote(sys.executable) + ' "${PLUGIN_ROOT}/check.py"'
        handler = {"type": "command", "command": command}
        hooks = {"hooks": {"Stop": [{"hooks": [handler]}]}}
        (root / "hooks/hooks.json").write_text(json.dumps(hooks))
        manifest = {"name": "bundle"}
        if form != "default":
            manifest["hooks"] = {
                "path": "./hooks/hooks.json",
                "paths": ["./hooks/hooks.json"],
                "inline": hooks,
                "inlines": [hooks],
            }[form]
        (root / ".codex-plugin").mkdir()
        (root / ".codex-plugin/plugin.json").write_text(json.dumps(manifest))
        source = "plugin.json#hooks[0]" if form.startswith("inline") else "hooks/hooks.json"
        key = f"bundle:{source}:stop:0:0"
        user = tmp_path / "config.toml"
        user.write_text(
            f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(command_identity(handler)[0])}\n"
            if approved
            else ""
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=user),
            plugin_dirs=(root,),
            skills_enabled=False,
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= 2
                if len(requests) == 2:
                    assert any(
                        isinstance(i, ContextItem) and i.content == "PLUGIN_CHECK"
                        for i in request.items
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:
            assert len(runtime._plugin_manager.plugins) == 1
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (2 if approved else 1)
            if approved:
                assert (tmp_path / "hook-calls").read_text().splitlines() == ["False", "True"]
            else:
                assert not (tmp_path / "hook-calls").exists()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
