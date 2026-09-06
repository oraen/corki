"""API-key-free demonstration of Corki's complete coding harness loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest
from corki.models.types import ModelEvent
from corki.protocol.events import PlanUpdated, ToolCallCompleted, TurnCompleted
from corki.protocol.ids import ModelStepId, ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def _tool_item(
    request: ModelRequest,
    call_id: str,
    name: str,
    arguments: dict[str, object],
    step_id: ModelStepId,
) -> ToolCallItem:
    call = ToolCall(
        ToolCallId(call_id),
        name,
        arguments,
        raw_arguments=json.dumps(arguments, ensure_ascii=False),
    )
    return ToolCallItem(call, request.items[-1].turn_id, step_id)


class DemoModel:
    """Return a fixed inspect/plan/edit/verify/final sequence."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        index = len(self.requests)
        turn_id = request.items[-1].turn_id
        if index == 1:
            step_id = new_step_id()
            yield ModelCompleted(
                (
                    _tool_item(
                        request,
                        "plan-1",
                        "update_plan",
                        {
                            "plan": [
                                {"step": "inspect", "status": "completed"},
                                {"step": "write", "status": "in_progress"},
                                {"step": "verify", "status": "pending"},
                            ]
                        },
                        step_id,
                    ),
                    _tool_item(
                        request,
                        "inspect-1",
                        "exec_command",
                        {"cmd": "printf harness-inspected"},
                        step_id,
                    ),
                )
            )
        elif index == 2:
            yield ModelCompleted(
                (
                    _tool_item(
                        request,
                        "patch-1",
                        "apply_patch",
                        {
                            "patch": "*** Begin Patch\n"
                            "*** Add File: demo.txt\n"
                            "+created by core loop demo\n"
                            "*** End Patch"
                        },
                        new_step_id(),
                    ),
                )
            )
        elif index == 3:
            yield ModelCompleted(
                (
                    _tool_item(
                        request,
                        "verify-1",
                        "exec_command",
                        {"cmd": "test -f demo.txt && printf harness-verified"},
                        new_step_id(),
                    ),
                )
            )
        elif index == 4:
            yield ModelCompleted(
                (AssistantMessageItem("implementation complete", turn_id, new_step_id()),)
            )
        else:
            yield ModelCompleted((AssistantMessageItem("memory recalled", turn_id, new_step_id()),))

    async def aclose(self) -> None:
        return None


async def _run(workspace: Path) -> dict[str, object]:
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("DEMO_PROJECT_INSTRUCTION", encoding="utf-8")
    model = DemoModel()
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=workspace,
            command_yield_seconds=1,
            command_timeout_seconds=5,
        ),
        database_path=workspace / ".runtime" / "sessions.db",
        home_path=workspace / ".runtime",
        model=model,
    )
    first = [event async for event in runtime.stream("create and verify demo.txt")]
    second = [event async for event in runtime.stream("what did you just do?")]
    await runtime.aclose()

    first_request = model.requests[0]
    first_user = next(
        index for index, item in enumerate(first_request.items) if isinstance(item, UserMessageItem)
    )
    assert all(isinstance(item, ContextItem) for item in first_request.items[:first_user])
    assert "DEMO_PROJECT_INSTRUCTION" in "\n".join(
        item.content for item in first_request.items if isinstance(item, ContextItem)
    )
    remembered_messages = [
        item.content
        for item in model.requests[-1].items
        if isinstance(item, (UserMessageItem, AssistantMessageItem))
    ]
    assert remembered_messages == [
        "create and verify demo.txt",
        "implementation complete",
        "what did you just do?",
    ], remembered_messages
    assert (workspace / "demo.txt").read_text(encoding="utf-8") == "created by core loop demo\n"
    assert isinstance(first[-1], TurnCompleted) and isinstance(second[-1], TurnCompleted)

    return {
        "status": "ok",
        "model_steps": len(model.requests),
        "tool_calls": sum(isinstance(event, ToolCallCompleted) for event in first),
        "plan_recalled": any(isinstance(event, PlanUpdated) for event in first),
        "project_context_before_user": True,
        "cross_turn_memory": True,
    }


def main() -> None:
    with TemporaryDirectory(prefix="corki-core-demo-") as directory:
        print(json.dumps(asyncio.run(_run(Path(directory))), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
