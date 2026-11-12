"""Prepare vision inputs at send time, keeping original desktop assets unchanged.

OpenAI image requirements: developers.openai.com/api/docs/guides/images-vision
Claude limits: platform.claude.com/docs/en/build-with-claude/vision
Unknown compatible endpoints do not inherit limits from a model-name substring.
"""

import io
import math
from urllib.parse import urlparse

from PIL import Image, ImageOps


def image_budget(settings, count):
    url = urlparse(settings.api_base)
    host = (url.hostname or "").lower()
    route = url.path.rstrip("/").removesuffix("/v1")
    local = host in {"localhost", "127.0.0.1", "::1"}
    # Request budgets include base64 expansion. Reserve room for non-image input;
    # the runtime remains responsible for checking the complete request.
    if local and route.endswith("/qoder"):
        return max(1024, (8 * 1024 * 1024 - 64 * 1024) * 3 // (4 * count))
    if host == "api.anthropic.com":
        return min(10_000_000 * 3 // 4, 31_000_000 * 3 // (4 * count))
    if host == "api.openai.com" or (local and route.endswith("/openai")):
        return (512 * 1024 * 1024 - 1024 * 1024) * 3 // (4 * count)
    return None


def prepare_image(raw, mime, settings, count=1):
    count = max(1, count)
    provider_budget = image_budget(settings, count)
    # Desktop event transport is 64 MiB. Compression keeps the encoded images
    # within its budget, rather than rejecting the original upload.
    transport_budget = 48 * 1024 * 1024 * 3 // (4 * count)
    budget = min(provider_budget, transport_budget) if provider_budget else transport_budget
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("不支持的图片类型")
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            # Resize oversized decoded images rather than rejecting a large screenshot.
            pixels = source.width * source.height
            if (budget is None or len(raw) <= budget) and pixels <= 32_000_000:
                return raw, mime
            image = ImageOps.exif_transpose(source)
            if pixels > 32_000_000:
                scale = math.sqrt(32_000_000 / pixels)
                image.thumbnail(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                )
            # Prefer a lossless representation for screenshots and text.
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            if budget is None or output.tell() <= budget:
                return output.getvalue(), "image/png"
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                background = Image.new("RGB", image.size, "white")
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            while True:
                for quality in (90, 80, 65, 45):
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= budget:
                        return output.getvalue(), "image/jpeg"
                if max(image.size) <= 64:
                    raise ValueError("图片压缩后仍超过供应商限制")
                image.thumbnail((max(1, int(image.width * 0.75)), max(1, int(image.height * 0.75))))
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("图片内容无效或尺寸过大") from error
