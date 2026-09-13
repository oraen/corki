"""Filter canonical history in forward batches without exposing compaction copies."""

from collections.abc import Iterable

from corki.protocol.items import CompactionItem, ConversationItem, UserMessageItem


class HistoryProjection:
    """Own the replacement-span boundary of one chronological display scan."""

    def __init__(self, remaining_replacements: int = 0):
        if type(remaining_replacements) is not int or remaining_replacements < 0:
            raise ValueError("remaining replacements must be a nonnegative integer")
        self.remaining_replacements = remaining_replacements

    def feed(self, items: Iterable[ConversationItem]) -> tuple[ConversationItem, ...]:
        # Consume eagerly: partially consumed generators must not reorder state
        # transitions when the next batch arrives.
        visible = []
        for item in items:
            if self.remaining_replacements:
                self.remaining_replacements -= 1
                continue
            if isinstance(item, CompactionItem):
                self.remaining_replacements = item.replacement_item_count
                continue
            if isinstance(item, UserMessageItem) and item.retained_from_id is not None:
                continue
            visible.append(item)
        return tuple(visible)
