"""Enhanced terminal keys translated to the composer's existing editing keys."""

import re
from contextlib import contextmanager

from prompt_toolkit.output.vt100 import Vt100_Output

_CSI_U = re.compile(r"\x1b\[(\d+)(?::(\d*)(?::\d*)?)?(?:;(\d+)(?::([123]))?)?u")
_MODIFY_OTHER = re.compile(r"\x1b\[27;(\d+);(\d+)~")


def decode_key(sequence: str) -> str:
    """Preserve legacy CSI; decode enhanced text/control keys and drop releases."""
    match = _CSI_U.fullmatch(sequence)
    if match:
        code, shifted, modifiers, event = match.groups()
        if event == "3":
            return ""
        code, modifiers = int(code), int(modifiers or 1) - 1
        if modifiers & 1 and shifted:
            code = int(shifted)
    else:
        match = _MODIFY_OTHER.fullmatch(sequence)
        if not match:
            if sequence.startswith("\x1b[") and sequence.endswith("u") and sequence[2:3].isdigit():
                return ""
            return sequence
        modifiers, code = (int(value) for value in match.groups())
        modifiers -= 1
    # Unsupported system shortcuts must not become unmodified submit/control keys.
    if modifiers < 0 or modifiers & ~(7 | 64 | 128) or not 0 <= code <= 0x10FFFF:
        return ""
    modifiers &= 7
    if 0xD800 <= code <= 0xDFFF or 0xE000 <= code <= 0xF8FF:
        return ""
    if code in {10, 13}:
        return "\n" if modifiers else "\r"
    if code == 9 and modifiers == 1:
        return "\x1b[Z"
    char = chr(code)
    if modifiers & 4:
        if char == "?":
            char = "\x7f"
        elif char == " " or (len(char.upper()) == 1 and "@" <= char.upper() <= "_"):
            char = chr(ord(char.upper()) & 31)
        else:
            return ""
    elif modifiers & 1 and char.isascii() and char.isalpha():
        char = char.upper()
    return ("\x1b" if modifiers & 2 else "") + char


@contextmanager
def enhanced_keyboard(app):
    """Own keyboard reporting only while an interactive VT prompt owns input."""
    output = getattr(app, "output", None)
    if not isinstance(output, Vt100_Output):
        yield
        return
    active = False

    def enable(_):
        nonlocal active
        if active:
            return
        active = True
        # Xterm/tmux fallback first; Kitty-capable terminals select CSI-u last.
        output.write_raw("\x1b[>4;2m\x1b[>1u")
        output.flush()

    # before_render runs after prompt-toolkit acquires raw input ownership.
    app.before_render += enable
    try:
        yield
    finally:
        app.before_render -= enable
        if active:
            output.write_raw("\x1b[<u\x1b[>4;0m")
            output.flush()
