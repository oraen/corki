"""Stable UTF-8 pipes for standalone skill helpers on every platform."""

import sys
from contextlib import suppress


def configure_stdio():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        # Embedders may supply streams without a reconfigure method.
        with suppress(AttributeError, OSError, ValueError):
            # Disable newline translation so csv.writer does not emit CRCRLF
            # on Windows. JSON supplied on stdin follows the same UTF-8 contract.
            stream.reconfigure(encoding="utf-8", newline="")
