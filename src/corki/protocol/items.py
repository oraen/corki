"""Provider-neutral, append-only conversation items.

The harness stores semantic items rather than Chat Completions messages.  A
provider adapter may group several items into one wire message, while a
Responses-style adapter can transmit them independently.  This keeps provider
wire formats out of persistence, context management, and LangGraph state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeAlias

from corki.protocol.ids import (
    ItemId,
    ModelStepId,
    ThreadId,
    ToolCallId,
    TurnId,
    new_item_id,
    new_model_step_id,
)
from corki.protocol.memory import MemoryCitation, MemoryCitationEntry
from corki.protocol.tools import (
    ImageAttachment,
    ToolCall,
    ToolSpec,
    ToolStateUpdate,
    tool_spec_from_payload,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ContextRole(StrEnum):
    DEVELOPER = "developer"
    USER = "user"


@dataclass(frozen=True, slots=True)
class UserMessageItem:
    content: str
    turn_id: TurnId
    id: ItemId = field(default_factory=new_item_id)
    attachments: tuple[ImageAttachment, ...] = ()
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class AssistantMessageItem:
    content: str
    turn_id: TurnId
    step_id: ModelStepId
    id: ItemId = field(default_factory=new_item_id)
    created_at: str = field(default_factory=_now)
    memory_citation: MemoryCitation | None = None


@dataclass(frozen=True, slots=True)
class ReasoningItem:
    """Opaque provider reasoning and an optional user-safe summary."""

    content: str
    turn_id: TurnId
    step_id: ModelStepId
    id: ItemId = field(default_factory=new_item_id)
    summary: str | None = None
    provider_name: str | None = None
    provider_item_id: str | None = None
    encrypted_content: str | None = None
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class ToolCallItem:
    call: ToolCall
    turn_id: TurnId
    step_id: ModelStepId
    id: ItemId = field(default_factory=new_item_id)
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class ToolResultItem:
    call_id: ToolCallId
    tool_name: str
    content: str
    turn_id: TurnId
    id: ItemId = field(default_factory=new_item_id)
    is_error: bool = False
    display_content: str | None = None
    attachments: tuple[ImageAttachment, ...] = ()
    state_update: ToolStateUpdate = field(default_factory=ToolStateUpdate)
    created_at: str = field(default_factory=_now)
    discovered_tools: tuple[ToolSpec, ...] = ()

    def __post_init__(self) -> None:
        # MsgPack checkpoints decode sequences as lists. Restore the canonical
        # contract at construction, not only in SQLite's JSON adapter.
        object.__setattr__(self, "discovered_tools", tuple(self.discovered_tools))


@dataclass(frozen=True, slots=True)
class ContextItem:
    key: str
    role: ContextRole
    content: str
    turn_id: TurnId
    id: ItemId = field(default_factory=new_item_id)
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True, slots=True)
class CompactionItem:
    """Model-visible checkpoint replacing history through ``through_item_id``."""

    summary: str
    through_item_id: ItemId
    turn_id: TurnId
    id: ItemId = field(default_factory=new_item_id)
    # Replacement items are persisted immediately after the marker because
    # storage is append-only. These fields reconstruct their model-visible
    # order, where Codex may place the summary between retained user messages
    # and newly injected context instead of physically first.
    replacement_item_count: int = 0
    summary_insert_index: int = 0
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.replacement_item_count < 0:
            raise ValueError("replacement_item_count must not be negative")
        if not 0 <= self.summary_insert_index <= self.replacement_item_count:
            raise ValueError("summary_insert_index must be inside replacement history")


ConversationItem: TypeAlias = (
    UserMessageItem
    | AssistantMessageItem
    | ReasoningItem
    | ToolCallItem
    | ToolResultItem
    | ContextItem
    | CompactionItem
)


def item_kind(item: ConversationItem) -> str:
    return {
        UserMessageItem: "user_message",
        AssistantMessageItem: "assistant_message",
        ReasoningItem: "reasoning",
        ToolCallItem: "tool_call",
        ToolResultItem: "tool_result",
        ContextItem: "context",
        CompactionItem: "compaction",
    }[type(item)]


def item_to_payload(item: ConversationItem) -> dict[str, Any]:
    payload = asdict(item)
    if isinstance(item, ToolResultItem) and not item.discovered_tools:
        # Preserve old idempotent payloads, not merely old constructor defaults.
        payload.pop("discovered_tools")
    if isinstance(item, ToolCallItem):
        payload["call"] = {
            "id": str(item.call.id),
            "name": item.call.name,
            "arguments": dict(item.call.arguments) if item.call.arguments is not None else None,
            "raw_arguments": item.call.raw_arguments,
            "parse_error": item.call.parse_error,
        }
    if isinstance(item, ContextItem):
        payload["role"] = item.role.value
    return payload


def item_from_payload(kind: str, payload: dict[str, Any]) -> ConversationItem:
    payload = dict(payload)
    payload["id"] = ItemId(payload["id"])
    payload["turn_id"] = TurnId(payload["turn_id"])
    attachments = payload.get("attachments")
    if attachments is not None:
        payload["attachments"] = tuple(ImageAttachment(**value) for value in attachments)
    if kind in {"assistant_message", "reasoning", "tool_call"}:
        payload["step_id"] = ModelStepId(payload["step_id"])
    if kind == "user_message":
        return UserMessageItem(**payload)
    if kind == "assistant_message":
        raw_citation = payload.get("memory_citation")
        if raw_citation is not None:
            payload["memory_citation"] = MemoryCitation(
                entries=tuple(
                    MemoryCitationEntry(**entry) for entry in raw_citation.get("entries", ())
                ),
                thread_ids=tuple(ThreadId(value) for value in raw_citation.get("thread_ids", ())),
            )
        return AssistantMessageItem(**payload)
    if kind == "reasoning":
        return ReasoningItem(**payload)
    if kind == "tool_call":
        raw_call = payload.pop("call")
        payload["call"] = ToolCall(
            id=ToolCallId(raw_call["id"]),
            name=raw_call["name"],
            arguments=raw_call["arguments"],
            raw_arguments=raw_call.get("raw_arguments", ""),
            parse_error=raw_call.get("parse_error"),
        )
        return ToolCallItem(**payload)
    if kind == "tool_result":
        payload["call_id"] = ToolCallId(payload["call_id"])
        payload["discovered_tools"] = tuple(
            tool_spec_from_payload(value) for value in payload.get("discovered_tools", ())
        )
        state_update = payload.get("state_update") or {}
        plan = state_update.get("plan")
        payload["state_update"] = ToolStateUpdate(
            plan=tuple(dict(value) for value in plan) if plan is not None else None
        )
        return ToolResultItem(**payload)
    if kind == "context":
        payload["role"] = ContextRole(payload["role"])
        return ContextItem(**payload)
    if kind == "compaction":
        payload["through_item_id"] = ItemId(payload["through_item_id"])
        return CompactionItem(**payload)
    raise ValueError(f"unknown conversation item kind: {kind}")


def new_step_id() -> ModelStepId:
    """Create one association key for all items from a model response."""

    return new_model_step_id()


def items_from_messages(messages: tuple[object, ...]) -> tuple[ConversationItem, ...]:
    """Migrate legacy ``Message`` rows into canonical items."""

    from corki.protocol.messages import Message, MessageRole

    items: list[ConversationItem] = []
    for value in messages:
        if not isinstance(value, Message):
            raise TypeError(f"expected Message, got {type(value).__name__}")
        item_id = ItemId(str(value.id))
        if value.role is MessageRole.USER:
            items.append(
                UserMessageItem(
                    value.content,
                    value.turn_id,
                    id=item_id,
                    attachments=value.attachments,
                    created_at=value.created_at,
                )
            )
        elif value.role is MessageRole.DEVELOPER:
            items.append(
                ContextItem(
                    "legacy.developer",
                    ContextRole.DEVELOPER,
                    value.content,
                    value.turn_id,
                    id=item_id,
                    created_at=value.created_at,
                )
            )
        elif value.role is MessageRole.TOOL:
            if value.tool_call_id is None:
                continue
            items.append(
                ToolResultItem(
                    value.tool_call_id,
                    value.name or "unknown",
                    value.content,
                    value.turn_id,
                    id=item_id,
                    attachments=value.attachments,
                    created_at=value.created_at,
                )
            )
        else:
            step_id = ModelStepId(str(value.id))
            if value.reasoning_content:
                items.append(
                    ReasoningItem(
                        value.reasoning_content,
                        value.turn_id,
                        step_id,
                        created_at=value.created_at,
                    )
                )
            if value.content:
                items.append(
                    AssistantMessageItem(
                        value.content,
                        value.turn_id,
                        step_id,
                        id=item_id,
                        created_at=value.created_at,
                    )
                )
            items.extend(
                ToolCallItem(call, value.turn_id, step_id, created_at=value.created_at)
                for call in value.tool_calls
            )
    return tuple(items)
