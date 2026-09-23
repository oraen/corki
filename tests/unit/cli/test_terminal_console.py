import os
from io import StringIO

from rich.console import ConsoleDimensions

from corki.cli.terminal_console import TerminalConsole


def test_live_geometry_wins_over_inherited_fixed_dimensions(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")

    class Output(StringIO):
        def fileno(self):
            return 123

        def isatty(self):
            return True

    size = os.terminal_size((40, 30))

    def terminal_size(fd):
        assert fd == 123
        return size

    monkeypatch.setattr(os, "get_terminal_size", terminal_size)
    console = TerminalConsole(file=Output())
    assert console.size == ConsoleDimensions(40, 30)
    size = os.terminal_size((100, 35))
    assert console.size == ConsoleDimensions(100, 35)
    assert os.environ["COLUMNS"] == "80"
    assert os.environ["LINES"] == "24"


def test_redirected_output_keeps_rich_dimensions(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    console = TerminalConsole(file=StringIO(), force_terminal=False)
    assert console.size == ConsoleDimensions(80, 24)
