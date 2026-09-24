"""Original user-message indexing matches storage.forks, excluding retained copies."""

from corki.cli.draft_history import DraftEntry
from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import ImageInput
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ImageAttachment
from corki.sessions.models import TurnStatus


class BacktrackUnavailable(ValueError):
    """A fixed, safe-to-display reason why a prompt cannot be forked."""


def validate_selection(history, index, selected):
    prompts = editable_prompts(history)
    if type(index) is not int or not 0 <= index < len(prompts) or prompts[index] != selected:
        raise BacktrackUnavailable("The selected prompt no longer matches the saved conversation.")
    if any(item.turn_id == selected.turn_id for item in prompts[:index]):
        raise BacktrackUnavailable(
            "The selected prompt is a steer and cannot be branched independently."
        )
    turn = next(
        (turn for turn in getattr(history, "turns", ()) if turn.id == selected.turn_id), None
    )
    if turn is None:
        raise BacktrackUnavailable("The selected prompt has no saved turn status.")
    if turn.status in {TurnStatus.CREATED, TurnStatus.RUNNING}:
        raise BacktrackUnavailable("The selected prompt belongs to a turn still in progress.")


def editable_prompts(history):
    items = getattr(history, "items", history)
    return tuple(
        item
        for item in items
        if isinstance(item, UserMessageItem) and item.retained_from_id is None
    )


def _mention_ranges(text, token, start):
    """Codex find_next_mention_token_range boundaries; offsets are Unicode here."""

    def name_char(char):
        return char.isascii() and (char.isalnum() or char in "_-")

    while (index := text.find(token, start)) >= 0:
        end = index + len(token)
        following = text[end : end + 1]
        valid = not following or not name_char(following)
        if token.startswith("@"):
            valid &= not index or not name_char(text[index - 1])
            if following in {"/", "\\"}:
                valid = False
            elif following == ".":
                after_dot = text[end + 1 : end + 2]
                valid &= not after_dot or not name_char(after_dot)
        if valid:
            yield index, end
        start = end


def prompt_input(item):
    images = item.attachments or tuple(
        part for part in item.content_items if isinstance(part, ImageAttachment)
    )
    if not images and not item.mentions:
        return item.content
    draft = ImageDraft()
    draft.restore(item.content, images, item.image_positions)
    scan_from = 0
    for selector in item.mentions:
        sigil = "$" if selector.kind == "skill" else "@"
        label = sigil + selector.name
        # Rebind only an existing marker backed by durable host metadata. A
        # literal marker in text without such metadata is never a selector.
        for start, end in _mention_ranges(draft.text, label, scan_from):
            if not any(e.start < end and start < e.end for e in draft.elements):
                draft.bind(start, label, selector)
                scan_from = end
                break
    return ImageInput(
        item.content, images, item.image_positions, DraftEntry.capture(draft), item.mentions
    )
