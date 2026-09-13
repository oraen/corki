"""Persistence port owned by the session/memory domain."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from corki.models.failure import ModelFailure
from corki.models.types import ModelCompleted
from corki.protocol.ids import SessionId, ThreadId, TurnId
from corki.protocol.items import ConversationItem
from corki.protocol.memory import ThreadMemoryMode
from corki.protocol.session_source import DEFAULT_SESSION_SOURCE, SessionSource
from corki.protocol.settings import ThreadModelSettings
from corki.protocol.tools import ToolCall, ToolResult
from corki.sessions.display import (
    DisplayItemsCursor,
    DisplayItemsPage,
    DisplayTurnsCursor,
    DisplayTurnsPage,
)
from corki.sessions.fork import ForkSnapshot
from corki.sessions.models import ContextUsage, DisplayHistory, TurnRecord, TurnStatus


class SessionRepository(Protocol):
    async def load_fork_snapshot(self, thread_id: ThreadId) -> ForkSnapshot:
        """Read historical facts in one snapshot, without taking execution ownership."""
        ...

    async def fork_thread(
        self,
        thread_id: ThreadId,
        cwd: Path,
        *,
        memory_mode: ThreadMemoryMode,
        session_id: SessionId,
        session_source: SessionSource,
        source_thread_id: ThreadId,
        before_user_message: int | None = None,
        snapshot: ForkSnapshot | None = None,
    ) -> None:
        """Atomically publish an independent snapshot; never copy execution obligations.

        None keeps committed history with an interrupted boundary for active turns.
        An index cuts before that original user message; retries reuse committed data.
        """
        ...

    async def thread_exists(self, thread_id: ThreadId) -> bool:
        """Distinguish existing thread metadata from a caller-selected new identity."""
        ...

    async def create_thread(
        self,
        thread_id: ThreadId,
        cwd: Path,
        *,
        memory_mode: ThreadMemoryMode = ThreadMemoryMode.ENABLED,
        session_id: SessionId | None = None,
        session_source: SessionSource = DEFAULT_SESSION_SOURCE,
    ) -> None:
        """Atomically create with initial source mode; preserve an existing thread's metadata."""
        ...

    async def load_thread_session_id(self, thread_id: ThreadId) -> SessionId:
        """Return authoritative stored identity, failing for absent or invalid metadata."""
        ...

    async def materialize_transcript(self, thread_id: ThreadId) -> Path | None:
        """Return a maintained local JSONL path, or None for nonlocal/missing storage."""
        ...

    async def load_thread_source(self, thread_id: ThreadId) -> SessionSource:
        """Read historical provenance independently of the current initiating host."""
        ...

    async def set_thread_memory_mode(self, thread_id: ThreadId, mode: ThreadMemoryMode) -> bool:
        """Persist source eligibility only; return False when the thread does not exist."""
        ...

    async def save_thread_model_settings(
        self, thread_id: ThreadId, settings: ThreadModelSettings
    ) -> None:
        """Commit the complete cold-resume group without rewriting history or source mode."""
        ...

    async def load_thread_model_settings(self, thread_id: ThreadId) -> ThreadModelSettings | None:
        """Return None for legacy threads with no persisted model defaults."""
        ...

    async def save_turn(self, turn: TurnRecord) -> None: ...

    async def retry_turn_terminal(self, turn: TurnRecord) -> None:
        """Atomically finish the same admission or confirm an identical stored result."""
        ...

    async def confirm_turn_terminal(self, turn: TurnRecord) -> bool:
        """Independently confirm the exact terminal payload after an ambiguous write."""
        ...

    async def append_items(
        self, thread_id: ThreadId, items: tuple[ConversationItem, ...]
    ) -> None: ...

    async def load_items(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]: ...

    async def load_display_snapshot(self, thread_id: ThreadId) -> DisplayHistory:
        """Read conversation items and turn terminal metadata from one database snapshot."""
        ...

    async def load_display_items_page(
        self, thread_id: ThreadId, *, cursor: DisplayItemsCursor | None = None, limit: int = 100
    ) -> DisplayItemsPage:
        """Read an older raw item batch; terminal metadata is a separate contract."""
        ...

    async def load_display_turns_page(
        self, thread_id: ThreadId, *, cursor: DisplayTurnsCursor | None = None, limit: int = 100
    ) -> DisplayTurnsPage:
        """Read bounded chronological Turn metadata independent of item pages."""
        ...

    async def contains_display_item(
        self, thread_id: ThreadId, *, kind: str, identity: str, through_sequence: int
    ) -> bool:
        """Check an identity at a fixed display boundary without loading full history."""
        ...

    async def load_context_usage(self, thread_id: ThreadId) -> ContextUsage | None: ...

    async def latest_thread(self, cwd: Path | None = None) -> ThreadId | None: ...

    async def latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None: ...

    async def load_turn_status(self, thread_id: ThreadId, turn_id: TurnId) -> TurnStatus | None:
        """Read one thread-scoped lifecycle fact; missing is not proof of completion."""
        ...

    async def load_hook_batch(
        self, thread_id: ThreadId, turn_id: TurnId, batch_key: str
    ) -> tuple[dict, dict] | None:
        """Read an immutable batch and its execution records at one storage snapshot."""
        ...

    async def load_hook_batch_keys(self, thread_id: ThreadId, prefix: str) -> tuple:
        """Read admitted batch identities in insertion order across this Thread's Turns."""
        ...

    async def save_hook_batch(
        self, thread_id: ThreadId, turn_id: TurnId, batch_key: str, snapshot: dict
    ) -> None:
        """Persist the complete admitted batch before its first external side effect."""
        ...

    async def load_hook_executions(
        self, thread_id: ThreadId, turn_id: TurnId, prefix: str
    ) -> tuple[tuple[str, dict, dict | None], ...]:
        """Read immutable hook facts without claiming or repeating an effect."""
        ...

    async def has_hook_executions(self, thread_id: ThreadId, turn_id: TurnId, prefix: str) -> bool:
        """Check whether a hook batch already began, without claiming a new side effect."""
        ...

    async def claim_hook_execution(
        self, thread_id: ThreadId, turn_id: TurnId, execution_key: str, request: dict
    ) -> dict | None:
        """Return a completed result or claim once; unknown outcomes must not rerun."""
        ...

    async def complete_hook_execution(
        self, thread_id: ThreadId, turn_id: TurnId, execution_key: str, request: dict, result: dict
    ) -> None: ...

    async def claim_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, call: ToolCall
    ) -> ToolResult | None: ...

    async def complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult
    ) -> None: ...

    async def load_turn_tool_outcomes(
        self, thread_id: ThreadId, turn_id: TurnId
    ) -> tuple[ToolResult, ...]:
        """Claim-ordered outcomes, including unknown in-flight calls; never execute them."""
        ...

    async def load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None: ...

    async def load_partial_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> tuple[ConversationItem, ...]: ...

    async def append_partial_item(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int, item: ConversationItem
    ) -> None: ...

    async def commit_model_step(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        step_index: int,
        completed: ModelCompleted,
    ) -> None: ...

    async def close(self) -> None: ...

    async def load_model_failure(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelFailure | None: ...

    async def save_model_failure(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int, failure: ModelFailure
    ) -> None: ...
