import base64

from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ImageAttachment


def _png_data_url(width: int, height: int) -> str:
    header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    header += width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return "data:image/png;base64," + base64.b64encode(header).decode()


def test_text_estimator_is_code_efficient_and_unicode_conservative() -> None:
    assert estimate_text_tokens("a" * 400) == 100
    assert estimate_text_tokens("你" * 100) == 150


def test_image_estimator_counts_resized_and_original_inputs() -> None:
    turn_id = new_turn_id()
    resized = UserMessageItem(
        "image",
        turn_id,
        attachments=(ImageAttachment(_png_data_url(1, 1), "high"),),
    )
    original = UserMessageItem(
        "image",
        turn_id,
        attachments=(ImageAttachment(_png_data_url(6_400, 6_400), "original"),),
    )

    assert estimate_item_tokens(resized) >= 1_844
    assert estimate_item_tokens(original) >= 10_000
