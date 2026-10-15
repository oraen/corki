"""Pinned Codex sanitizer examples and matching boundaries; all tokens are fake."""

import pytest

from corki.memory.pipeline import _redact_secrets


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Bearer abcde+fghijklmnopqrstuvwxyz012345", "Bearer [REDACTED_SECRET]"),
        ("Bearer abcdefghijklmnop+secret_suffix", "Bearer [REDACTED_SECRET]"),
        ("Bearer sk-abcdefghijklmnopqrst+secret_suffix", "Bearer [REDACTED_SECRET]"),
        ("Bearer AKIAABCDEFGHIJKLMNOP/~secret_suffix", "Bearer [REDACTED_SECRET]"),
        ("Bearer AbcdefghijklMN09._~+/-==; echo done", "Bearer [REDACTED_SECRET]; echo done"),
        ("authorization: bEaReR\tabcdefghijklmnop", "authorization: Bearer [REDACTED_SECRET]"),
        ("Bearer   abcdefghijklmnop", "Bearer [REDACTED_SECRET]"),
        ("sk-abcdefghijklmnopqrst", "[REDACTED_SECRET]"),
        ("AKIAABCDEFGHIJKLMNOP", "[REDACTED_SECRET]"),
        ('api_key = "abcdefgh"', 'api_key = "[REDACTED_SECRET]"'),
        ("token: 'abcdefgh'", "token: '[REDACTED_SECRET]'"),
        ("PASSWORD=abcdefgh", "PASSWORD=[REDACTED_SECRET]"),
        ("secret=abcdefgh;next", "secret=[REDACTED_SECRET]"),
    ],
)
def test_redacts_supported_secrets_with_source_order_and_prefixes(value, expected):
    assert _redact_secrets(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "Bearer of good news",
        "Bearer abcdefghijklmno",
        "NotABearer abcdefghijklmnop",
        "Bearerabcdefghijklmnop",
        "Bearer\nabcdefghijklmnop",
        "Bearer\u00a0abcdefghijklmnop",
        "Bearer abcdefghijklmno\u212a",
        "sk-abcdefghijklmnopqrs",
        "AKIAABCDEFGHIJKLMNO",
        "prefixAKIAABCDEFGHIJKLMNOP",
        "password=abcdefg",
        "tokenizer=abcdefgh",
    ],
)
def test_source_boundaries_do_not_redact_ordinary_or_short_values(value):
    assert _redact_secrets(value) == value
