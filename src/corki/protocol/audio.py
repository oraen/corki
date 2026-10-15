"""Bounded PCM WAV duration from actual data, including streaming RIFF headers."""

import base64
import struct

MAX_AUDIO_BYTES = 50 * 1024 * 1024


def wav_duration_seconds(url: str) -> float | None:
    metadata, separator, payload = url.partition(",")
    if not separator or "base64" not in metadata.lower().split(";")[1:]:
        return None
    if len(payload) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
        return None
    try:
        raw = base64.b64decode(payload, validate=True)
        if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return None
        position, audio_format = 12, None
        while position + 8 <= len(raw):
            kind = raw[position : position + 4]
            size = struct.unpack_from("<I", raw, position + 4)[0]
            start = position + 8
            chunk = memoryview(raw)[start : start + size]
            if kind == b"fmt ":
                encoding = struct.unpack_from("<H", chunk)[0]
                if encoding == 0xFFFE:
                    if bytes(chunk[26:40]) != bytes.fromhex("000000001000800000aa00389b71"):
                        return None
                    encoding = struct.unpack_from("<H", chunk, 24)[0]
                if encoding not in {1, 3}:
                    return None
                rate = struct.unpack_from("<I", chunk, 4)[0]
                align = struct.unpack_from("<H", chunk, 12)[0]
                if not rate or not align:
                    return None
                audio_format = rate, align
            elif kind == b"data":
                if audio_format is None:
                    return None
                rate, align = audio_format
                return (len(chunk) // align) / rate
            position = start + size + size % 2
    except (ValueError, struct.error):
        return None
    return None
