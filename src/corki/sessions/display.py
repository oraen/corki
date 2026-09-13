"""Local history item page boundaries; independent of the model context window."""

from dataclasses import dataclass

from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import ConversationItem
from corki.sessions.models import DisplayTurn


@dataclass(frozen=True, slots=True)
class DisplayItemsCursor:
    thread_id: ThreadId
    before_sequence: int

    def __post_init__(self):
        if type(self.before_sequence) is not int or not 0 <= self.before_sequence < 2**63:
            raise ValueError("display cursor sequence must be a nonnegative SQLite integer")


@dataclass(frozen=True, slots=True)
class DisplayItemsPage:
    """A chronological raw batch, not a complete Turn or a terminal-state snapshot."""

    items: tuple[ConversationItem, ...]
    next_cursor: DisplayItemsCursor | None
    leading_replacements: int = 0
    newest_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class DisplayTurnsCursor:
    thread_id: ThreadId
    before_turn_id: TurnId


@dataclass(frozen=True, slots=True)
class DisplayTurnsPage:
    """Chronological Turn facts, including Turns without any conversation items."""

    turns: tuple[DisplayTurn, ...]
    next_cursor: DisplayTurnsCursor | None
