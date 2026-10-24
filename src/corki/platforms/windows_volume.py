"""Codex-style Windows 10 retry from a verified, pinned physical volume root.

Only the DOS drive alias may be followed. The returned root must be a plain
local volume root, and every filesystem component below it stays no-follow.
"""

import ctypes as C
import re


def is_local_volume_root(name: str) -> bool:
    return re.fullmatch(r"\\Device\\HarddiskVolume[0-9]+\\", name, re.IGNORECASE) is not None


class AttributeTagInfo(C.Structure):
    _fields_ = [("attributes", C.c_uint32), ("tag", C.c_uint32)]


def open_relative_to_volume(
    files, name, *, access, disposition, directory, security_descriptor,
):
    # Same drive-only boundary as Codex exec-server/windows_volume_fallback.rs.
    prefix = "\\??\\"
    if (
        not name.startswith(prefix) or len(name) < 7
        or name[4] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        or name[5:7] != ":\\"
    ):
        raise OSError("no-follow retry requires a local disk drive path")
    root = files._open_nt(
        name[:7], access=0x80 | 0x20, directory=True,
        strict=False, reparse_point=True,
    )
    try:
        kernel = files.kernel
        kernel.GetFileInformationByHandleEx.argtypes = [
            C.c_void_p, C.c_int, C.c_void_p, C.c_uint32,
        ]
        kernel.GetFileInformationByHandleEx.restype = C.c_int
        info = AttributeTagInfo()
        if not kernel.GetFileInformationByHandleEx(root, 9, C.byref(info), C.sizeof(info)):
            raise C.WinError(C.get_last_error())
        if not info.attributes & 0x10 or info.attributes & 0x400:
            raise OSError("no-follow retry requires a plain volume directory")
        kernel.GetFinalPathNameByHandleW.argtypes = [
            C.c_void_p, C.c_wchar_p, C.c_uint32, C.c_uint32,
        ]
        kernel.GetFinalPathNameByHandleW.restype = C.c_uint32
        buffer = C.create_unicode_buffer(128)
        length = kernel.GetFinalPathNameByHandleW(root, buffer, len(buffer), 2)
        if not length:
            raise C.WinError(C.get_last_error())
        if length >= len(buffer) or not is_local_volume_root(buffer.value):
            raise OSError("drive must resolve directly to a local disk volume root")
        return files._open_nt(
            name[7:], root=root, access=access, disposition=disposition,
            directory=directory, security_descriptor=security_descriptor,
        )
    finally:
        files.close(root)
