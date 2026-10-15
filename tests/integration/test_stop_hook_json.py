"""File discovery reaches actual Stop execution through frozen settings."""

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
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, WarningEvent
from corki.protocol.items import AssistantMessageItem, new_step_id


@pytest.mark.parametrize(
    "case", ["approved", "untrusted", "self_approved", "frozen", "invalid", "both", "disabled"]
)
def test_json_hook_file_enters_runtime_with_independent_approval(tmp_path, case):
    async def scenario():
        project = tmp_path / "project"
        folder = project / ".corki"
        folder.mkdir(parents=True)
        source = folder / "hooks.json"
        user = tmp_path / "user.toml"

        def command(label):
            return shlex.join(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    f"Path('hook-calls').open('a').write({label!r}+'\\n'); print('{{}}')",
                ]
            )

        handler = {"type": "command", "command": command("json")}
        fingerprint = command_identity(handler)[0]
        key = f"{source}:stop:0:0"
        document = {
            "hooks": {"Stop": [{"hooks": [handler]}], "state": {key: {"trusted_hash": fingerprint}}}
        }
        source.write_text("{" if case == "invalid" else json.dumps(document))
        approval = f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        user.write_text(
            f"[projects.{json.dumps(str(project))}]\n"
            f"trust_level='{'untrusted' if case == 'disabled' else 'trusted'}'\n"
            + (approval if case not in ("untrusted", "self_approved") else "")
        )
        if case == "self_approved":
            (folder / "config.toml").write_text(approval)
        if case == "disabled":
            # An unadmitted, malformed project config cannot break Stop either.
            (folder / "config.toml").write_text("[[broken")
        if case == "both":
            toml_command = command("toml")
            toml_hash = command_identity({"type": "command", "command": toml_command})[0]
            (folder / "config.toml").write_text(
                "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype='command'\n"
                f"command={json.dumps(toml_command)}\n"
            )
            user.write_text(
                user.read_text()
                + f"[hooks.state.{json.dumps(str(folder / 'config.toml') + ':stop:0:0')}]\n"
                + f"trusted_hash={json.dumps(toml_hash)}\n"
            )
        settings = replace(
            CorkiSettings.for_directory(project, config_file=user),
            skills_enabled=False,
            plugins_enabled=False,
        )
        if case == "frozen":
            source.write_text(
                json.dumps(
                    {"hooks": {"Stop": [{"hooks": [{**handler, "command": command("changed")}]}]}}
                )
            )

        class Model:
            async def stream(self, request):
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
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted)
            calls = project / "hook-calls"
            if case in ("approved", "frozen", "both"):
                # Independent synchronous handlers execute concurrently; file
                # effects need not finish in their declaration order.
                assert sorted(calls.read_text().splitlines()) == (
                    ["json", "toml"] if case == "both" else ["json"]
                )
                expected_keys = [key]
                if case == "both":
                    expected_keys.append(f"{folder / 'config.toml'}:stop:0:0")
                for event_type in (HookStarted, HookCompleted):
                    assert [
                        event.run.key for event in events if isinstance(event, event_type)
                    ] == expected_keys
            else:
                assert not calls.exists()
            if case == "invalid":
                assert any(isinstance(e, WarningEvent) and "JSON" in e.message for e in events)
            if case == "both":
                assert any(isinstance(e, WarningEvent) and "both" in e.message for e in events)
        finally:
            await runtime.aclose()

        if case == "frozen":
            cold_settings = replace(
                CorkiSettings.for_directory(project, config_file=user),
                skills_enabled=False,
                plugins_enabled=False,
            )
            cold = await LangGraphRuntime.acreate(
                settings=cold_settings,
                model=Model(),
                database_path=tmp_path / "cold.db",
                home_path=tmp_path / "home",
            )
            try:
                events = [event async for event in cold.stream("finish after reload")]
                assert isinstance(events[-1], TurnCompleted)
                assert (project / "hook-calls").read_text().splitlines() == ["json"]
                assert any(isinstance(e, WarningEvent) and "untrusted" in e.message for e in events)
            finally:
                await cold.aclose()

    asyncio.run(scenario())
