"""Keep untrusted text from issuing terminal control sequences while rendering."""

import re

_USER_CONTROLS = re.compile(r"\x1b\[[^@-~]*(?:[@-~]|$)|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_user_text(value: str) -> str:
    """Codex user-input rule: remove CSI and controls, preserving tabs/newlines.

    An unfinished CSI consumes the remaining text. Other ESC sequences lose only
    their control characters, matching history_cell::sanitize_user_text. This is
    input normalization, not the lossless escaping used to display tool output.
    """
    return _USER_CONTROLS.sub("", value)


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
