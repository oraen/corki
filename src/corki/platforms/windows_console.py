"""Console signal mode while no prompt-toolkit reader owns Ctrl+C."""

import ctypes
from ctypes import wintypes


def enable_console_interrupts(terminal_input) -> None:
    """Keep raw typeahead, but let the host receive interrupts between readers.

    The enclosing raw_mode owns restoration. A nested prompt-toolkit raw_mode
    disables this bit again while its key bindings own Ctrl+C, then restores it.
    Pipe and dummy inputs are not consoles and must not change process stdin.
    """
    from prompt_toolkit.input.win32 import Win32Input

    if not isinstance(terminal_input, Win32Input):
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetConsoleMode.restype = wintypes.BOOL
    mode = wintypes.DWORD()
    if not kernel.GetConsoleMode(terminal_input.handle, ctypes.byref(mode)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.SetConsoleMode(terminal_input.handle, mode.value | 0x0001):
        raise ctypes.WinError(ctypes.get_last_error())
