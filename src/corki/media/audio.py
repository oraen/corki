"""Audio data-URL normalization; intentionally independent of codec decodability."""

import base64

from corki.protocol.audio import MAX_AUDIO_BYTES
from corki.protocol.tools import AudioAttachment, TextContent

PROCESSING_ERROR = "audio content omitted because it could not be processed"
TOO_LARGE = (
    "audio content omitted because it exceeded the supported size limit; use a smaller audio file"
)
UNSUPPORTED_FORMAT = (
    "audio content omitted because its format is not supported; use wav, mp3, m4a, webm, or ogg"
)
UNSUPPORTED_INPUT = "audio content omitted because you do not support audio input"

_MIMES = {
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/mp4": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/webm": "audio/webm",
    "audio/ogg": "audio/ogg",
}


def prepare_audio(part: AudioAttachment) -> AudioAttachment | TextContent:
    url = part.data_url
    if url[:5].lower() != "data:":
        return TextContent(PROCESSING_ERROR)
    metadata, separator, payload = url[5:].partition(",")
    fields = metadata.split(";")
    if not separator or not fields[0]:
        return TextContent(PROCESSING_ERROR)
    mime = _MIMES.get(fields[0].lower())
    if mime is None:
        return TextContent(UNSUPPORTED_FORMAT)
    if not any(field.lower() == "base64" for field in fields[1:]):
        return TextContent(PROCESSING_ERROR)
    if len(payload) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
        return TextContent(TOO_LARGE)
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError:
        return TextContent(PROCESSING_ERROR)
    if len(raw) > MAX_AUDIO_BYTES:
        return TextContent(TOO_LARGE)
    encoded = base64.b64encode(raw).decode("ascii")
    # Rust's STANDARD decoder rejects nonzero trailing bits and excess padding;
    # Python's validate=True alone still permits those non-canonical encodings.
    if encoded != payload:
        return TextContent(PROCESSING_ERROR)
    return AudioAttachment(f"data:{mime};base64,{encoded}")
