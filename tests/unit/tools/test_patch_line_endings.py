"""Updating a source line must not rewrite unrelated line endings."""

import pytest

from corki.tools.builtin.patch import _apply_hunks, _apply_operations, _parse_patch


def test_update_preserves_crlf_and_mixed_existing_endings():
    original = "first\r\nsecond\nthird\r\n"
    updated = _apply_hunks(original, ("@@", " second", "-third", "+THIRD"), "fixture")
    assert updated == "first\r\nsecond\nTHIRD\r\n"


def test_update_uses_first_existing_ending_for_new_lines():
    updated = _apply_hunks("first\r\nsecond\r\n", ("@@", " first", "+middle", " second"), "fixture")
    assert updated == "first\r\nmiddle\r\nsecond\r\n"


@pytest.mark.parametrize("move", [False, True])
def test_file_update_preserves_line_endings_and_unicode_path(tmp_path, move):
    source = tmp_path / "中文 original.txt"
    source.write_bytes(b"first\r\nsecond\nthird\r\n")
    target = tmp_path / "中文 moved.txt" if move else source
    rename = f"*** Move to: {target.name}\n" if move else ""
    operations = _parse_patch(
        f"*** Begin Patch\n*** Update File: {source.name}\n{rename}"
        "@@\n second\n-third\n+THIRD\n*** End Patch"
    )
    _apply_operations(tmp_path, operations)
    assert target.read_bytes() == b"first\r\nsecond\nTHIRD\r\n"
    if move:
        assert not source.exists()
