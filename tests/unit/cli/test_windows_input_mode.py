"""Real Windows console modes, isolated from the pytest runner's terminal."""

import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires a Windows console")


def test_console_interrupt_gaps_and_nested_reader_restore(tmp_path):
    result = tmp_path / "console-result.txt"
    script = textwrap.dedent(r'''
        import ctypes as C
        import sys
        from ctypes import wintypes as W
        from pathlib import Path
        from prompt_toolkit.input.win32 import Win32Input
        from corki.cli.terminal_input_mode import between_readers_mode

        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.FreeConsole()
        assert kernel.AllocConsole(), C.get_last_error()
        kernel.CreateFileW.argtypes = [
            W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE
        ]
        kernel.CreateFileW.restype = W.HANDLE
        kernel.SetStdHandle.argtypes = [W.DWORD,W.HANDLE]
        kernel.GetConsoleMode.argtypes = [W.HANDLE,C.POINTER(W.DWORD)]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.CreateFileW("CONIN$",0xC0000000,3,None,3,0,None)
        assert handle != C.c_void_p(-1).value
        assert kernel.SetStdHandle(W.DWORD(-10),handle)
        terminal = Win32Input()
        def mode():
            value = W.DWORD()
            assert kernel.GetConsoleMode(terminal.handle,C.byref(value))
            return value.value
        try:
            original = mode()
            for fail in (False,True):
                try:
                    with between_readers_mode(terminal):
                        gap = mode()
                        assert gap & 1, "Ctrl+C must signal while no reader is active"
                        assert not gap & 6, "typeahead must remain noncanonical and unechoed"
                        with terminal.raw_mode():
                            assert not mode() & 1, "active reader must own Ctrl+C"
                        assert mode() == gap
                        if fail:
                            raise ValueError("fixture")
                except ValueError:
                    assert fail
                assert mode() == original, "complete console mode must be restored"
            Path(sys.argv[1]).write_text("PASS",encoding="utf-8")
        finally:
            terminal.close()
            kernel.CloseHandle(handle)
            kernel.FreeConsole()
    ''')
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(result)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert result.read_text(encoding="utf-8") == "PASS"


def test_pipe_input_does_not_modify_console_mode():
    from prompt_toolkit.input import create_pipe_input

    from corki.cli.terminal_input_mode import between_readers_mode

    with create_pipe_input() as terminal, between_readers_mode(terminal):
        terminal.send_text("fixture")
        assert "".join(key.data for key in terminal.read_keys()) == "fixture"
