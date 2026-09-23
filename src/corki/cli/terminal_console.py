"""Live terminal geometry, independent of inherited COLUMNS/LINES hints."""

import os

from rich.console import Console, ConsoleDimensions


class TerminalConsole(Console):
    """Use the actual output terminal for interactive layout and resize detection.

    Rich treats inherited COLUMNS/LINES as fixed dimensions. Those hints can be
    stale after resize and disagree with the composer's physical terminal size.
    Redirected output and streams without a terminal retain Rich's fallback.
    """

    @property
    def size(self) -> ConsoleDimensions:
        if self.is_terminal:
            try:
                width, height = os.get_terminal_size(self.file.fileno())
                if width > 0 and height > 0:
                    return ConsoleDimensions(width, height)
            except (AttributeError, OSError, ValueError):
                pass
        return super().size
