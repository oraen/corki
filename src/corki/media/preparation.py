"""Owned image-then-audio preparation at history and model-request boundaries."""

import asyncio
from dataclasses import replace

from corki.media.audio import UNSUPPORTED_INPUT, prepare_audio
from corki.media.images import ImagePreparation, joined_work
from corki.protocol.items import ToolResultItem, UserMessageItem
from corki.protocol.tools import AudioAttachment, TextContent, content_text


class MediaPreparation:
    def __init__(self, image_policy=None, *, supports_audio=False):
        self._images = ImagePreparation(image_policy)
        self._supports_audio = supports_audio
        self._gate = asyncio.Semaphore(2)

    async def prepare_items(self, items, *, for_model=False):
        items = await self._images.prepare_items(items, for_model=for_model)
        if not any(
            isinstance(item, (ToolResultItem, UserMessageItem))
            and any(isinstance(part, AudioAttachment) for part in item.content_items)
            for item in items
        ):
            return items
        async with self._gate:
            return await joined_work(self._items, items, for_model)

    async def prepare_result(self, result):
        result = await self._images.prepare_result(result)
        if not any(isinstance(part, AudioAttachment) for part in result.content_items):
            return result
        async with self._gate:
            parts = await joined_work(self._parts, result.content_items, False)
        return replace(result, content=content_text(parts), content_items=parts)

    def _items(self, items, for_model):
        output = []
        for item in items:
            if not isinstance(item, (ToolResultItem, UserMessageItem)) or not item.content_items:
                output.append(item)
                continue
            parts = self._parts(item.content_items, for_model)
            updates = {"content_items": parts}
            if isinstance(item, ToolResultItem):
                updates["content"] = content_text(parts)
            output.append(replace(item, **updates))
        return tuple(output)

    def _parts(self, parts, for_model):
        prepared = tuple(prepare_audio(p) if isinstance(p, AudioAttachment) else p for p in parts)
        if for_model and not self._supports_audio:
            return tuple(
                TextContent(UNSUPPORTED_INPUT) if isinstance(p, AudioAttachment) else p
                for p in prepared
            )
        return prepared
