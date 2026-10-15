"""Updating a source line must not rewrite unrelated line endings."""

from corki.tools.builtin.patch import _apply_hunks


def test_update_preserves_crlf_and_mixed_existing_endings():
    original = "first\r\nsecond\nthird\r\n"
    updated = _apply_hunks(original, ("@@", " second", "-third", "+THIRD"), "fixture")
    assert updated == "first\r\nsecond\nTHIRD\r\n"


def test_update_uses_first_existing_ending_for_new_lines():
    updated = _apply_hunks("first\r\nsecond\r\n", ("@@", " first", "+middle", " second"), "fixture")
    assert updated == "first\r\nmiddle\r\nsecond\r\n"
