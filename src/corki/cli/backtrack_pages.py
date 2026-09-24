"""Bounded display windows over immutable history, without truncating source items."""

from corki.cli.display_text import visible_terminal_text
from corki.cli.history_projection import HistoryProjection
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
)
from corki.protocol.wire_numbers import dumps_wire


def parts(item):
    if isinstance(item, UserMessageItem):
        suffix = f"\n[{len(item.attachments)} image attachment(s)]" if item.attachments else ""
        return "› ", item.content, suffix, "\n\n"
    if isinstance(item, AssistantMessageItem):
        return "• ", item.content, "\n\n"
    if isinstance(item, ReasoningItem):
        return ("Thinking: ", item.summary, "\n\n") if item.summary else ()
    if isinstance(item, ToolCallItem):
        return (
            "• ",
            item.call.name,
            " ",
            item.call.raw_arguments or dumps_wire(item.call.arguments),
            "\n\n",
        )
    if isinstance(item, ToolResultItem):
        failed = item.is_error or item.exit_code not in (None, 0)
        return (
            item.display_content if item.display_content is not None else item.content,
            "\n[Tool failed]" if failed else "",
            "\n\n",
        )
    return ("Turn interrupted.\n\n",) if isinstance(item, TurnAbortedItem) else ()


class BacktrackPages:
    def __init__(self, history, prompts, page_chars=32768):
        if page_chars < 1:
            raise ValueError("Invalid display window")
        self.page_chars = page_chars
        self.items = tuple(HistoryProjection().feed(getattr(history, "items", history)))
        positions = {item.id: index for index, item in enumerate(self.items)}
        try:
            self.prompts = tuple(positions[prompt.id] for prompt in prompts)
        except KeyError:
            raise ValueError("Selected prompts do not match visible history") from None

    def page(self, cursor):
        index, offset = cursor
        remaining = self.page_chars
        chunks, ranges, size = [], {}, 0
        while index < len(self.items) and remaining:
            source_parts = parts(self.items[index])
            length = sum(map(len, source_parts))
            take = min(remaining, length - offset)
            skip, needed = offset, take
            start = size
            for part in source_parts:
                if skip >= len(part):
                    skip -= len(part)
                    continue
                amount = min(needed, len(part) - skip)
                text = visible_terminal_text(part[skip : skip + amount])
                chunks.append(text)
                size += len(text)
                needed -= amount
                skip = 0
                if not needed:
                    break
            if take:
                ranges[index] = start, size
            remaining -= take
            offset += take
            if offset == length:
                index, offset = index + 1, 0
        next_cursor = (index, offset) if index < len(self.items) else None
        return "".join(chunks), ranges, next_cursor

    def previous(self, cursor):
        index, offset = cursor
        remaining = self.page_chars
        while remaining:
            amount = min(offset, remaining)
            offset -= amount
            remaining -= amount
            if not remaining or not index:
                break
            index -= 1
            offset = sum(map(len, parts(self.items[index])))
        return index, offset
