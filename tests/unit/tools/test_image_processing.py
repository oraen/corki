"""Decoded image policies ported from Codex's image-preparation scenarios."""

import asyncio
import base64
import io
import threading

import pytest
from PIL import Image

from corki.context.window import _retained_user_messages
from corki.media.images import (
    LOW_ERROR,
    PROCESSING_ERROR,
    REMOTE_ERROR,
    ImagePolicy,
    ImagePreparation,
    output_dimensions,
)
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ImageAttachment, TextContent, ToolCall, ToolResult
from corki.tools.base import ToolContext
from corki.tools.builtin.image import ViewImageTool


@pytest.mark.parametrize("budget", [80, 10000])
def test_compacted_past_user_messages_do_not_reinject_media_or_untruncated_parts(budget):
    original = UserMessageItem(
        "raw user text",
        new_turn_id(),
        content_items=(TextContent("observed " * 100), ImageAttachment("data:bad")),
    )
    retained = _retained_user_messages((original,), excluded_ids=set(), token_budget=budget)
    assert len(retained) == 1
    assert not retained[0].attachments and not retained[0].content_items
    assert retained[0].content.startswith("observed ")
    assert (
        len(retained[0].content) < 800 if budget == 80 else retained[0].content == "observed " * 100
    )
    assert original.content_items[0].text == "observed " * 100


def encoded_image(size=(64, 32), format="PNG", **options):
    stream = io.BytesIO()
    with Image.new("RGB", size, (10, 20, 30)) as image:
        image.save(stream, format=format, **options)
    return stream.getvalue()


