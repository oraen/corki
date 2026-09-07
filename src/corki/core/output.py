"""Per-message visible output; response completion is not a message boundary."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from corki.models import ModelTextDelta
from corki.protocol.events import AssistantMessageCompleted, AssistantTextDelta, RuntimeEvent
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import AssistantMessageItem
from corki.protocol.memory import MemoryCitationStreamFilter


@dataclass
class _MessageStream:
    filter: MemoryCitationStreamFilter = field(default_factory=MemoryCitationStreamFilter)
    visible: str = ""


class ModelOutput:
    def __init__(
        self, thread: ThreadId, turn: TurnId, emit: Callable[[RuntimeEvent], Awaitable[None]]
    ) -> None:
        self.thread, self.turn, self.emit = thread, turn, emit
        self._streams: dict[str | None, _MessageStream] = {}
        self._completed: set[str] = set()

    async def delta(self, event: ModelTextDelta) -> None:
        stream = self._streams.setdefault(event.item_id, _MessageStream())
        visible = stream.filter.push(event.delta)
        if visible:
            stream.visible += visible
            await self.emit(AssistantTextDelta(self.thread, self.turn, visible))

    async def complete(self, item: AssistantMessageItem) -> None:
        if item.id in self._completed:
            return
        stream = self._streams.pop(item.id, None)
        unkeyed = stream is None
        if stream is None:
            stream = self._streams.pop(None, _MessageStream())
        tail = stream.filter.finish(citation_valid=item.memory_citation is not None)
        if tail:
            stream.visible += tail
            await self.emit(AssistantTextDelta(self.thread, self.turn, tail))
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
            await self.emit(AssistantTextDelta(self.thread, self.turn, suffix))
        if item.content:
            await self.emit(AssistantMessageCompleted(self.thread, self.turn, item.content))
        self._completed.add(item.id)
