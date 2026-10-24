"""Private configuration files, including host-selected symlink locations."""

import ctypes as C
import os
import secrets
from pathlib import Path

from corki.platforms.windows_acl import WindowsPrivateACL


class SecurityAttributes(C.Structure):
    _fields_ = [("length", C.c_uint32), ("descriptor", C.c_void_p), ("inherit", C.c_int)]


class PrivateFiles:
    def __init__(self):
        self.acl = WindowsPrivateACL()
        self.kernel = self.acl.kernel
        self.kernel.CreateFileW.argtypes = [
            C.c_wchar_p, C.c_uint32, C.c_uint32, C.POINTER(SecurityAttributes),
            C.c_uint32, C.c_uint32, C.c_void_p,
        ]
        self.kernel.CreateFileW.restype = C.c_void_p

    def open(self, path, *, create=False, descriptor=None):
        attributes = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
        name = os.path.abspath(path)
        if not name.startswith("\\\\?\\"):
            name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
        # Configuration paths are explicitly host-selected. Follow their parent
        # junctions/symlinks, unlike the untrusted transcript-entry API. CREATE_NEW
        # still rejects an existing final entry, including a pre-planted symlink.
        handle = self.kernel.CreateFileW(
            name,
            0xC0020000 if create else 0x60000,  # read/write + READ_CONTROL, or WRITE_DAC
            1 | 2 | 4, C.byref(attributes) if descriptor else None,
            1 if create else 3, 0x80, None,  # CREATE_NEW / OPEN_EXISTING
        )
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        return handle


def private_temporary_file(directory: Path, *, prefix: str) -> tuple[int, str]:
    """Create with a protected ACL before any configuration content is written."""
    import msvcrt

    files = PrivateFiles()
    for _ in range(100):
        path = directory / (prefix + secrets.token_hex(16))
        try:
            with files.acl.descriptor() as descriptor:
                handle = files.open(path, create=True, descriptor=descriptor)
        except FileExistsError:
            continue
        try:
            files.acl.verify(handle)
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            files.kernel.CloseHandle(handle)
            path.unlink(missing_ok=True)
            raise
        return descriptor, str(path)
    raise FileExistsError("could not create a private configuration temporary file")


def restrict_file(path: Path) -> None:
    """Apply the user/system-only DACL to an existing host-selected config target."""
    files = PrivateFiles()
    api = files.acl.api
    api.GetSecurityDescriptorDacl.argtypes = [
        C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.POINTER(C.c_int),
    ]
    api.GetSecurityDescriptorDacl.restype = C.c_int
    api.SetSecurityInfo.argtypes = [
        C.c_void_p, C.c_int, C.c_uint32, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p,
    ]
    api.SetSecurityInfo.restype = C.c_uint32
    handle = files.open(path)
    try:
        with files.acl.descriptor() as descriptor:
            present, defaulted, acl = C.c_int(), C.c_int(), C.c_void_p()
            if not api.GetSecurityDescriptorDacl(
                descriptor, C.byref(present), C.byref(acl), C.byref(defaulted)
            ):
                raise C.WinError(C.get_last_error())
            if not present.value or not acl:
                raise OSError("private configuration ACL is missing")
            error = api.SetSecurityInfo(handle, 1, 4 | 0x80000000, None, None, acl, None)
            if error:
                raise C.WinError(error)
    finally:
        files.kernel.CloseHandle(handle)
