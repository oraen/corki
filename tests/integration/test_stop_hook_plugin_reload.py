"""Each Stop invocation captures one atomically published definition/authority pair."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import load_local_config
from corki.core import LangGraphRuntime, stop_hooks
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize("approval", ["renewed", "unchanged", "revoked"])
@pytest.mark.parametrize("timing", ["before_stop", "during_stop"])
def test_plugin_hook_reload_publishes_definition_and_approval_together(
    tmp_path, monkeypatch, approval, timing
):
    async def scenario():
        root = tmp_path / "bundle"
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "hooks").mkdir()
        (root / ".codex-plugin/plugin.json").write_text('{"name":"bundle"}')
        hook_file = root / "hooks/hooks.json"
        user = tmp_path / "config.toml"

        def handler(label):
            return {
                "type": "command",
                "command": shlex.join(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        f"Path('hook-calls').open('a').write({label!r}+'\\n'); print('{{}}')",
                    ]
                ),
            }

        old, new = handler("old"), handler("new")

        def definition(command):
            hook_file.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [command, command]}]}}))

        def trust(command, enabled=True):
            user.write_text(
                "".join(
                    f"[hooks.state.{json.dumps(f'bundle:hooks/hooks.json:stop:0:{index}')} ]\n"
                    f"trusted_hash={json.dumps(command_identity(command)[0])}\n"
                    f"enabled={'true' if enabled else 'false'}\n"
                    for index in range(2)
                )
            )

        definition(old)
        trust(old)
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=user),
            plugin_dirs=(root,),
            skills_enabled=False,
        )
        requests = []
        old_graph = None
        refreshed = False

        async def reload_hooks():
            nonlocal old_graph, refreshed
            old_graph = runtime._graph
            definition(new)
            if approval != "unchanged":
                trust(new, enabled=approval != "revoked")
            document, configuration = load_local_config(tmp_path, user)
            publish = await runtime._prepare_configuration_reload(configuration, document)
            publish()
            refreshed = True
            assert runtime._graph is old_graph

        execute = stop_hooks.run_command

        async def execute_and_reload(*args, **kwargs):
            result = await execute(*args, **kwargs)
            if timing == "during_stop" and not refreshed:
                # The first real subprocess finished, but the same Stop call
                # still has its second command to execute after publication.
                await reload_hooks()
            return result

        monkeypatch.setattr(stop_hooks, "run_command", execute_and_reload)

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= 2
                if len(requests) == 1 and timing == "before_stop":
                    await reload_hooks()
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
            first = [e async for e in runtime.stream("first")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            calls = tmp_path / "hook-calls"
            first_calls = (
                ["old", "old"]
                if timing == "during_stop"
                else ["new", "new"]
                if approval == "renewed"
                else []
            )
            assert (calls.read_text().splitlines() if calls.exists() else []) == first_calls
            assert refreshed
            second = [e async for e in runtime.stream("second")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert runtime._graph is not old_graph
            assert len(requests) == 2
            assert (calls.read_text().splitlines() if calls.exists() else []) == (
                first_calls + (["new", "new"] if approval == "renewed" else [])
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
