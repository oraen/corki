"""Hook-facing MCP identity is separate from ordinary model names and RPC routes."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.mcp.names import normalize_tool_names
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "nested,raw,expected",
    [
        (False, None, {"value": "original"}),
        (True, None, {"value": "original"}),
        (False, "", {}),
        (False, " \u2003", {}),
        (False, "{broken", "{broken"),
        (False, "[1,true]", [1, True]),
        (False, "null", None),
        (False, '{"value":"raw"}', {"value": "raw"}),
        (False, "\u001c", "\u001c"),
    ],
)
@pytest.mark.parametrize("prefix", [False, True])
@pytest.mark.parametrize("remote", ["write", "exec_command"])
@pytest.mark.parametrize("rewrite", [False, True])
def test_mcp_hook_name_and_rewrite_do_not_change_rpc_route(
    tmp_path, nested, raw, expected, prefix, remote, rewrite
):
    async def scenario():
        calls = []

        class Client:
            async def call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {"content": [{"type": "text", "text": "remote result"}]}

        tool = MCPTool("docs", {"name": remote, "inputSchema": {"type": "object"}}, Client())
        tool = tool.with_model_name(normalize_tool_names((("docs", remote),), prefix=prefix)[0])
        canonical = tool.spec.name
        hook_name = f"mcp__docs__{remote}"
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"value": "rewritten"},
            }
        }
        if not rewrite:
            output = {}
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "Path('mcp-hook.json').write_text(json.dumps(p)); "
                f"print(json.dumps({output!r}))",
            ]
        )
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command}, event_name="PreToolUse", matcher=hook_name
        )[0]
        definition = (
            f"[[hooks.PreToolUse]]\nmatcher={json.dumps(hook_name)}\n"
            f"[[hooks.PreToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    completed = request_call(
                        request,
                        "exec" if nested else canonical,
                        f'text(await tools.{canonical.replace("::", "__")}({{value:"original"}}))'
                        if nested
                        else {"value": "original"},
                    )
                    if raw is not None:
                        item = completed.items[0]
                        completed = replace(
                            completed,
                            items=(
                                replace(
                                    item,
                                    call=replace(
                                        item.call,
                                        arguments=None,
                                        raw_arguments=raw,
                                    ),
                                ),
                            ),
                        )
                    yield completed
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="code_mode_only" if nested else "direct",
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            registry=registry,
            model=Model(),
            home_path=tmp_path,
            database_path=tmp_path / "state.db",
        )
        try:
            events = [event async for event in runtime.stream("call MCP")]
            assert any(isinstance(event, TurnCompleted) for event in events)
            invalid = raw in {"{broken", "[1,true]", "null", "\u001c"}
            expected_arguments = (
                {"value": "rewritten"} if rewrite else None if raw in {"", " \u2003"} else expected
            )
            assert calls == ([] if invalid and not rewrite else [(remote, expected_arguments)])
            payload = json.loads((tmp_path / "mcp-hook.json").read_text())
            assert payload["tool_name"] == hook_name
            assert payload["tool_input"] == expected
            assert sum(isinstance(event, HookCompleted) for event in events) == 1
            if not nested:
                history = await runtime._repository.load_items(runtime.thread_id)
                original = next(item.call for item in history if isinstance(item, ToolCallItem))
                if raw is not None:
                    assert original.raw_arguments == raw and original.arguments is None
                result = next(item for item in history if isinstance(item, ToolResultItem))
                assert result.is_error == (invalid and not rewrite)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
