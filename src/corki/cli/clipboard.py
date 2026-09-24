"""User-requested clipboard writes; no shell interpolation or detached helpers."""

import base64
import os
import re
import sys

from markdown_it import MarkdownIt

from corki.execution.owned_process import run_owned


def copy_choices(markdown):
    choices = {"Whole response": markdown}
    lines = markdown.splitlines(keepends=True)
    quote_depth = 0
    for token in MarkdownIt().parse(markdown):
        content = None
        if token.type in {"fence", "code_block"}:
            language = token.info.split()[0] if token.info.strip() else ""
            label = f"{language} code" if language else "Code block"
            content = token.content
        elif token.type == "blockquote_open":
            if not quote_depth and token.map:
                label = "Blockquote"
                content = "".join(
                    re.sub(r"^ {0,3}> ?", "", line) for line in lines[token.map[0] : token.map[1]]
                )
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        if content and content.strip():
            choices[f"{len(choices)}. {label}"] = content
    return choices


async def write_clipboard(text, *, console, cwd):
    data = text.encode("utf-8")
    if len(data) > 8_000_000:
        raise ValueError("response exceeds clipboard size limit")
    remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
    argv = None
    if not remote:
        if sys.platform == "darwin":
            argv = ["/usr/bin/pbcopy"]
        elif sys.platform == "win32":
            argv = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
                "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
            ]
    if argv:
        try:
            await run_owned(argv, data, cwd=cwd, output_limit=1024, timeout=3)
            return "Copied to clipboard."
        except (OSError, ValueError):
            pass
    if not console.is_terminal:
        raise ValueError("no supported clipboard backend or interactive terminal")
    # OSC 52 has no acknowledgement; do not claim that the terminal accepted it.
    sequence = "\x1b]52;c;" + base64.b64encode(data).decode("ascii") + "\x07"
    if os.environ.get("TMUX"):
        sequence = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    console.file.write(sequence)
    console.file.flush()
    return "Copy sent to terminal clipboard (requires terminal clipboard permission)."
