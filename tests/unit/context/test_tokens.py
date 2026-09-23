import base64
import io

import pytest
from PIL import Image

from corki.context.tokens import estimate_item_tokens, estimate_text_tokens
from corki.media.images import ImagePolicy, ImagePreparation
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


def test_original_jpeg_with_long_metadata_uses_decoded_dimensions() -> None:
    stream = io.BytesIO()
    with Image.new("RGB", (2_048, 2_048), (10, 20, 30)) as image:
        image.save(stream, format="JPEG")
    raw = stream.getvalue()
    app_segment = b"\xff\xe1" + (60_002).to_bytes(2, "big") + b"x" * 60_000
    raw = raw[:2] + app_segment * 9 + raw[2:]
    url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    attachment = ImageAttachment(url, "original")
    prepared = ImagePreparation(ImagePolicy(supports_original=True, unified_budget=True))
    assert prepared.prepare_image(attachment) == attachment
    item = UserMessageItem("image", new_turn_id(), attachments=(attachment,))

    assert estimate_item_tokens(item) >= 4_096


@pytest.mark.parametrize(
    "url",
    [
        "fixture:small-image",
        "https://example.invalid/image.png",
        "data:image/png;notbase64,AAAA",
    ],
)
def test_non_data_image_reference_counts_visible_url_not_inline_payload(url) -> None:
    item = UserMessageItem(
        "image",
        new_turn_id(),
        attachments=(ImageAttachment(url, "high"),),
    )

    assert estimate_item_tokens(item) < 100
