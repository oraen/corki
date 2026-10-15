"""Authoritative nested values through the real worker, graph and durable ledger."""

import asyncio
import json
import sqlite3

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.tools import MCPTool
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


async def run_script(tmp_path, script, *, registry=None, mode="code_mode_only"):
    requests = []

    class Model:
        async def stream(self, request):
            requests.append(request)
            turn = request.items[-1].turn_id
            if len(requests) == 1:
                call = ToolCall(
                    new_tool_call_id(), "exec", None, raw_arguments=script, input_kind="freeform"
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
            else:
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

        async def aclose(self):
            pass

    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False, tool_mode=mode),
        database_path=tmp_path / "sessions.db",
        model=Model(),
        registry=registry,
    )
    try:
        events = [event async for event in runtime.stream("execute")]
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert len(requests) == 2
        result = next(
            item
            for item in requests[-1].items
            if isinstance(item, ToolResultItem) and item.tool_name == "exec"
        )
        assert "Script completed" in result.content, result.content
        assert not runtime._code_mode.cells
        with sqlite3.connect(tmp_path / "sessions.db") as db:
            ledger = {
                name: json.loads(payload)
                for name, payload in db.execute("SELECT tool_name,result_json FROM tool_executions")
            }
        return result.content, ledger, requests[-1]
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_shell_returns_fields_not_formatted_diagnostics(tmp_path, mode):
    content, ledger, request = asyncio.run(
        run_script(
            tmp_path,
            "const r = await tools.exec_command({cmd:'printf answer; exit 7', login:false}); "
            "text({kind:typeof r,output:r.output,exit_code:r.exit_code,"
            "wall:typeof r.wall_time_seconds,session:'session_id' in Object(r)});",
            mode=mode,
        )
    )
    assert '{"kind":"object","output":"answer","exit_code":7,"wall":"number",' in content
    assert '"session":false' in content
    assert ledger["exec_command"]["code_mode_output"]["value"]["exit_code"] == 7
    assert not any(
        isinstance(i, ToolResultItem) and i.tool_name == "exec_command" for i in request.items
    )


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_mcp_preserves_protocol_value_without_private_result_metadata(tmp_path, empty, mode):
    raw = {"content": [], "_meta": {"client_secret": "PRIVATE"}}
    if not empty:
        raw.update(
            {
                "content": [
                    {
                        "type": "image",
                        "data": "YWJj",
                        "mimeType": "image/png",
                        "_meta": {"codex/imageDetail": "original"},
                    }
                ],
                "structuredContent": {"rows": [{"answer": 42}]},
                "isError": True,
            }
        )

    class Client:
        async def call_tool(self, name, arguments):
            return raw

    tool = MCPTool("fixture", {"name": "read"}, Client())
    registry = ToolRegistry()
    registry.register(tool)
    content, ledger, request = asyncio.run(
        run_script(
            tmp_path,
            "text(await tools.mcp__fixture__read({}));",
            registry=registry,
            mode=mode,
        )
    )
    expected = {k: v for k, v in raw.items() if k != "_meta"}
    assert json.dumps(expected, ensure_ascii=False, separators=(",", ":")) in content
    nested = ledger[tool.spec.name]
    assert nested["code_mode_output"]["value"] == expected
    assert json.loads(nested["mcp_result_json"]) == raw
    public_ledger = {name: dict(value) for name, value in ledger.items()}
    del public_ledger[tool.spec.name]["mcp_result_json"]
    assert "PRIVATE" not in json.dumps(public_ledger)
    assert "PRIVATE" not in content + repr(request.items)
    assert raw["_meta"] == {"client_secret": "PRIVATE"}


@pytest.mark.parametrize("value", [None, False, 0, 1.5, "", [1, None], {"rows": [42]}])
def test_explicit_json_values_reach_javascript_without_text_coercion(tmp_path, value):
    class Tool:
        spec = ToolSpec("typed", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(
                call.id, call.name, "diagnostic", code_mode_output=CodeModeOutput(value)
            )

    registry = ToolRegistry()
    registry.register(Tool())
    content, ledger, _ = asyncio.run(
        run_script(
            tmp_path,
            "const r = await tools.typed({}); text({value:r});",
            registry=registry,
        )
    )
    assert json.dumps({"value": value}, separators=(",", ":")) in content
    assert ledger["typed"]["code_mode_output"]["value"] == value


def test_json_looking_text_is_not_automatically_parsed(tmp_path):
    class Tool:
        spec = ToolSpec("plain", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, '{"value":42}')

    registry = ToolRegistry()
    registry.register(Tool())
    content, ledger, _ = asyncio.run(
        run_script(
            tmp_path,
            "const r = await tools.plain({}); text(typeof r); text(r);",
            registry=registry,
        )
    )
    assert 'string\n{"value":42}' in content
    assert "code_mode_output" not in ledger["plain"]


def test_oversized_bridge_value_rejects_promise_without_replaying_tool(tmp_path):
    calls = []

    class Tool:
        spec = ToolSpec("large", "fixture", {"type": "object"})

        async def execute(self, call, context):
            calls.append(call)
            return ToolResult(
                call.id,
                call.name,
                "diagnostic",
                code_mode_output=CodeModeOutput({"large": "x" * 4_000_001}),
            )

    registry = ToolRegistry()
    registry.register(Tool())
    content, ledger, _ = asyncio.run(
        run_script(
            tmp_path,
            "try { await tools.large({}); } catch(e) { text('caught: '+e); }",
            registry=registry,
        )
    )
    assert "caught:" in content and "limit" in content
    assert len(calls) == 1
    assert len(ledger["large"]["code_mode_output"]["value"]["large"]) == 4_000_001


def test_running_shell_session_id_can_be_passed_to_write_stdin(tmp_path):
    source = (
        "const running = await tools.exec_command({"
        'cmd:\'stty -echo; read answer; printf "%s" "$answer"\','
        "tty:true,login:false,yield_time_ms:50});"
        "text(typeof running.session_id);"
        "const done = await tools.write_stdin({session_id:running.session_id,"
        "chars:'finished\\n',yield_time_ms:1000});"
        "text({output:done.output,exit_code:done.exit_code});"
    )
    content, ledger, _ = asyncio.run(run_script(tmp_path, source))
    assert 'string\n{"output":"finished","exit_code":0}' in content
    running = ledger["exec_command"]["code_mode_output"]["value"]
    assert "session_id" in running and "exit_code" not in running
    done = ledger["write_stdin"]["code_mode_output"]["value"]
    assert "session_id" not in done and done["exit_code"] == 0


def test_invalid_typed_output_is_an_observation_in_the_real_script(tmp_path):
    class Tool:
        spec = ToolSpec("invalid", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(
                call.id,
                call.name,
                "not a valid result",
                code_mode_output=CodeModeOutput(float("nan")),
            )

    registry = ToolRegistry()
    registry.register(Tool())
    content, ledger, _ = asyncio.run(
        run_script(
            tmp_path,
            "try { await tools.invalid({}); } catch(e) { text(typeof e); text(e); }",
            registry=registry,
        )
    )
    assert "string\nValueError:" in content and "JSON compliant" in content
    assert ledger["invalid"]["is_error"] is True
    assert "code_mode_output" not in ledger["invalid"]
