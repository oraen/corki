"""API-key-free exercise of every default Corki tool through the agent loop."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import PlanUpdated, ToolCallCompleted, TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def _completed_call(
    request: ModelRequest,
    name: str,
    arguments: dict[str, object],
) -> ModelCompleted:
    call = ToolCall(
        ToolCallId(f"demo-{name}"),
        name,
        arguments,
        raw_arguments=json.dumps(arguments),
    )
    return ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))


class BuiltinToolModel:
    """Drive deterministic calls while still consuming real prior tool output."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        step = len(self.requests)
        if step == 1:
            yield _completed_call(
                request,
                "update_plan",
                {
                    "plan": [
                        {"step": "edit", "status": "in_progress"},
                        {"step": "inspect", "status": "pending"},
                    ]
                },
            )
        elif step == 2:
            yield _completed_call(
                request,
                "apply_patch",
                {
                    "patch": "*** Begin Patch\n"
                    "*** Add File: tool-demo.txt\n"
                    "+all tools reached\n"
                    "*** End Patch"
                },
            )
        elif step == 3:
            code = "import sys;print('ready',flush=True);print(sys.stdin.readline().strip())"
            command = f"{shlex.quote(sys.executable)} -u -c {shlex.quote(code)}"
            yield _completed_call(
                request,
                "exec_command",
                {"cmd": command, "login": False, "yield_time_ms": 50},
            )
        elif step == 4:
            last_result = next(
                item for item in reversed(request.items) if isinstance(item, ToolResultItem)
            )
            match = re.search(r"session ID ([0-9a-f-]+)", last_result.content)
            if match is None:
                raise AssertionError(last_result.content)
            yield _completed_call(
                request,
                "write_stdin",
                {"session_id": match.group(1), "chars": "continued\n", "yield_time_ms": 500},
            )
        elif step == 5:
            yield _completed_call(request, "view_image", {"path": "pixel.png"})
        else:
            yield ModelCompleted(
                (
                    AssistantMessageItem(
                        "all default tools completed",
                        request.items[-1].turn_id,
                        new_step_id(),
                    ),
                )
            )

    async def aclose(self) -> None:
        return None


async def _run(workspace: Path) -> dict[str, object]:
    # A valid 1x1 transparent PNG keeps the visual-tool exercise deterministic.
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X1n0WQAAAABJRU5ErkJggg=="
    )
    (workspace / "pixel.png").write_bytes(pixel)
    model = BuiltinToolModel()
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=workspace,
            command_yield_seconds=0.05,
            command_timeout_seconds=5,
        ),
        database_path=workspace / ".runtime" / "sessions.db",
        home_path=workspace / ".runtime",
        model=model,
    )
    events = [event async for event in runtime.stream("exercise every default tool")]
    await runtime.aclose()

    advertised = {tool.name for tool in model.requests[0].tools}
    expected = {"exec_command", "write_stdin", "apply_patch", "update_plan", "view_image"}
    assert expected <= advertised
    assert (workspace / "tool-demo.txt").read_text(encoding="utf-8") == "all tools reached\n"
    write_result = next(
        item
        for item in model.requests[4].items
        if isinstance(item, ToolResultItem) and item.tool_name == "write_stdin"
    )
    assert "continued" in write_result.content
    image_result = next(
        item
        for item in model.requests[-1].items
        if isinstance(item, ToolResultItem) and item.tool_name == "view_image"
    )
    assert len(image_result.attachments) == 1
    assert sum(isinstance(event, ToolCallCompleted) for event in events) == 5
    assert any(isinstance(event, PlanUpdated) for event in events)
    assert isinstance(events[-1], TurnCompleted)
    return {
        "status": "ok",
        "tools": sorted(expected),
        "long_process_resumed": True,
        "image_attachment_recalled": True,
    }


def main() -> None:
    with TemporaryDirectory(prefix="corki-tools-demo-") as directory:
        print(json.dumps(asyncio.run(_run(Path(directory))), sort_keys=True))


if __name__ == "__main__":
    main()
