"""Non-V2 interrupted-turn guidance from the pinned Codex reference."""

from corki.protocol.ids import ItemId, TurnId
from corki.protocol.items import TurnAbortedItem

_GUIDANCE = (
    "<turn_aborted>\n"
    "The user interrupted the previous turn on purpose. Any running unified exec processes "
    "may still be running in the background. If any tools/commands were aborted, they may "
    "have partially executed.\n"
    "</turn_aborted>"
)


def interrupted_turn_item(turn_id: TurnId) -> TurnAbortedItem:
    return TurnAbortedItem(_GUIDANCE, turn_id, id=ItemId(f"turn-aborted:{turn_id}"))
