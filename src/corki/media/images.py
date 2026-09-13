"""Codex-style decoded image preparation, with owned CPU work and bounded caching."""

from __future__ import annotations

import asyncio
import base64
import io
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from hashlib import sha256

from PIL import Image

from corki.protocol.content_kinds import user_content_kinds
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.protocol.tools import ImageAttachment, TextContent, content_text

PROCESSING_ERROR = "image content omitted because it could not be processed"
TOO_LARGE = (
    "image content omitted because it exceeded the supported size limit; use a smaller image"
)
REMOTE_ERROR = "image content omitted because remote image URLs are not supported"
LOW_ERROR = (
    "image content omitted because detail 'low' is not supported; use 'high', 'original', or 'auto'"
)
UNSUPPORTED = "image content omitted because you do not support image input"
MAX_INPUT_BYTES = 1024 * 1024 * 1024
MAX_DECODE_PIXELS = 64_000_000
FORMATS = ("PNG", "JPEG", "GIF", "WEBP")


class ImageTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImagePolicy:
    supports_images: bool = True
    supports_original: bool = False
    unified_budget: bool = False

    @property
    def unified(self):
        # Corki has no Responses Lite transport; only original-capable models
        # can activate Codex's experimental unified-budget branch here.
        return self.unified_budget and self.supports_original


def output_dimensions(width: int, height: int, *, original: bool) -> tuple[int, int]:
    maximum, patches = (6000, 10000) if original else (2048, 2500)

    def fits(w, h):
        return max(w, h) <= maximum and math.ceil(w / 32) * math.ceil(h / 32) <= patches

    width, height = max(1, width), max(1, height)
    if fits(width, height):
        return width, height
    scale = min(1, maximum / max(width, height))
    # Rust f64::round uses half-away-from-zero, not Python's bankers rounding.
    width, height = (
        max(1, math.floor(width * scale + 0.5)),
        max(1, math.floor(height * scale + 0.5)),
    )
    if fits(width, height):
        return width, height
    scale = math.sqrt(32 * 32 * patches / width / height)
    across, down = width * scale / 32, height * scale / 32
    scale *= min(math.floor(across) / across, math.floor(down) / down)
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def validated_image(raw: bytes):
    image = Image.open(io.BytesIO(raw), formats=FORMATS)
    try:
        if image.width * image.height > MAX_DECODE_PIXELS:
            raise ImageTooLarge("image pixel limit exceeded")
        image.load()  # A plausible header alone is not proof of valid pixels.
        return image
    except BaseException:
        image.close()
        raise


def validate_image_bytes(raw: bytes) -> None:
    with validated_image(raw):
        pass


