"""Provider rendering of ordered tool output, with explicit audio capability fallback."""

from corki.media.audio import UNSUPPORTED_INPUT as UNSUPPORTED_AUDIO
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolContent,
)

UNSUPPORTED_ENCRYPTED = (
    "[Encrypted tool output unavailable: this provider cannot read the opaque result. "
    "Use a supported provider or request a plaintext result from its source.]"
)


def response_content(
    parts: tuple[ToolContent, ...], *, audio_enabled: bool, encrypted_enabled=False
):
    output = []
    for part in parts:
        if isinstance(part, TextContent):
            output.append({"type": "input_text", "text": part.text})
        elif isinstance(part, EncryptedContent):
            output.append(
                {"type": "encrypted_content", "encrypted_content": part.encrypted_content}
                if encrypted_enabled
                else {"type": "input_text", "text": UNSUPPORTED_ENCRYPTED}
            )
        elif isinstance(part, ImageAttachment):
            output.append(
                {"type": "input_image", "image_url": part.data_url, "detail": part.detail}
            )
        elif audio_enabled:
            output.append({"type": "input_audio", "audio_url": part.data_url})
        else:
            output.append({"type": "input_text", "text": UNSUPPORTED_AUDIO})
    return output


def chat_content(parts: tuple[ToolContent, ...], *, audio_enabled: bool):
    output = []
    for part in parts:
        if isinstance(part, TextContent):
            output.append({"type": "text", "text": part.text})
        elif isinstance(part, EncryptedContent):
            output.append({"type": "text", "text": UNSUPPORTED_ENCRYPTED})
        elif isinstance(part, ImageAttachment):
            output.append(
                {"type": "image_url", "image_url": {"url": part.data_url, "detail": part.detail}}
            )
        elif isinstance(part, AudioAttachment):
            metadata, separator, payload = part.data_url.partition(",")
            mime = metadata[5:].split(";", 1)[0].lower()
            formats = {
                "audio/wav": "wav",
                "audio/x-wav": "wav",
                "audio/wave": "wav",
                "audio/mpeg": "mp3",
                "audio/mp3": "mp3",
            }
            if (
                audio_enabled
                and separator
                and "base64" in metadata.lower().split(";")[1:]
                and mime in formats
            ):
                output.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": payload, "format": formats[mime]},
                    }
                )
            else:
                output.append(
                    {
                        "type": "text",
                        "text": UNSUPPORTED_AUDIO
                        if not audio_enabled
                        else "[Audio output omitted: Chat audio input requires base64 WAV or MP3.]",
                    }
                )
    return output
