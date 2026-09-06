"""Persistence port owned by the session/memory domain."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from corki.models.types import ModelCompleted
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ConversationItem
from corki.protocol.tools import ToolCall, ToolResult
from corki.sessions.models import TurnRecord


class SessionRepository(Protocol):
    async def create_thread(self, thread_id: ThreadId, cwd: Path) -> None: ...

    async def save_turn(self, turn: TurnRecord) -> None: ...

    async def append_items(
        self, thread_id: ThreadId, items: tuple[ConversationItem, ...]
    ) -> None: ...

    async def load_items(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]: ...

    async def latest_thread(self, cwd: Path | None = None) -> ThreadId | None: ...

    async def latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None: ...

    async def claim_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, call: ToolCall
    ) -> ToolResult | None: ...

    async def complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult
    ) -> None: ...

    async def load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None: ...

    async def commit_model_step(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        step_index: int,
        completed: ModelCompleted,
    ) -> None: ...

    async def close(self) -> None: ...