async def joined_work(work, *args):
    task = asyncio.create_task(asyncio.to_thread(work, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class ImagePreparation:
    def __init__(self, policy=None, *, cache_entries=32, cache_bytes=64 * 1024 * 1024):
        self.policy = policy or ImagePolicy()
        self.cache_entries, self.cache_bytes = cache_entries, cache_bytes
        self._cache = OrderedDict()
        self._cached_bytes = 0
        self._lock = threading.Lock()
        self._gate = asyncio.Semaphore(2)

    async def prepare_items(self, items, *, for_model=False):
        if not any(
            isinstance(item, (ToolResultItem, UserMessageItem))
            and (
                item.attachments or any(isinstance(p, ImageAttachment) for p in item.content_items)
            )
            for item in items
        ):
            return tuple(items)
        async with self._gate:
            return await joined_work(self._items, tuple(items), for_model)

    async def prepare_result(self, result):
        if not result.attachments and not any(
            isinstance(p, ImageAttachment) for p in result.content_items
        ):
            return result
        async with self._gate:
            return await joined_work(self._result, result)

    def _result(self, result):
        parts = result.content_items or (TextContent(result.content), *result.attachments)
        prepared = self._parts(parts)
        return replace(
            result, content=content_text(prepared), content_items=prepared, attachments=()
        )

    def _items(self, items, for_model):
        output = []
        for item in items:
            if not isinstance(item, (ToolResultItem, UserMessageItem)):
                output.append(item)
                continue
            if not item.attachments and not item.content_items:
                output.append(item)
                continue
            parts = item.content_items or (TextContent(item.content), *item.attachments)
            prepared = self._parts(parts)
            kinds = user_content_kinds(item, parts) if isinstance(item, UserMessageItem) else None
            if for_model and not self.policy.supports_images:
                if kinds is not None:
                    kinds = [
                        "images.unsupported" if isinstance(p, ImageAttachment) else kind
                        for p, kind in zip(prepared, kinds, strict=True)
                    ]
                prepared = tuple(
                    TextContent(UNSUPPORTED) if isinstance(p, ImageAttachment) else p
                    for p in prepared
                )
            updates = {"content_items": prepared, "attachments": ()}
            if kinds is not None and for_model and not self.policy.supports_images:
                updates["content_item_kinds"] = tuple(kinds)
            if isinstance(item, ToolResultItem):
                updates["content"] = content_text(prepared)
            output.append(replace(item, **updates))
        return tuple(output)

    def _parts(self, parts):
        return tuple(
            self.prepare_image(part) if isinstance(part, ImageAttachment) else part
            for part in parts
        )

    def prepare_image(self, part):
        url = part.data_url
        if url.partition(":")[0].lower() in {"http", "https"}:
            return TextContent(REMOTE_ERROR)
        if url[:5].lower() != "data:":
            return part  # Match Codex's non-data, non-HTTP passthrough branch.
        detail = part.detail
        if detail == "original" and not self.policy.supports_original:
            detail = "high"
        if detail == "low" and not self.policy.unified:
            return TextContent(LOW_ERROR)
        original = self.policy.unified or detail == "original"
        try:
            metadata, separator, payload = url[5:].partition(",")
            if not separator or "base64" not in metadata.lower().split(";"):
                raise ValueError("invalid data URL")
            if len(payload) > MAX_INPUT_BYTES:
                raise ImageTooLarge("base64 limit exceeded")
            raw = base64.b64decode(payload, validate=True)
            if len(raw) > MAX_INPUT_BYTES:
                raise ImageTooLarge("decoded input limit exceeded")
            key = sha256(raw).digest(), original
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
            if cached is None:
                cached = self._decode(raw, original)
                with self._lock:
                    size = len(cached)
                    if size <= self.cache_bytes and self.cache_entries > 0:
                        previous = self._cache.pop(key, None)
                        if previous is not None:
                            self._cached_bytes -= len(previous)
                        self._cache[key] = cached
                        self._cached_bytes += size
                        while (
                            len(self._cache) > self.cache_entries
                            or self._cached_bytes > self.cache_bytes
                        ):
                            _, evicted = self._cache.popitem(last=False)
                            self._cached_bytes -= len(evicted)
            return ImageAttachment(cached, "original" if self.policy.unified else detail)
        except (ImageTooLarge, Image.DecompressionBombError):
            return TextContent(TOO_LARGE)
        except Exception:  # noqa: BLE001 - malformed media is a per-item omission
            return TextContent(PROCESSING_ERROR)

    @staticmethod
    def _decode(raw, original):
        with validated_image(raw) as source:
            target = output_dimensions(source.width, source.height, original=original)
            format = source.format
            if target == source.size and format in {"PNG", "JPEG", "WEBP"}:
                encoded = raw
            else:
                metadata = {}
                profile = source.info.get("icc_profile")
                if isinstance(profile, bytes) and profile[16:20] == b"RGB ":
                    metadata["icc_profile"] = profile
                exif = source.info.get("exif")
                if exif:
                    metadata["exif"] = exif
                if format not in {"PNG", "JPEG", "WEBP"}:
                    format = "PNG"
                mode = "RGB" if format == "JPEG" else "RGBA"
                with source.convert(mode) as pixels:
                    prepared = (
                        pixels.resize(target, Image.Resampling.BILINEAR)
                        if target != pixels.size
                        else pixels
                    )
                    try:
                        stream = io.BytesIO()
                        prepared.save(
                            stream,
                            format=format,
                            **metadata,
                            **(
                                {"quality": 85}
                                if format == "JPEG"
                                else {"lossless": True}
                                if format == "WEBP"
                                else {}
                            ),
                        )
                        encoded = stream.getvalue()
                    finally:
                        if prepared is not pixels:
                            prepared.close()
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[format]
            return f"data:{mime};base64,{base64.b64encode(encoded).decode('ascii')}"
