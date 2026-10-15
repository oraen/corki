"""Prepare assistant media on copies without rewriting its archived body."""

from dataclasses import replace

from corki.media.audio import UNSUPPORTED_INPUT, prepare_audio
from corki.media.images import UNSUPPORTED
from corki.protocol.items import AssistantMessageItem
from corki.protocol.response_body import response_body_payload
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent
from corki.protocol.wire_numbers import dumps_wire


def prepare_response_bodies(items, images, supports_audio, for_model):
    result = []
    for item in items:
        if isinstance(item, AssistantMessageItem) and item.response_body_json is not None:
            body = response_body_payload(item.response_body_json, "message")
            content = []
            for part in body["content"]:
                if part["type"] == "input_image":
                    prepared = images.prepare_image(
                        ImageAttachment(part["image_url"], part.get("detail", "auto"))
                    )
                    part = (
                        {"type": "input_text", "text": prepared.text}
                        if isinstance(prepared, TextContent)
                        else {
                            "type": "input_image",
                            "image_url": prepared.data_url,
                            "detail": prepared.detail,
                        }
                    )
                elif part["type"] == "input_audio":
                    prepared = prepare_audio(AudioAttachment(part["audio_url"]))
                    part = (
                        {"type": "input_text", "text": prepared.text}
                        if isinstance(prepared, TextContent)
                        else {"type": "input_audio", "audio_url": prepared.data_url}
                    )
                content.append(part)
            replacements = {}
            if for_model and not images.policy.supports_images:
                replacements["input_image"] = UNSUPPORTED
            if for_model and not supports_audio:
                replacements["input_audio"] = UNSUPPORTED_INPUT
            projected = [
                {"type": "input_text", "text": replacements[part["type"]]}
                if part["type"] in replacements
                else part
                for part in content
            ]
            updates = {}
            if projected != body["content"]:
                updates["response_body_json"] = dumps_wire({"content": projected}, sort_keys=True)
            if updates:
                item = replace(item, **updates)
        result.append(item)
    return tuple(result)
