"""Response-owned plan display projection; canonical assistant items stay intact."""

from dataclasses import dataclass, field

from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    ProposedPlanCompleted,
    ProposedPlanDelta,
)
from corki.protocol.memory import MemoryCitationStreamFilter
from corki.protocol.proposed_plan import ProposedPlanParser, split_proposed_plan


@dataclass
class _Stream:
    citations: MemoryCitationStreamFilter = field(default_factory=MemoryCitationStreamFilter)
    parser: ProposedPlanParser = field(default_factory=ProposedPlanParser)
    source: str = ""
    leading: str = ""
    visible_started: bool = False


class PlanOutput:
    def __init__(self, thread, turn, emit):
        self.thread, self.turn, self.emit = thread, turn, emit
        self.streams = {}
        self.plan_completed = False

    async def _segments(self, stream, item_id, segments):
        for segment in segments:
            if segment.kind == "normal":
                text = segment.text
                if not stream.visible_started:
                    stream.leading += text
                    if not stream.leading.strip():
                        continue
                    text, stream.leading = stream.leading, ""
                    stream.visible_started = True
                await self.emit(AssistantTextDelta(self.thread, self.turn, text, item_id))
            elif segment.kind in {"start", "delta"} and not self.plan_completed:
                await self.emit(ProposedPlanDelta(self.thread, self.turn, segment.text, item_id))

    async def delta(self, event):
        stream = self.streams.setdefault(event.item_id, _Stream())
        text = stream.citations.push(event.delta)
        stream.source += text
        await self._segments(stream, event.item_id, stream.parser.push(text))

    async def complete(self, item):
        stream = self.streams.pop(item.id, None)
        unkeyed = stream is None
        if stream is None:
            stream = self.streams.pop(None, _Stream())
        tail = stream.citations.finish()
        stream.source += tail
        await self._segments(stream, item.id, stream.parser.push(tail))
        if item.content.startswith(stream.source):
            suffix = item.content[len(stream.source) :]
            await self._segments(stream, item.id, stream.parser.push(suffix))
        await self._segments(stream, item.id, stream.parser.finish())
        if unkeyed and stream.source.startswith(item.content):
            remaining = stream.source[len(item.content) :]
            if remaining:
                carry = _Stream(source=remaining, visible_started=stream.visible_started)
                carry.parser.push(remaining)
                self.streams[None] = carry
        visible, plan = split_proposed_plan(item.content)
        if plan is not None and not self.plan_completed:
            self.plan_completed = True
            await self.emit(ProposedPlanCompleted(self.thread, self.turn, plan, item.id))
        if visible.strip():
            await self.emit(AssistantMessageCompleted(self.thread, self.turn, visible, item.id))
