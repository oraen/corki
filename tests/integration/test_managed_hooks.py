"""Managed hook authority is supplied by the host, never by project content."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("origin", ["user", "project"])
def test_plain_configuration_cannot_claim_managed_hook_authority(tmp_path, origin):
    async def scenario():
        document = (
            "allow_managed_hooks_only=true\n"
            "[managed_hook_policy]\nonly_managed=true\n"
            "[[hooks.SessionStart]]\nis_managed=true\n"
            '[[hooks.SessionStart.hooks]]\ntype="command"\ncommand="untrusted"\n'
            "is_managed=true\n"
        )
        configuration = LocalConfigState(
            (ConfigLayer(tmp_path / "requirements.toml", origin, contents=document),)
        )

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=configuration,
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            import tomllib

            publish = await runtime._prepare_configuration_reload(
                configuration, tomllib.loads(document)
            )
            publish()
            assert runtime._settings.managed_hook_policy is None
            commands, _ = runtime._graph._stop_hooks._snapshot["SessionStart"]
            assert commands == ()
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("only_managed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("origin", ["user", "project", "plugin"])
def test_managed_hooks_survive_user_state_and_filter_ordinary_sources(
    tmp_path, monkeypatch, only_managed, enabled, origin
):
    import corki.core.session_end_hooks as end
    import corki.core.start_hooks as start
    from corki.config.hooks import ManagedHookPolicy

    async def scenario():
        source = tmp_path / "requirements.toml"
        user_path = tmp_path / "config.toml"
        project_path = tmp_path / "project.toml"
        plugin_root = tmp_path / "bundle"
        key = (
            f"{user_path}:session_start:0:0"
            if origin == "user"
            else f"{project_path}:session_start:0:0"
            if origin == "project"
            else "bundle:hooks/hooks.json:session_start:0:0"
        )
        user_hash, _ = command_identity(
            {"type": "command", "command": "user"}, event_name="SessionStart"
        )
        definition = (
            '[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\ntype="command"\ncommand="user"\n'
        )
        document = (definition if origin == "user" else "") + (
            f"[hooks.state.{json.dumps(key)}]\n"
            f"trusted_hash={json.dumps(user_hash)}\n"
            f"[hooks.state.{json.dumps(f'{source}:session_start:0:0')}]\n"
            'enabled=false\ntrusted_hash="revoked"\n'
        )
        layers = [ConfigLayer(user_path, "user", contents=document)]
        if origin == "project":
            layers.append(ConfigLayer(project_path, "project", contents=definition))
        if origin == "plugin":
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            (plugin_root / "hooks").mkdir()
            (plugin_root / ".codex-plugin/plugin.json").write_text('{"name":"bundle"}')
            (plugin_root / "hooks/hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [{"hooks": [{"type": "command", "command": "user"}]}]
                        }
                    }
                )
            )
        policy = ManagedHookPolicy(
            source=source,
            hooks_json=json.dumps(
                {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "managed-start"}]}],
                    "SessionEnd": [{"hooks": [{"type": "command", "command": "managed-end"}]}],
                }
            ),
            only_managed=only_managed,
        )
        calls = []

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def run(command, payload, **kwargs):
            calls.append((command.command, payload["hook_event_name"]))
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        monkeypatch.setattr(start, "run_command", run)
        monkeypatch.setattr(end, "run_command", run)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=origin == "plugin",
                plugin_dirs=(plugin_root,) if origin == "plugin" else (),
                managed_hook_policy=policy,
                hooks_enabled=enabled,
                configuration=LocalConfigState(tuple(layers)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            # Rebuilding ordinary configuration must retain the host policy.
            publish = await runtime._prepare_configuration_reload(
                runtime._settings.configuration, {}
            )
            publish()
            commands = runtime._graph._stop_hooks._snapshot["SessionStart"][0]
            assert [command.command for command in commands] == (
                ["managed-start", *([] if only_managed else ["user"])] if enabled else []
            )
            assert isinstance([e async for e in runtime.stream("INPUT")][-1], TurnCompleted)
        finally:
            await runtime.aclose()
        expected = [
            ("managed-start", "SessionStart"),
            *(([("user", "SessionStart")]) if not only_managed else []),
            ("managed-end", "SessionEnd"),
        ]
        if enabled:
            assert calls[-1:] == expected[-1:]
            assert sorted(calls[:-1]) == sorted(expected[:-1])
        else:
            assert calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_unloadable_required_session_end_rejects_runtime(tmp_path, managed, enabled):
    from corki.config.hooks import ManagedHookPolicy

    async def scenario():
        path = tmp_path / "config.toml"
        definition = {"SessionEnd": [{"hooks": [{"type": "mcp_tool"}]}]}
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            hooks_enabled=enabled,
            managed_hook_policy=ManagedHookPolicy(
                source=tmp_path / "requirements.toml", hooks_json=json.dumps(definition)
            )
            if managed
            else None,
            configuration=LocalConfigState(
                (
                    ConfigLayer(
                        path,
                        "user",
                        contents=(
                            '[[hooks.SessionEnd]]\n[[hooks.SessionEnd.hooks]]\ntype="mcp_tool"\n'
                        )
                        if not managed
                        else "",
                    ),
                )
            ),
        )

        async def create():
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
            )

        if managed and enabled:
            with pytest.raises(ValueError, match="failed to load required managed hooks"):
                await create()
        else:
            runtime = await create()
            try:
                await runtime._ensure_ready()
                commands, warnings = runtime._graph._stop_hooks._snapshot["SessionEnd"]
                assert not commands
                assert any("MCP hooks are not supported" in w for w in warnings) == enabled
            finally:
                await runtime.aclose()
        assert not calls

    asyncio.run(scenario())
