"""End-to-end project skill, Codex plugin, and stdio MCP recall demo."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall

MCP_SERVER = r"""
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "demo", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [{
                "name": "echo",
                "description": "Echo through MCP",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True},
            }]
        }
    elif method == "tools/call":
        text = message["params"]["arguments"]["text"]
        result = {"content": [{"type": "text", "text": "mcp:" + text}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


def _call(
    request: ModelRequest, call_id: str, name: str, arguments: dict[str, object]
) -> ToolCallItem:
    return ToolCallItem(
        ToolCall(ToolCallId(call_id), name, arguments, raw_arguments=json.dumps(arguments)),
        request.items[-1].turn_id,
        new_step_id(),
    )


class ExtensionModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        turn_id = request.items[-1].turn_id
        if len(self.requests) == 1:
            yield ModelCompleted(
                (_call(request, "search-1", "tool_search", {"query": "fixture echo"}),)
            )
            return
        if len(self.requests) == 2:
            step_id = new_step_id()
            calls = (
                ToolCall(ToolCallId("skill-1"), "skill_read", {"name": "demo-recall"}),
                ToolCall(ToolCallId("plugin-1"), "plugin__demo__echo", {"text": "hello"}),
                ToolCall(ToolCallId("mcp-1"), "mcp__fixture::echo", {"text": "hello"}),
            )
            yield ModelCompleted(tuple(ToolCallItem(call, turn_id, step_id) for call in calls))
            return
        if len(self.requests) == 3:
            yield ModelCompleted(
                (AssistantMessageItem("checking extension results", turn_id, new_step_id()),),
                end_turn=False,
            )
            return
        yield ModelCompleted((AssistantMessageItem("extensions recalled", turn_id, new_step_id()),))

    async def aclose(self) -> None:
        return None


def _create_extensions(workspace: Path) -> None:
    (workspace / "pyproject.toml").write_text(
        "[project]\nname='extensions-demo'\n", encoding="utf-8"
    )
    skill = workspace / ".corki" / "skills" / "demo-recall" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: demo-recall\ndescription: Demonstrate project skill recall.\n---\n\n"
        "PROJECT_SKILL_BODY\n",
        encoding="utf-8",
    )
    plugin = workspace / ".corki" / "plugins" / "demo"
    manifest = plugin / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "1.0.0",
                "description": "Demo plugin",
                "entrypoint": "plugin.py:register",
            }
        ),
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        "def register(api):\n"
        "    api.register_tool(\n"
        "        name='echo', description='Echo through a plugin',\n"
        "        parameters={'type': 'object', 'properties': {'text': {'type': 'string'}}, "
        "'required': ['text'], 'additionalProperties': False},\n"
        "        handler=lambda args, context: {'plugin': args['text']},\n"
        "    )\n",
        encoding="utf-8",
    )


async def _run(workspace: Path) -> dict[str, object]:
    _create_extensions(workspace)
    model = ExtensionModel()
    mcp = MCPServerSettings(
        "fixture",
        "stdio",
        command=sys.executable,
        args=("-u", "-c", MCP_SERVER),
        cwd=workspace,
        timeout_seconds=5,
    )
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=workspace,
            mcp_servers=(mcp,),
            plugin_dirs=(workspace / ".corki/plugins",),
        ),
        database_path=workspace / ".runtime" / "sessions.db",
        home_path=workspace / ".runtime",
        model=model,
    )
    events = [event async for event in runtime.stream("use $demo-recall and all extensions")]
    await runtime.aclose()

    advertised = {tool.name for tool in model.requests[0].tools}
    assert {"skill_read", "plugin__demo__echo", "tool_search"} <= advertised
    assert "mcp__fixture::echo" not in advertised
    loaded = {tool.name for tool in model.requests[1].tools}
    assert "mcp__fixture::echo" in loaded
    context = "\n".join(
        item.content for item in model.requests[0].items if isinstance(item, ContextItem)
    )
    assert "PROJECT_SKILL_BODY" in context
    assert "plugin__demo__echo" in context
    assert "Deferred sources: fixture" in context
    assert "mcp__fixture::echo" not in context
    outputs = [item.content for item in model.requests[2].items if isinstance(item, ToolResultItem)]
    assert any("PROJECT_SKILL_BODY" in output for output in outputs)
    assert any('"plugin": "hello"' in output for output in outputs)
    assert any(value.endswith("\nOutput:\nmcp:hello") for value in outputs)
    assert len(model.requests) == 4
    assert any(
        isinstance(item, AssistantMessageItem) and item.content == "checking extension results"
        for item in model.requests[3].items
    )
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].final_answer == "extensions recalled"
    return {
        "status": "ok",
        "skill_recalled": True,
        "plugin_called": True,
        "mcp_called": True,
        "advertised_tools": sorted(loaded),
        "initial_tools": sorted(advertised),
        "mcp_discovered": True,
        "model_continuation": True,
    }


def main() -> None:
    with TemporaryDirectory(prefix="corki-extensions-demo-") as directory:
        print(json.dumps(asyncio.run(_run(Path(directory))), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
