"""Preserve typeahead between readers without disabling interrupt signals."""

import os
from contextlib import contextmanager


@contextmanager
def between_readers_mode(terminal_input):
    # Each prompt-toolkit application nests its own raw mode, disabling ISIG
    # while its key handler owns Ctrl+C. In gaps, asyncio's signal handler owns
    # it instead. Keep all other raw input flags, notably ICRNL, unchanged.
    with terminal_input.raw_mode():
        if os.name == "posix":
            import termios

            try:
                descriptor = terminal_input.fileno()
            except (OSError, NotImplementedError):
                descriptor = None  # DummyInput / non-terminal input adapter.
            if descriptor is not None and os.isatty(descriptor):
                attributes = termios.tcgetattr(descriptor)
                attributes[3] |= termios.ISIG | termios.NOFLSH
                termios.tcsetattr(descriptor, termios.TCSANOW, attributes)
        yield
    # The enclosing raw_mode restores the complete original terminal state,
    # including signal and flush flags, on exceptions and cancellation too.
