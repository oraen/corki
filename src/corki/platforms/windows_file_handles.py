"""Windows no-follow handles, using the pinned Codex executor's NT open semantics.

This is a low-level primitive, not an ACL policy. Callers must validate ownership
and permissions before using private data. Never fall back to an ordinary open.
"""

import ctypes as C
import os
from pathlib import PureWindowsPath


def nt_path(path: str) -> str:
    value = PureWindowsPath(path)
    if "\0" in path or not value.is_absolute() or ".." in value.parts:
        raise ValueError("no-follow operations require an absolute disk or UNC path")
    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        result = "\\??\\UNC\\" + text[8:]
    elif text.startswith("\\\\?\\") and len(value.drive) == 6 and value.drive[-1] == ":":
        result = "\\??\\" + text[4:]
    elif len(value.drive) == 2 and value.drive[1] == ":" and value.drive[0].isalpha():
        result = "\\??\\" + text
    elif text.startswith("\\\\") and not text.startswith(("\\\\.\\", "\\\\?\\")):
        result = "\\??\\UNC\\" + text[2:]
    else:
        raise ValueError("device namespace paths are not supported")
    # UNICODE_STRING stores byte lengths in 16-bit fields, including room for NUL.
    if len(result.encode("utf-16-le")) + 2 > 65535:
        raise ValueError("filesystem path is too long")
    return result


class UnicodeString(C.Structure):
    _fields_ = [("length", C.c_uint16), ("maximum_length", C.c_uint16), ("buffer", C.c_void_p)]


class ObjectAttributes(C.Structure):
    _fields_ = [
        ("length", C.c_uint32),
        ("root", C.c_void_p),
        ("name", C.POINTER(UnicodeString)),
        ("attributes", C.c_uint32),
        ("security_descriptor", C.c_void_p),
        ("security_qos", C.c_void_p),
    ]


class SecurityQoS(C.Structure):
    _fields_ = [
        ("length", C.c_uint32),
        ("level", C.c_uint32),
        ("tracking", C.c_ubyte),
        ("effective_only", C.c_ubyte),
    ]


class IOStatus(C.Structure):
    _fields_ = [("status", C.c_void_p), ("information", C.c_size_t)]


class WindowsFiles:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows file handles require Windows")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.ntdll = C.WinDLL("ntdll", use_last_error=True)
        self.ntdll.NtCreateFile.argtypes = [
            C.POINTER(C.c_void_p),
            C.c_uint32,
            C.POINTER(ObjectAttributes),
            C.POINTER(IOStatus),
            C.c_void_p,
            C.c_uint32,
            C.c_uint32,
            C.c_uint32,
            C.c_uint32,
            C.c_void_p,
            C.c_uint32,
        ]
        self.ntdll.NtCreateFile.restype = C.c_int32
        self.ntdll.RtlNtStatusToDosError.argtypes = [C.c_int32]
        self.ntdll.RtlNtStatusToDosError.restype = C.c_uint32
        self.kernel.CloseHandle.argtypes = [C.c_void_p]
        self.kernel.CloseHandle.restype = C.c_int
        self.kernel.GetFileType.argtypes = [C.c_void_p]
        self.kernel.GetFileType.restype = C.c_uint32
        self.kernel.SetFileInformationByHandle.argtypes = [
            C.c_void_p,
            C.c_int,
            C.c_void_p,
            C.c_uint32,
        ]
        self.kernel.SetFileInformationByHandle.restype = C.c_int

    def open(
        self, path, *, access, disposition=1, directory=False, root=None, security_descriptor=None
    ):
        """Open an absolute path or a single child name relative to a pinned handle.

        Dispositions: FILE_OPEN=1, FILE_CREATE=2, FILE_OPEN_IF=3. NT rejects all
        reparse points; the descriptor supplies an explicit ACL for new entries.
        """
        if root is None:
            text = nt_path(str(path))
        else:
            text = str(path)
            if not text or text in {".", ".."} or any(c in text for c in "\\/:\0"):
                raise ValueError("relative handle opens require a single child name")
        try:
            return self._open_nt(
                text, access=access, disposition=disposition, directory=directory,
                root=root, security_descriptor=security_descriptor,
            )
        except OSError as error:
            # Windows 10 rejects the Object Manager DOS drive alias as well as
            # filesystem reparses. Match Codex's verified-volume retry: never
            # canonicalize the filesystem path or relax checks below the root.
            if root is not None or getattr(error, "winerror", None) != 4395:
                raise
            from corki.platforms.windows_volume import open_relative_to_volume

            return open_relative_to_volume(
                self, text, access=access, disposition=disposition,
                directory=directory, security_descriptor=security_descriptor,
            )

    def _open_nt(
        self, text, *, access, disposition=1, directory=False, root=None,
        security_descriptor=None, strict=True, reparse_point=False,
    ):
        """Internal NT names; public callers must use the validated open method."""
        encoded = text.encode("utf-16-le")
        if len(encoded) + 2 > 65535:
            raise ValueError("filesystem path is too long")
        buffer = C.create_string_buffer(encoded + b"\0\0")
        name = UnicodeString(len(encoded), len(encoded) + 2, C.addressof(buffer))
        qos = SecurityQoS(C.sizeof(SecurityQoS), 1, 0, 1)  # SecurityIdentification
        attributes = ObjectAttributes(
            C.sizeof(ObjectAttributes),
            root,
            C.pointer(name),
            0x40 | (0x1000 if strict else 0),
            security_descriptor,
            C.addressof(qos),  # CASE_INSENSITIVE | DONT_REPARSE
        )
        status_block, handle = IOStatus(), C.c_void_p()
        status = self.ntdll.NtCreateFile(
            C.byref(handle),
            access | 0x100000,
            C.byref(attributes),
            C.byref(status_block),
            None,
            0x80,
            7,
            disposition,
            (1 if directory else 0x40) | 0x20 | (0x200000 if reparse_point else 0),
            None,
            0,
        )
        if status < 0:
            raise C.WinError(self.ntdll.RtlNtStatusToDosError(status))
        if not handle.value or handle.value == C.c_void_p(-1).value:
            raise OSError("NT open returned an invalid handle")
        return handle.value

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise C.WinError(C.get_last_error())

    def delete(self, handle):
        """Delete the opened entry itself, never resolve its path a second time."""
        disposition = C.c_ubyte(1)
        if not self.kernel.SetFileInformationByHandle(
            handle, 4, C.byref(disposition), C.sizeof(disposition)
        ):  # FileDispositionInfo
            raise C.WinError(C.get_last_error())
