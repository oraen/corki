"""System policy locations are host authority, not user environment settings."""

import ctypes
import logging
import sys
from pathlib import Path
from uuid import UUID


def _windows_program_data() -> Path:
    # FOLDERID_ProgramData, not the caller-editable ProgramData environment variable.
    folder_id = (ctypes.c_byte * 16).from_buffer_copy(
        UUID("62AB5D82-FDC1-4DC3-A9DD-070D1D495D97").bytes_le
    )
    shell = ctypes.WinDLL("shell32")
    ole = ctypes.WinDLL("ole32")
    query = shell.SHGetKnownFolderPath
    query.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    query.restype = ctypes.c_int32
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    output = ctypes.c_void_p()
    try:
        status = query(ctypes.byref(folder_id), 0, None, ctypes.byref(output))
        if status != 0 or not output.value:
            raise OSError(f"ProgramData known-folder query failed: HRESULT {status:#x}")
        return Path(ctypes.wstring_at(output))
    finally:
        if output.value:
            free(output)


def system_requirements_path() -> Path:
    """Use Corki's application namespace with native system-location semantics."""
    if sys.platform != "win32":
        return Path("/etc/corki/requirements.toml")
    try:
        program_data = _windows_program_data()
    except OSError as exc:
        # Native Codex also warns and falls back if the known-folder API fails.
        logging.getLogger(__name__).warning(
            "Failed to resolve ProgramData known folder; using default path: %s", exc
        )
        program_data = Path("C:/ProgramData")
    return program_data / "Corki" / "requirements.toml"
