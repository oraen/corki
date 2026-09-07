"""Serializable facts passed between LangGraph nodes."""

from __future__ import annotations

from typing import NotRequired, TypedDict

from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ConversationItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolSpec


class CorkiState(TypedDict):
    """State for one user turn, distinct from long-lived runtime services."""

    thread_id: ThreadId
    operation: NotRequired[str]
    turn_id: TurnId
    cwd: str
    user_input: str
    request_items: tuple[ConversationItem, ...]
    request_tools: tuple[ToolSpec, ...]
    dispatch_tools: tuple[ToolSpec, ...]
    tool_snapshot_id: NotRequired[str]
    context_instructions: str
    pending_input_items: tuple[UserMessageItem, ...]
    last_model_items: tuple[ConversationItem, ...]
    model_end_turn: bool | None
    plan: tuple[dict[str, str], ...]
    step_count: int
    tool_call_count: int
    route: str
    status: str
    final_answer: str | None
    error: str | None
    realtime_active: bool
    model_interrupted: bool
    streamed_tool_results: tuple[ToolResultItem, ...]
    sample_count: int
    model_retry_count: int
    model_retry_pending: bool
    model_retry_delay: float
    model_retry_error: str
    model_connection_retry_count: int
    model_retry_attempt: int
    model_retry_limit: int | None
