"""Serializable facts passed between LangGraph nodes."""

from __future__ import annotations

from typing import TypedDict

from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ConversationItem, UserMessageItem
from corki.protocol.tools import ToolSpec


class CorkiState(TypedDict):
    """State for one user turn, distinct from long-lived runtime services."""

    thread_id: ThreadId
    turn_id: TurnId
    cwd: str
    user_input: str
    request_items: tuple[ConversationItem, ...]
    request_tools: tuple[ToolSpec, ...]
    dispatch_tools: tuple[ToolSpec, ...]
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
