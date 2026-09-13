"""Identity and completion of individual Responses message/reasoning items."""

from dataclasses import dataclass, field, replace
from typing import Any

from corki.models.base import ModelError
from corki.models.item_metadata import capture_response_identity
from corki.models.types import ModelReasoningDelta, ModelTextDelta
from corki.protocol.ids import ItemId, ModelStepId, TurnId, new_item_id
from corki.protocol.items import AssistantMessageItem, ReasoningItem
from corki.protocol.response_body import capture_response_body


@dataclass
class _Content:
    kind: str
    id: ItemId = field(default_factory=new_item_id)
    parts: dict[tuple[str, int], str] = field(default_factory=dict)
    completed: AssistantMessageItem | ReasoningItem | None = None
    provider_id: str | None = None

    def text(self) -> str:
        channels = {key[0] for key in self.parts}
        channel = "summary" if "summary" in channels else "text"
        return "".join(value for key, value in sorted(self.parts.items()) if key[0] == channel)


class ResponseContent:
    def __init__(self, turn_id: TurnId, step_id: ModelStepId, provider: str) -> None:
        self.turn_id, self.step_id, self.provider = turn_id, step_id, provider
        self._items: dict[str, _Content] = {}
        self._aliases: dict[str, str] = {}
        self.extra_chars = 0

    def _key(self, item: dict[str, Any], event: dict[str, Any], kind: str) -> str:
        identity = item.get("id") or event.get("item_id")
        index = event.get("output_index")
        index_key = f"index:{index}" if index is not None else None
        if identity:
            key = str(identity)
            if index_key is not None:
                old = self._aliases.setdefault(index_key, key)
                if old != key:
                    raise ModelError("Responses output index changed item identity")
                if index_key in self._items and key not in self._items:
                    self._items[key] = self._items.pop(index_key)
        else:
            key = self._aliases.get(index_key, index_key) if index_key else f"anonymous:{kind}"
        anonymous = f"anonymous:{kind}"
        if key not in self._items and anonymous in self._items:
            self._items[key] = self._items.pop(anonymous)
        return key

    def added(self, item: dict[str, Any], event: dict[str, Any]) -> None:
        kind = item["type"]
        key = self._key(item, event, kind)
        buffer = self._items.setdefault(key, _Content(kind))
        buffer.provider_id = item.get("id") or buffer.provider_id

    def delta(self, event: dict[str, Any], *, reasoning: bool):
        kind = "reasoning" if reasoning else "message"
        key = self._key({}, event, kind)
        buffer = self._items.setdefault(key, _Content(kind))
        buffer.provider_id = event.get("item_id") or buffer.provider_id
        if buffer.kind != kind or buffer.completed is not None:
            raise ModelError("Responses delta conflicts with a completed item")
        delta = event.get("delta") or ""
        if not isinstance(delta, str):
            raise ModelError("Responses text delta is not a string")
        summary = event["type"] == "response.reasoning_summary_text.delta"
        index = event.get("summary_index" if summary else "content_index", 0)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ModelError("Responses content index is invalid")
        part = ("summary" if summary else "text", index)
        buffer.parts[part] = buffer.parts.get(part, "") + delta
        self.extra_chars += len(delta)
        if reasoning:
            return ModelReasoningDelta(
                delta,
                item_id=buffer.id,
                section_index=index,
                channel="summary" if summary else "raw",
            )
        return ModelTextDelta(delta, item_id=buffer.id)

    def complete(self, item: dict[str, Any], event: dict[str, Any], *, synthetic: bool = False):
        kind = item["type"]
        key = self._key(item, event, kind)
        buffer = self._items.setdefault(key, _Content(kind))
        if not synthetic:
            buffer.provider_id = item.get("id") or buffer.provider_id
        if buffer.kind != kind:
            raise ModelError("Responses item type changed")
        field_name = "summary" if kind == "reasoning" else "content"
        parts = item.get(field_name)
        if parts is not None and not isinstance(parts, list):
            raise ModelError(f"Responses {field_name} is not a list")
        texts = []
        for part in parts or []:
            if not isinstance(part, dict):
                raise ModelError("Responses content part is not an object")
            if part.get("type") in {"summary_text", "output_text", "text"}:
                text = part.get("text", "")
                if not isinstance(text, str):
                    raise ModelError("Responses completed content is not a string")
                texts.append(text)
        text = "".join(texts) if parts is not None else buffer.text()
        streamed = buffer.text()
        # Full items may contain a missing tail, but cannot rewrite text already
        # emitted to the user. Raw reasoning and its summary are distinct views.
        compare = kind == "message" or any(key[0] == "summary" for key in buffer.parts)
        if compare and not text.startswith(streamed):
            raise ModelError("Responses completed item changed streamed text")
        if kind == "message":
            phase = item.get("phase")
            if phase not in (None, "commentary", "final_answer"):
                raise ModelError("Responses assistant phase is invalid")
            completed = AssistantMessageItem(
                text,
                self.turn_id,
                self.step_id,
                id=buffer.id,
                phase=phase,
                response_item_metadata_json=None if synthetic else capture_response_identity(item),
                response_body_json=None if synthetic else capture_response_body(item),
            )
        else:
            encrypted = item.get("encrypted_content")
            if encrypted is not None and not isinstance(encrypted, str):
                raise ModelError("Responses encrypted reasoning is not a string")
            completed = ReasoningItem(
                text,
                self.turn_id,
                self.step_id,
                id=buffer.id,
                provider_name=self.provider,
                summary=text
                if parts is not None or any(key[0] == "summary" for key in buffer.parts)
                else None,
                provider_item_id=buffer.provider_id,
                encrypted_content=encrypted,
                response_item_metadata_json=None if synthetic else capture_response_identity(item),
                response_body_json=None if synthetic else capture_response_body(item),
            )
        if buffer.completed is not None:
            if replace(completed, created_at=buffer.completed.created_at) != buffer.completed:
                raise ModelError("Responses completed content item changed")
            return None
        self.extra_chars += max(0, len(text) - sum(map(len, buffer.parts.values())))
        self.extra_chars += len(completed.response_item_metadata_json or "")
        # Display text was counted above; retain bounds on all remaining body data
        # (including raw reasoning, media, empty part wrappers and hidden markup).
        self.extra_chars += max(0, len(completed.response_body_json or "") - len(text))
        if isinstance(completed, ReasoningItem):
            self.extra_chars += len(completed.encrypted_content or "")
        buffer.completed = completed
        return completed

    def finish_pending(self):
        """Compatibility for providers with deltas but no item.done/output list."""
        for key, buffer in list(self._items.items()):
            if buffer.completed is None and buffer.parts:
                item = {"type": buffer.kind, "id": key}
                yield self.complete(item, {}, synthetic=True)
