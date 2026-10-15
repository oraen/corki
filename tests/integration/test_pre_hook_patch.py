"""Patch Hook aliases and command translation reach actual native file execution."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest
from test_bundled_execution import compiler as compiler
from test_filesystem_helper_runtime import workspace_policy
from test_patch_approvals import Model, patch

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["rewrite", "invalid", "block", "decline"])
def test_patch_hook_command_is_revalidated_and_reviewed(tmp_path, compiler, mode, action):
    async def scenario():
        model = Model(mode)
        revised = patch("*** Add File: revised.txt\n+changed")
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": 123 if action == "invalid" else revised},
            }
        }
        if action == "block":
            output = {"decision": "block", "reason": "patch denied"}
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; "
                "p=json.load(sys.stdin); Path('patch-hook.json').write_text(json.dumps(p)); "
                f"print(json.dumps({output!r}))",
            ]
        )
        matcher = "Write|Edit|apply_patch" if action == "rewrite" else "Edit"
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command},
            event_name="PreToolUse",
            matcher=matcher,
        )[0]
        definition = (
            f"[[hooks.PreToolUse]]\nmatcher={json.dumps(matcher)}\n"
            f"[[hooks.PreToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        policy = replace(workspace_policy(compiler, tmp_path), approval_policy_json='"untrusted"')
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode=mode,
                execution_permissions=policy,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            model=model,
            home_path=tmp_path / "host",
            database_path=tmp_path / "state.db",
        )
        prompts = []

        async def respond(request):
            prompts.append(request)
            assert request.kind == "patch_approval"
            params = request.params["_meta"]["tool_params"]
            assert params["patch"] == revised
            assert params["files"] == [str(tmp_path / "revised.txt")]
            assert not (tmp_path / "allowed.txt").exists()
            assert not (tmp_path / "revised.txt").exists()
            runtime.respond_execution_approval(
                request.request_id, "decline" if action == "decline" else "accept"
            )

        runtime.set_execution_approval_handler(respond)
        try:
            events = [event async for event in runtime.stream("apply patch")]
            assert sum(isinstance(event, TurnCompleted) for event in events) == 1
            assert sum(isinstance(event, HookCompleted) for event in events) == 1
            payload = json.loads((tmp_path / "patch-hook.json").read_text())
            assert payload["tool_name"] == "apply_patch"
            assert payload["tool_input"] == {"command": model.patch}
            assert len(prompts) == (1 if action in {"rewrite", "decline"} else 0)
            assert not (tmp_path / "allowed.txt").exists()
            if action == "rewrite":
                assert (tmp_path / "revised.txt").read_text() == "changed\n"
            else:
                assert not (tmp_path / "revised.txt").exists()
            history = await runtime._repository.load_items(runtime.thread_id)
            calls = [
                item
                for item in history
                if isinstance(item, ToolCallItem) and item.call.name == "apply_patch"
            ]
            if mode == "direct":
                assert len(calls) == 1 and calls[0].call.arguments == {"patch": model.patch}
            results = [
                item
                for item in history
                if isinstance(item, ToolResultItem) and item.tool_name == "apply_patch"
            ]
            if mode == "direct":
                assert len(results) == 1
                assert results[0].is_error == (action != "rewrite")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
