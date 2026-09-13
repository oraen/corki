"""Signals restricted to one manager-owned POSIX process group."""

import ctypes
import errno
import os
import sys


def _group_members(group_id: int) -> tuple[int, ...]:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    list_members = library.proc_listpgrppids
    list_members.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    list_members.restype = ctypes.c_int
    capacity = 16
    while capacity <= 65536:
        members = (ctypes.c_int * capacity)()
        count = list_members(group_id, members, ctypes.sizeof(members))
        if count < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        if count < capacity:
            return tuple(members[:count])
        capacity *= 2
    raise OSError(errno.EOVERFLOW, "owned process group exceeds member-list limit")


def signal_owned_group(group_id: int, sig: int) -> bool:
    if type(group_id) is not int or not 0 < group_id <= 2**31 - 1:
        raise ValueError("invalid owned process group id")
    try:
        os.killpg(group_id, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if sys.platform != "darwin":
            raise
    delivered = False
    first_error = None
    for member in sorted(_group_members(group_id), key=lambda pid: pid == group_id):
        if member <= 0:
            continue
        try:
            # The member may have exited or moved groups since the enumeration.
            if os.getpgid(member) != group_id:
                continue
            os.kill(member, sig)
            delivered = True
        except ProcessLookupError:
            continue
        except OSError as error:
            if first_error is None:
                first_error = error
    if first_error is not None and not delivered:
        raise first_error
    return delivered
