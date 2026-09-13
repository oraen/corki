"""Trusted local Stop commands drive the actual graph, never model tool dispatch."""

import asyncio
import json
import shlex
import sys
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolResultItem, new_step_id


@pytest.mark.parametrize(
    "trust",
    [
        "approved",
        "missing",
        "modified",
        "disabled",
        "project",
        "project_approved",
        "legacy",
        "approved_defaults",
        "approved_windows",
        "approved_windows_alias",
        "canonical",
        "canonical_disabled",
        "canonical_modified",
        "canonical_empty",
        "canonical_invalid",
        "layer_enable",
        "layer_preserve_disabled",
        "layer_disabled_source",
        "layer_project_override",
        "layer_trimmed",
        "layer_invalid",
    ],
)
def test_stop_hook_continuation_requires_independent_user_approval(tmp_path, trust):
    async def scenario():
        script = (
            "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
            "f=Path('hook-calls'); f.open('a').write(str(p['stop_hook_active'])+'\\n'); "
            "print(json.dumps({} if p['stop_hook_active'] else "
            "{'decision':'block','reason':'VERIFY_BEFORE_FINISH'}))"
        )
        command = shlex.join([sys.executable, "-c", script])
        fingerprint, _ = command_identity({"type": "command", "command": command})
        if trust == "legacy":
            fingerprint = (
                "sha256:"
                + sha256(
                    json.dumps(
                        {"type": "command", "command": command, "timeout": 600, "async": False},
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
            )
        user_file, project_file = tmp_path / "user.toml", tmp_path / "project.toml"
        source = project_file if trust.startswith("project") else user_file
        key = f"file:{source}:stop:0:0"
        approval = (
            f"[hooks.state.{json.dumps(key)}]\n"
            f"trusted_hash={json.dumps('changed' if trust == 'modified' else fingerprint)}\n"
            f"enabled={'false' if trust == 'disabled' else 'true'}\n"
        )
        if trust == "canonical":
            approval = approval.replace(json.dumps(key), json.dumps(key.removeprefix("file:")))
        elif trust.startswith("canonical_"):
            approval += f"[hooks.state.{json.dumps(key.removeprefix('file:'))}]\n"
            if trust == "canonical_disabled":
                approval += f"enabled=false\ntrusted_hash={json.dumps(fingerprint)}\n"
            elif trust == "canonical_modified":
                approval += 'trusted_hash="changed"\n'
            elif trust == "canonical_invalid":
                approval += 'enabled="not a bool"\n'
        definition = (
            "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype='command'\ncommand="
            + json.dumps(command)
            + "\n"
        )
        if trust == "approved_defaults":
            definition += "timeout=600\nasync=false\nadditionalContextLimit=17\nunknown='ignored'\n"
        if trust in ("approved_windows", "approved_windows_alias"):
            field = "commandWindows" if trust == "approved_windows" else "command_windows"
            definition += f"{field}='exit 92'\n"
        if trust.startswith("project"):
            layers = (
                ConfigLayer(
                    user_file, "user", contents=approval if trust == "project_approved" else ""
                ),
                ConfigLayer(project_file, "project", contents=definition + approval),
            )
        else:
            layers = (
                ConfigLayer(
                    user_file,
                    "user",
                    contents=definition + ("" if trust == "missing" else approval),
                ),
            )
        if trust.startswith("layer_"):
            canonical = key.removeprefix("file:")
            state_header = f"[hooks.state.{json.dumps(canonical)}]\n"
            lower = state_header + f"trusted_hash={json.dumps(fingerprint)}\n"
            if trust in ("layer_enable", "layer_preserve_disabled"):
                lower += "enabled=false\n"
            upper = state_header
            if trust in ("layer_enable", "layer_project_override"):
                upper += "enabled=" + ("true" if trust == "layer_enable" else "false") + "\n"
            elif trust == "layer_preserve_disabled":
                upper += f"trusted_hash={json.dumps(fingerprint)}\n"
            elif trust == "layer_invalid":
                upper += 'enabled="not a bool"\n'
            elif trust == "layer_trimmed":
                lower = lower.replace(json.dumps(canonical), json.dumps(f"  {canonical}  "))
            layers = (
                ConfigLayer(user_file, "user", contents=definition),
                ConfigLayer(
                    tmp_path / "approval.toml",
                    "user",
                    disabled_reason="definitions disabled"
                    if trust == "layer_disabled_source"
                    else None,
                    contents=lower,
                ),
                ConfigLayer(
                    tmp_path / "override.toml",
                    "project" if trust == "layer_project_override" else "user",
                    contents=upper,
                ),
            )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 2:
                    assert any(
                        isinstance(i, ContextItem) and i.content == "VERIFY_BEFORE_FINISH"
                        for i in request.items
                    )
                assert len(requests) <= 2
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState(layers),
            ),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("finish only after checking")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            allowed = trust in ("approved", "project_approved", "approved_defaults", "canonical")
            allowed |= trust.startswith("layer_") and trust != "layer_preserve_disabled"
            allowed |= trust in ("approved_windows", "approved_windows_alias")
            assert len(requests) == (2 if allowed else 1)
            if allowed:
                assert (tmp_path / "hook-calls").read_text().splitlines() == ["False", "True"]
            else:
                assert not (tmp_path / "hook-calls").exists()
            history = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(i, ToolResultItem) for i in history)
            assert sum(
                isinstance(i, ContextItem) and i.content_kind == "hook.stop.feedback"
                for i in history
            ) == int(allowed)
            if trust in ("missing", "modified", "project", "legacy"):
                assert any(isinstance(e, WarningEvent) and "untrusted" in e.message for e in events)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
