"""Oversized screenshots are adapted for the chosen endpoint, not rejected at 1 MB."""

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from images import image_budget, prepare_image


def picture():
    import random

    output = io.BytesIO()
    Image.frombytes("RGB", (900, 700), random.Random(1).randbytes(900 * 700 * 3)).save(
        output, "PNG"
    )
    return output.getvalue()


def test_images_larger_than_old_limit_remain_original_when_supported():
    raw = picture()
    assert len(raw) > 1_000_000
    for url in (
        "https://api.openai.com/v1",
        "http://127.0.0.1:17001/openai",
        "https://custom.invalid/v1",
    ):
        assert prepare_image(raw, "image/png", SimpleNamespace(api_base=url)) == (raw, "image/png")


def test_qoder_compresses_to_request_budget_without_modifying_original():
    raw = picture() + b"\0" * 7_000_000
    settings = SimpleNamespace(api_base="http://127.0.0.1:17001/qoder")
    for count in (1, 3):
        prepared, mime = prepare_image(raw, "image/png", settings, count)
        assert len(prepared) <= image_budget(settings, count)
        assert mime in {"image/png", "image/jpeg"}
        with Image.open(io.BytesIO(prepared)) as image:
            image.verify()
    assert raw == picture() + b"\0" * 7_000_000


def test_invalid_image_is_not_forwarded():
    with pytest.raises(ValueError, match="图片内容无效"):
        prepare_image(b"broken", "image/png", SimpleNamespace(api_base="https://custom.invalid"))


def test_known_provider_budget_and_unknown_endpoint_not_guessed_from_model():
    assert image_budget(SimpleNamespace(api_base="https://api.anthropic.com/v1"), 1) == 7_500_000
    assert image_budget(SimpleNamespace(api_base="https://custom.invalid/openai"), 1) is None


def test_oversized_file_tries_lossless_compression_before_jpeg():
    raw = picture() + b"\0" * 8_000_000
    prepared, mime = prepare_image(raw, "image/png", SimpleNamespace(api_base="https://api.anthropic.com/v1"))
    assert mime == "image/png"
    assert len(prepared) < 7_500_000
    with Image.open(io.BytesIO(prepared)) as image:
        assert image.size == (900, 700)


def test_qoder_image_budget_tracks_eight_mib_request_with_base64_room():
    settings = SimpleNamespace(api_base="http://127.0.0.1:17001/qoder/v1")
    assert image_budget(settings, 1) == (8 * 1024 * 1024 - 64 * 1024) * 3 // 4
    assert image_budget(settings, 4) == image_budget(settings, 1) // 4
