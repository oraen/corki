"""Async lifecycle output never obtains synchronous Turn-control authority."""

import pytest

from corki.core.stop_hooks import outcome


@pytest.mark.parametrize(
    "stdout,code,warning,error",
    [
        ('{"continue":false,"stopReason":"stop","systemMessage":"notice"}', 0, "notice", False),
        ('{"decision":"block","reason":"stop","systemMessage":"notice"}', 0, "notice", False),
        ('{"decision":"block"}', 0, None, False),
        ("ordinary stdout is not context", 0, None, False),
        ("{invalid", 0, None, True),
        ('{"continue":"false"}', 0, None, True),
        ("", 2, None, True),
        ("", 9, None, True),
    ],
)
def test_async_diagnostics_without_control(stdout, code, warning, error):
    decision, text, warnings = outcome(
        {"exit_code": code, "stdout": stdout, "stderr": "never a continuation"}, control=False
    )
    assert decision == "allow"
    assert bool(text) is error
    assert warnings == ((warning,) if warning else ())
