"""Owned image-then-audio preparation at history and model-request boundaries."""

import asyncio
from dataclasses import replace

from corki.media.audio import UNSUPPORTED_INPUT, prepare_audio
from corki.media.images import UNSUPPORTED, ImagePreparation, joined_work
from corki.media.response_body import prepare_response_bodies
from corki.protocol.content_kinds import project_message_media, user_content_kinds
from corki.protocol.items import (
    AssistantMessageItem,
    RemoteHistoryItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import AudioAttachment, TextContent, content_text
from corki.protocol.wire_numbers import dumps_wire


class MediaPreparation:
    def __init__(self, image_policy=None, *, supports_audio=False):
        self._images = ImagePreparation(image_policy)
        self._supports_audio = supports_audio
        self._gate = asyncio.Semaphore(2)

    async def prepare_items(self, items, *, for_model=False):
        items = await self._prepare_items(items, for_model=for_model)
        if not for_model or (self._images.policy.supports_images and self._supports_audio):
            return items
        projected = []
        for item in items:
            if isinstance(item, UserMessageItem) and item.content_item_kinds is not None:
                parts = item.content_items or (TextContent(item.content), *item.attachments)
                item = replace(item, content_item_kinds=tuple(user_content_kinds(item, parts)))
            projected.append(item)
        return tuple(projected)

    async def _prepare_items(self, items, *, for_model):
        if any(
            isinstance(item, AssistantMessageItem) and item.response_body_json is not None
            for item in items
        ):
            async with self._gate:
                items = await joined_work(
                    prepare_response_bodies, items, self._images, self._supports_audio, for_model
                )
        if for_model:
            replacements = {}
            if not self._images.policy.supports_images:
                replacements["input_image"] = ("images.unsupported", UNSUPPORTED)
            if not self._supports_audio:
                replacements["input_audio"] = ("audio.unsupported", UNSUPPORTED_INPUT)
            projected = []
            for item in items:
                if isinstance(item, RemoteHistoryItem) and item.payload["type"] == "message":
                    payload = project_message_media(item.payload, replacements)
                    item = replace(item, payload_json=dumps_wire(payload))
                projected.append(item)
            items = tuple(projected)
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
            if isinstance(item, UserMessageItem) and for_model and not self._supports_audio:
                kinds = user_content_kinds(item, item.content_items)
                updates["content_item_kinds"] = tuple(
                    "audio.unsupported"
                    if isinstance(original, AudioAttachment)
                    and isinstance(prepared, TextContent)
                    and prepared.text == UNSUPPORTED_INPUT
                    else kind
                    for original, prepared, kind in zip(
                        item.content_items, parts, kinds, strict=True
                    )
                )
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
