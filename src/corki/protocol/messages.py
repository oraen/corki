"""Canonical conversation messages independent of any model SDK."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from corki.protocol.ids import MessageId, ToolCallId, TurnId, new_message_id
from corki.protocol.tools import ImageAttachment, ToolCall


class MessageRole(StrEnum):
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """A replayable message stored in Corki's thread history."""

    role: MessageRole
    content: str
    id: MessageId
    turn_id: TurnId
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: ToolCallId | None = None
    name: str | None = None
    attachments: tuple[ImageAttachment, ...] = ()
    reasoning_content: str | None = None
    created_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        role: MessageRole,
        content: str,
        turn_id: TurnId,
        tool_calls: tuple[ToolCall, ...] = (),
        tool_call_id: ToolCallId | None = None,
        name: str | None = None,
        attachments: tuple[ImageAttachment, ...] = (),
        reasoning_content: str | None = None,
    ) -> Message:
        return cls(
            role=role,
            content=content,
            id=new_message_id(),
            turn_id=turn_id,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            name=name,
            attachments=attachments,
            reasoning_content=reasoning_content,
            created_at=datetime.now(UTC).isoformat(),
        )
