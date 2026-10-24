"""Native shared/exclusive byte-range locks for coordination handles."""

import ctypes as C
import msvcrt
from ctypes import wintypes as W


class Overlapped(C.Structure):
    _fields_ = [
        ("internal", C.c_size_t),
        ("internal_high", C.c_size_t),
        ("offset", W.DWORD),
        ("offset_high", W.DWORD),
        ("event", W.HANDLE),
    ]


def _api():
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [
        W.HANDLE,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        C.POINTER(Overlapped),
    ]
    kernel.LockFileEx.restype = W.BOOL
    kernel.UnlockFileEx.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = W.BOOL
    return kernel


def try_lock(descriptor: int, *, shared: bool) -> bool:
    # CRT LK_NBRLCK is exclusive too; LockFileEx preserves actual reader sharing.
    pending = Overlapped()
    if _api().LockFileEx(
        msvcrt.get_osfhandle(descriptor), 1 | (0 if shared else 2), 0, 1, 0, C.byref(pending)
    ):
        return True
    error = C.get_last_error()
    if error == 33:  # ERROR_LOCK_VIOLATION, not an ACL/access failure.
        return False
    raise C.WinError(error)


def unlock(descriptor: int) -> None:
    pending = Overlapped()
    if not _api().UnlockFileEx(msvcrt.get_osfhandle(descriptor), 0, 1, 0, C.byref(pending)):
        raise C.WinError(C.get_last_error())
