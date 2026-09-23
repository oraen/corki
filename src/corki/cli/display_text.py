"""Keep untrusted text from issuing terminal control sequences while rendering."""

_CONTROL_TRANSLATION = {
    codepoint: f"\\x{codepoint:02x}"
    for codepoint in (*range(0x20), *range(0x7F, 0xA0))
    if codepoint not in (0x09, 0x0A)
}


def visible_terminal_text(value: str) -> str:
    """Escape terminal controls, retaining tabs, newlines, and ordinary Unicode."""

    # Keep conventional CRLF line endings as lines; a lone CR can overwrite
    # already displayed text and must remain visible instead.
    return value.replace("\r\n", "\n").translate(_CONTROL_TRANSLATION)
