"""Opaque identifiers shared by graph, persistence, tools, and UI."""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4, uuid5

ThreadId = NewType("ThreadId", str)
SessionId = NewType("SessionId", str)
TurnId = NewType("TurnId", str)
MessageId = NewType("MessageId", str)
ItemId = NewType("ItemId", str)
ModelStepId = NewType("ModelStepId", str)
ToolCallId = NewType("ToolCallId", str)

_TOOL_RESULT_NAMESPACE = UUID("9683cb9e-0f07-47ba-8ea4-91d94a1bd489")


def new_thread_id() -> ThreadId:
    return ThreadId(str(uuid4()))


def new_session_id() -> SessionId:
    """Allocate independent host session identity for an internal task."""
    return SessionId(str(uuid4()))


def new_turn_id() -> TurnId:
    return TurnId(str(uuid4()))


def new_message_id() -> MessageId:
    return MessageId(str(uuid4()))


def new_item_id() -> ItemId:
    return ItemId(str(uuid4()))


def new_model_step_id() -> ModelStepId:
    return ModelStepId(str(uuid4()))


def new_tool_call_id() -> ToolCallId:
    return ToolCallId(str(uuid4()))


def stable_tool_result_item_id(
    thread_id: ThreadId,
    turn_id: TurnId,
    call_id: ToolCallId,
) -> ItemId:
    """Return the same item id whenever one durable tool call is replayed."""

    return ItemId(uuid5(_TOOL_RESULT_NAMESPACE, f"{thread_id}:{turn_id}:{call_id}").hex)
