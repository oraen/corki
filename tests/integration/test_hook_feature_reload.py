"""An effective hooks feature update controls new invocations, not host authority."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.hooks import ManagedHookPolicy
from corki.config.layers import load_local_config
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("initial", [False, True])
def test_hook_feature_file_reload_disables_and_reenables_managed_stop(
    tmp_path, monkeypatch, initial
):
    import corki.core.stop_hooks as hooks

    async def scenario():
        path = tmp_path / "config.toml"
        path.write_text(f"[features]\nhooks={str(initial).lower()}\nplugins=false\n")
        policy = ManagedHookPolicy(
            source=tmp_path / "requirements.toml",
            hooks_json=json.dumps({"Stop": [{"hooks": [{"type": "command", "command": "host"}]}]}),
            only_managed=True,
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=path),
            managed_hook_policy=policy,
            skills_enabled=False,
        )
        assert settings.hooks_enabled is initial
        calls = []

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def run(command, payload, **kwargs):
            calls.append(command.command)
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(hooks, "run_command", run)
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            expected = 0
            for value in (initial, False, None, False, True):
                enabled = True if value is None else value
                expected += int(enabled)
                entry = "" if value is None else f"hooks={str(value).lower()}\n"
                path.write_text(f"[features]\n{entry}plugins=false\n")
                loaded = CorkiSettings.for_directory(tmp_path, config_file=path)
                assert loaded.hooks_enabled is enabled
                document, configuration = load_local_config(tmp_path, path)
                publish = await runtime._prepare_configuration_reload(configuration, document)
                publish()
                assert runtime._settings.managed_hook_policy is policy
                assert isinstance([e async for e in runtime.stream("INPUT")][-1], TurnCompleted)
                assert calls == ["host"] * expected
            snapshot = runtime._graph._stop_hooks._snapshot
            path.write_text('[features]\nhooks="false"\nplugins=false\n')
            with pytest.raises(ValueError, match="features.hooks"):
                CorkiSettings.for_directory(tmp_path, config_file=path)
            document, configuration = load_local_config(tmp_path, path)
            with pytest.raises(ValueError, match="features.hooks"):
                await runtime._prepare_configuration_reload(configuration, document)
            assert runtime._graph._stop_hooks._snapshot is snapshot
            assert runtime._settings.hooks_enabled is True
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
