"""TTY-only, sanitized project title, owned by the CLI lifecycle."""

import unicodedata
from contextlib import contextmanager


@contextmanager
def project_title(console, directory):
    title = " ".join(
        "".join(
            char
            for char in (directory.name or str(directory))
            if char.isspace() or not unicodedata.category(char).startswith("C")
        ).split()
    )[:240]
    active = console.is_terminal and bool(title)
    if active:
        console.file.write(f"\x1b]0;{title}\x07")
        console.file.flush()
    try:
        yield
    finally:
        if active:
            console.file.write("\x1b]0;\x07")
            console.file.flush()
