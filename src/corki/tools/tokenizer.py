"""The pinned bm25 2.3.2 English path (licenses in TOKENIZER_LICENSES.txt).

Normalize with deunicode 1.6.2, lowercase, Unicode word boundaries, NLTK
stopwords from stop-words 0.9.0, then the legacy Snowball English stemmer.
Normalization produces ASCII, so only ASCII word-boundary states are reachable.
This is lexical tool discovery, not language detection or semantic retrieval.
"""

import base64
import hashlib
import json
import sys
import zlib
from collections.abc import Iterator
from functools import lru_cache
from importlib.resources import files

from snowballstemmer.english_stemmer import EnglishStemmer


@lru_cache(maxsize=1)
def _data() -> tuple[bytes, bytes, frozenset[str]]:
    resource = json.loads(files("corki.tools").joinpath("tokenizer_data.json").read_text("utf-8"))
    encoded = "".join(resource["deunicode_zlib_base64"])
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    pointers, mapping = raw[:419994], raw[419994:]
    for name, data in (("pointers", pointers), ("mapping", mapping)):
        if hashlib.sha256(data).hexdigest() != resource[f"{name}_sha256"]:
            raise ValueError("pinned tokenizer data checksum mismatch: " + name)
    # The exact NLTK English list in this release is already ASCII/lowercase;
    # its original file has no final newline. Do not substitute the ISO feature.
    words = resource["nltk_english"]
    if (
        hashlib.sha256("\n".join(words).encode("ascii")).hexdigest()
        != resource["nltk_english_sha256"]
    ):
        raise ValueError("pinned tokenizer data checksum mismatch: nltk English")
    return pointers, mapping, frozenset(words)


def _deunicode_char(char: str, pointers: bytes, mapping: bytes) -> str | None:
    index = ord(char) * 3
    if index + 3 > len(pointers):
        return None
    first, second, length = pointers[index : index + 3]
    if length <= 2:
        return bytes((first, second))[:length].decode("ascii")
    start = first | second << 8
    if start + length > len(mapping):
        return None  # Out-of-range entries intentionally represent unknown characters.
    return mapping[start : start + length].decode("ascii")


def normalize(text: str) -> str:
    """Match deunicode_with_tofu_cow, including its prefix and lookahead rules."""
    start = 0
    while start < len(text) and ord(text[start]) < 0x7F:
        start += 1
    if start == len(text):
        return text
    pointers, mapping, _ = _data()
    parts = [text[:start]]
    current = _deunicode_char(text[start], pointers, mapping)
    for index in range(start, len(text)):
        at_end = index + 1 == len(text)
        following = None if at_end else _deunicode_char(text[index + 1], pointers, mapping)
        if current is None:
            parts.append("[?]")
        elif (
            len(current) > 1
            and current.endswith(" ")
            and (at_end or (following is not None and following.startswith(" ")))
        ):
            parts.append(current[:-1])
        else:
            parts.append(current)
        current = following
    return "".join(parts)


def _ascii_words(text: str) -> Iterator[str]:
    """unicode-segmentation 1.12.0 word states restricted to normalized ASCII."""
    index = 0
    while index < len(text):
        char = text[index]
        if not (char.isalnum() or char == "_"):
            index += 1
            continue
        start = index
        has_word = char.isalnum()
        index += 1
        while index < len(text):
            char = text[index]
            if char.isalnum() or char == "_":
                has_word |= char.isalnum()
                index += 1
                continue
            if index + 1 == len(text):
                break
            before, after = text[index - 1], text[index + 1]
            if (char in "'.:" and before.isalpha() and after.isalpha()) or (
                char in "'.,;" and before.isdigit() and after.isdigit()
            ):
                index += 2
                continue
            break
        if has_word:
            yield text[start:index]


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    _, _, stopwords = _data()
    # A generated stemmer has mutable cursors; own one per call, never share it
    # across concurrent searches. Import directly to avoid optional PyStemmer routing.
    stemmer = EnglishStemmer()
    return [
        stemmer.stemWord(word)
        for word in _ascii_words(normalize(text).lower())
        if word not in stopwords
    ]


def token_id(token: str) -> int:
    """bm25's u32 TokenEmbedder: fxhash 0.2.1 hash32 of a Rust str.

    Native-endian four-byte chunks, remaining bytes, then a SEPARATE 0xff write
    from str::hash. This non-cryptographic identity deliberately retains collisions.
    """
    data = token.encode("utf-8")
    boundary = len(data) // 4 * 4
    result = 0

    def mix(word: int) -> None:
        nonlocal result
        rotated = ((result << 5) | (result >> 27)) & 0xFFFFFFFF
        result = ((rotated ^ word) * 0x27220A95) & 0xFFFFFFFF

    for index in range(0, boundary, 4):
        mix(int.from_bytes(data[index : index + 4], sys.byteorder))
    for byte in data[boundary:]:
        mix(byte)
    mix(0xFF)
    return result
