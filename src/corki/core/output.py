"""Per-message visible output; response completion is not a message boundary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from corki.core.plan_output import PlanOutput
from corki.models import ModelReasoningDelta, ModelTextDelta
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantReasoningCompleted,
    AssistantReasoningDelta,
    AssistantTextDelta,
    RuntimeEvent,
)
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import AssistantMessageItem, ReasoningItem
from corki.protocol.memory import MemoryCitationStreamFilter


@dataclass
class _MessageStream:
    filter: MemoryCitationStreamFilter = field(default_factory=MemoryCitationStreamFilter)
    visible: str = ""


class ModelOutput:
    def __init__(
        self,
        thread: ThreadId,
        turn: TurnId,
        emit: Callable[[RuntimeEvent], Awaitable[None]],
        *,
        plan_mode: bool = False,
    ) -> None:
        self.thread, self.turn, self.emit = thread, turn, emit
        self._streams: dict[str | None, _MessageStream] = {}
        self._completed: set[str] = set()
        self._reasoning: dict[str | None, tuple[str, int | None]] = {}
        self._plan = PlanOutput(thread, turn, emit) if plan_mode else None

    async def reasoning_delta(self, event: ModelReasoningDelta) -> None:
        if event.channel == "summary":
            previous, _ = self._reasoning.get(event.item_id, ("", None))
            self._reasoning[event.item_id] = (previous + event.delta, event.section_index)
        await self.emit(
            AssistantReasoningDelta(
                self.thread,
                self.turn,
                event.delta,
                event.item_id,
                event.section_index,
                channel=event.channel,
            )
        )

    async def complete_reasoning(self, item: ReasoningItem) -> None:
        if item.id in self._completed:
            return
        stream = self._reasoning.pop(item.id, None)
        display_id = item.id
        if stream is None:
            stream = self._reasoning.pop(None, ("", None))
            if stream[0]:
                display_id = None
        text, section = stream
        if item.summary and item.summary.startswith(text):
            suffix = item.summary[len(text) :]
            if suffix:
                await self.emit(
                    AssistantReasoningDelta(self.thread, self.turn, suffix, display_id, section)
                )
        await self.emit(AssistantReasoningCompleted(self.thread, self.turn, item.id))
        self._completed.add(item.id)

    async def delta(self, event: ModelTextDelta) -> None:
        if self._plan is not None:
            await self._plan.delta(event)
            return
        stream = self._streams.setdefault(event.item_id, _MessageStream())
        visible = stream.filter.push(event.delta)
        if visible:
            stream.visible += visible
            await self.emit(AssistantTextDelta(self.thread, self.turn, visible, event.item_id))

    async def complete(self, item: AssistantMessageItem) -> None:
        if item.id in self._completed:
            return
        if self._plan is not None:
            await self._plan.complete(item)
            self._completed.add(item.id)
            return
        stream = self._streams.pop(item.id, None)
        unkeyed = stream is None
        if stream is None:
            stream = self._streams.pop(None, _MessageStream())
        tail = stream.filter.finish()
        if tail:
            stream.visible += tail
            await self.emit(AssistantTextDelta(self.thread, self.turn, tail, item.id))
        if unkeyed and stream.visible.startswith(item.content):
            remaining = stream.visible[len(item.content) :]
            if remaining:
                # Legacy ModelPorts can stream several completed messages as
                # one unkeyed delta sequence. Consume only this item's prefix.
                self._streams[None] = _MessageStream(visible=remaining)
        suffix = (
            item.content[len(stream.visible) :] if item.content.startswith(stream.visible) else ""
        )
        if suffix:
            await self.emit(AssistantTextDelta(self.thread, self.turn, suffix, item.id))
        if item.content:
            await self.emit(
                AssistantMessageCompleted(self.thread, self.turn, item.content, item.id)
            )
        self._completed.add(item.id)