def data_url(raw, mime="application/octet-stream"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


@pytest.mark.parametrize(
    "capable,unified,expected",
    [(False, False, "high"), (True, False, "original"), (False, True, "high"), (True, True, None)],
)
def test_view_image_typed_detail_follows_capability_and_unified_gate(
    tmp_path, capable, unified, expected
):
    (tmp_path / "pixels.unknown").write_bytes(encoded_image())
    result = asyncio.run(
        ViewImageTool(supports_original=capable, unified_budget=unified).execute(
            ToolCall(
                new_tool_call_id(), "view_image", {"path": "pixels.unknown", "detail": "original"}
            ),
            ToolContext(tmp_path),
        )
    )
    assert result.code_mode_output.value.get("detail") == expected
    assert result.content_items[0].detail == (expected or "original")


@pytest.mark.parametrize(
    "failure", ["bad_pixels", "truncated_pixels", "too_large", "unsupported_model"]
)
def test_view_image_rejects_invalid_or_disallowed_files_before_typed_output(tmp_path, failure):
    raw = encoded_image()
    payload = (
        b"PRIVATE_NOT_AN_IMAGE"
        if failure == "bad_pixels"
        else raw[:45]
        if failure == "truncated_pixels"
        else raw
    )
    (tmp_path / "pixels.png").write_bytes(payload)
    tool = ViewImageTool(
        max_bytes=5 if failure == "too_large" else 100000,
        supports_images=failure != "unsupported_model",
    )
    with pytest.raises(ValueError) as error:
        asyncio.run(
            tool.execute(
                ToolCall(new_tool_call_id(), "view_image", {"path": "pixels.png"}),
                ToolContext(tmp_path),
            )
        )
    assert "PRIVATE" not in str(error.value) and base64.b64encode(payload).decode() not in str(
        error.value
    )
    assert (
        "invalid or unsupported" in str(error.value)
        if "pixels" in failure
        else "byte limit" in str(error.value)
        if failure == "too_large"
        else "do not support" in str(error.value)
    )


@pytest.mark.parametrize(
    "original,size,expected",
    [
        (False, (2048, 2048), (1600, 1600)),
        (True, (6401, 100), (6000, 94)),
        (True, (3201, 3201), (3200, 3200)),
        (False, (64, 32), (64, 32)),
        (False, (1, 10000), (1, 2048)),
        (True, (10000, 1), (6000, 1)),
    ],
)
def test_patch_budget_dimensions(original, size, expected):
    assert output_dimensions(*size, original=original) == expected


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_small_image_preserves_bytes_and_corrects_untrusted_mime(format, mime):
    raw = encoded_image(format=format)
    result = ImagePreparation().prepare_image(ImageAttachment(data_url(raw, "image/wrong")))
    assert result.data_url.startswith(f"data:{mime};base64,")
    assert base64.b64decode(result.data_url.split(",")[1]) == raw


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP", "GIF"])
def test_large_images_resize_and_gif_becomes_png(format):
    raw = encoded_image((2048, 2048), format)
    result = ImagePreparation().prepare_image(ImageAttachment(data_url(raw)))
    with Image.open(io.BytesIO(base64.b64decode(result.data_url.split(",")[1]))) as decoded:
        assert decoded.size == (1600, 1600)
        assert decoded.format == ("PNG" if format == "GIF" else format)


@pytest.mark.parametrize(
    "url,detail,expected",
    [
        ("data:image/png;base64,UFJJVkFURQ==", "high", PROCESSING_ERROR),
        ("data:image/png,invalid", "high", PROCESSING_ERROR),
        ("https://example.invalid/image.png", "high", REMOTE_ERROR),
        ("data:image/png;base64,YWJj", "low", LOW_ERROR),
    ],
)
def test_invalid_image_returns_stable_omission_not_bytes(url, detail, expected):
    assert ImagePreparation().prepare_image(ImageAttachment(url, detail)) == TextContent(expected)


@pytest.mark.parametrize(
    "capable,unified,detail,expected",
    [
        (False, False, "original", "high"),
        (True, False, "original", "original"),
        (True, True, "low", "original"),
        (False, True, "low", None),
    ],
)
def test_detail_capability_and_experimental_gate(capable, unified, detail, expected):
    result = ImagePreparation(
        ImagePolicy(supports_original=capable, unified_budget=unified)
    ).prepare_image(ImageAttachment(data_url(encoded_image()), detail))
    assert result == TextContent(LOW_ERROR) if expected is None else result.detail == expected


def test_exif_and_only_rgb_icc_survive_reencoding():
    exif = Image.Exif()
    exif[274] = 6
    for color in (b"RGB ", b"CMYK"):
        profile = b"\0" * 16 + color + b"\0" * 108
        raw = encoded_image((3000, 1000), "JPEG", exif=exif, icc_profile=profile)
        result = ImagePreparation().prepare_image(ImageAttachment(data_url(raw)))
        with Image.open(io.BytesIO(base64.b64decode(result.data_url.split(",")[1]))) as decoded:
            assert decoded.getexif()[274] == 6
            assert decoded.info.get("icc_profile") == (profile if color == b"RGB " else None)


def test_cache_is_content_and_policy_keyed_and_size_bounded(monkeypatch):
    service = ImagePreparation(
        ImagePolicy(supports_original=True), cache_entries=1, cache_bytes=100000
    )
    decode = service._decode
    calls = []

    def counted(raw, original):
        calls.append(original)
        return decode(raw, original)

    monkeypatch.setattr(service, "_decode", counted)
    raw = encoded_image()
    service.prepare_image(ImageAttachment(data_url(raw, "image/png")))
    service.prepare_image(ImageAttachment(data_url(raw, "image/other")))
    service.prepare_image(ImageAttachment(data_url(raw), "original"))
    assert calls == [False, True] and len(service._cache) == 1
    assert service._cached_bytes <= service.cache_bytes
    tiny = ImagePreparation(cache_bytes=1)
    tiny.prepare_image(ImageAttachment(data_url(raw)))
    assert not tiny._cache


def test_cancelled_preparation_joins_cpu_work(tmp_path, monkeypatch):
    async def scenario():
        service = ImagePreparation()
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        decode = service._decode

        def blocked(raw, original):
            entered.set()
            try:
                release.wait(3)
                return decode(raw, original)
            finally:
                exited.set()

        monkeypatch.setattr(service, "_decode", blocked)
        result = ToolResult(
            new_tool_call_id(),
            "image",
            "before",
            attachments=(ImageAttachment(data_url(encoded_image())),),
        )
        task = asyncio.create_task(service.prepare_result(result))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert exited.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
