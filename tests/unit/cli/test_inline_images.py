from dataclasses import replace

import pytest

from corki.cli.inline_images import ImageDraft
from corki.cli.input_owner import ImageInput, submission_value
from corki.context.local_retention import retained_user_messages
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem, item_from_payload, item_to_payload
from corki.protocol.tools import ImageAttachment, TextContent

A = ImageAttachment("data:image/png;base64,first")
B = ImageAttachment("data:image/png;base64,second")


def draft():
    value = ImageDraft()
    value.clear("甲乙")
    value.attach(A, 1, 1)
    return value


def test_paste_position_and_reverse_insertions_keep_number_identity():
    value = draft()
    assert value.text == "甲[Image #1]乙"
    value.attach(B, 0, 0)
    assert value.text == "[Image #2]甲[Image #1]乙"
    assert value.images == (A, B)
    assert value.positions == (11, 0)
    restored = ImageDraft()
    restored.restore(value.text, value.images, value.positions)
    assert restored.text == value.text and restored.positions == value.positions


@pytest.mark.parametrize("offset", range(10))
def test_deleting_any_part_removes_marker_and_corresponding_image(offset):
    value = draft()
    value.attach(B, len(value.text), len(value.text))
    start = 1 + offset
    cursor = value.sync(value.text[:start] + value.text[start + 1 :], start)
    assert value.text == "甲乙[Image #1]"
    assert value.images == (B,)
    assert 0 <= cursor <= len(value.text)
    value.undo()
    assert value.text == "甲[Image #1]乙[Image #2]"
    assert value.images == (A, B)


def test_plain_marker_text_never_becomes_attachment():
    value = draft()
    value.sync("[Image #1]" + value.text, 10)
    assert value.positions == (11,)
    assert value.images == (A,)
    value.sync(value.text[:11] + value.text[21:], 11)
    assert value.text == "[Image #1]甲乙"
    assert not value.images


def test_whitespace_positions_survive_input_normalization_and_history():
    value = ImageInput("  甲[Image #1]乙\n", (A,), (3,))
    assert submission_value(value) == value
    item = UserMessageItem(
        value.text,
        new_turn_id(),
        attachments=value.attachments,
        image_positions=value.image_positions,
    )
    assert item.content_items == (
        TextContent("<image name=[Image #1]>"),
        A,
        TextContent("</image>"),
        TextContent(value.text),
    )
    restored = item_from_payload("user_message", item_to_payload(item))
    assert restored == item
    prepared = replace(item, attachments=())
    assert prepared.image_positions == (3,)
    retained = retained_user_messages((prepared,), excluded_ids=set(), token_budget=100)
    assert retained[0].content == value.text
    assert not retained[0].image_positions and not retained[0].attachments


def test_combining_drafts_renumbers_only_real_markers():
    value = draft()
    value.append("\n")
    value.append("文字[Image #1]之后", (B,), (2,))
    assert value.text == "甲[Image #1]乙\n文字[Image #2]之后"
    assert value.images == (A, B)


def test_undo_history_is_bounded():
    value = draft()
    for _ in range(100):
        value.sync(value.text + "a", len(value.text) + 1)
    assert len(value.history) == 64


@pytest.mark.parametrize("positions", [(0,), (-1,), (1, 1), (True,)])
def test_bad_positions_rejected(positions):
    with pytest.raises(ValueError):
        ImageInput("甲[Image #1]乙", (A,), positions)
