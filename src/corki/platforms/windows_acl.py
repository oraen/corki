"""Private Windows file ACLs for local session data, never a chmod substitute."""

import ctypes as C
import os
from contextlib import contextmanager


class WindowsPrivateACL:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows file ACLs require Windows")
        self.api = C.WinDLL("advapi32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        pointer = C.c_void_p
        declarations = {
            "OpenProcessToken": ([pointer, C.c_uint32, C.POINTER(pointer)], C.c_int),
            "GetTokenInformation": (
                [pointer, C.c_int, pointer, C.c_uint32, C.POINTER(C.c_uint32)], C.c_int
            ),
            "ConvertSidToStringSidW": ([pointer, C.POINTER(pointer)], C.c_int),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
                [C.c_wchar_p, C.c_uint32, C.POINTER(pointer), pointer], C.c_int
            ),
            "GetSecurityInfo": (
                [pointer, C.c_int, C.c_uint32, C.POINTER(pointer), pointer,
                 C.POINTER(pointer), pointer, C.POINTER(pointer)], C.c_uint32
            ),
            "GetAce": ([pointer, C.c_uint32, C.POINTER(pointer)], C.c_int),
            "IsValidSid": ([pointer], C.c_int),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.kernel.GetCurrentProcess.restype = pointer
        self.kernel.CloseHandle.argtypes = [pointer]
        self.kernel.CloseHandle.restype = C.c_int
        self.kernel.LocalFree.argtypes = [pointer]
        self.kernel.LocalFree.restype = pointer
        token = pointer()
        if not self.api.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, C.byref(token)):
            raise C.WinError(C.get_last_error())
        try:
            needed = C.c_uint32()
            self.api.GetTokenInformation(token, 1, None, 0, C.byref(needed))
            if not needed.value:
                raise C.WinError(C.get_last_error())
            buffer = C.create_string_buffer(needed.value)
            if not self.api.GetTokenInformation(
                token, 1, buffer, len(buffer), C.byref(needed)
            ):
                raise C.WinError(C.get_last_error())
            self.user_sid = self._sid(C.cast(buffer, C.POINTER(pointer))[0])
        finally:
            self.kernel.CloseHandle(token)

    def _sid(self, sid):
        if not sid or not self.api.IsValidSid(sid):
            raise OSError("invalid file security identifier")
        text = C.c_void_p()
        if not self.api.ConvertSidToStringSidW(sid, C.byref(text)):
            raise C.WinError(C.get_last_error())
        try:
            return C.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    @contextmanager
    def descriptor(self):
        """New entries belong to this user; no inherited broad access grants."""
        text = f"O:{self.user_sid}D:P(A;OICI;FA;;;{self.user_sid})(A;OICI;FA;;;SY)"
        descriptor = C.c_void_p()
        if not self.api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text, 1, C.byref(descriptor), None
        ):
            raise C.WinError(C.get_last_error())
        try:
            yield descriptor.value
        finally:
            self.kernel.LocalFree(descriptor)

    def verify(self, handle):
        """Require current ownership and grants limited to user/system/admins.

        Administrators are Windows' privileged analogue of root. Existing ACLs
        are inspected, never silently rewritten to make a file pass validation.
        """
        owner, acl, descriptor = C.c_void_p(), C.c_void_p(), C.c_void_p()
        error = self.api.GetSecurityInfo(
            handle, 1, 1 | 4, C.byref(owner), None,
            C.byref(acl), None, C.byref(descriptor)
        )
        if error:
            raise C.WinError(error)
        try:
            if self._sid(owner) != self.user_sid or not acl:
                raise OSError("transcript entry is not private")
            # ACL header: revision/reserved (2), size (2), ACE count (2), reserved (2).
            count = C.c_uint16.from_address(acl.value + 4).value
            allowed = {self.user_sid, "S-1-5-18", "S-1-5-32-544"}
            for index in range(count):
                ace = C.c_void_p()
                if not self.api.GetAce(acl, index, C.byref(ace)):
                    raise C.WinError(C.get_last_error())
                kind = C.c_ubyte.from_address(ace.value).value
                if kind == 1:  # ACCESS_DENIED_ACE cannot grant access.
                    continue
                # Reject unfamiliar grant forms, including callback/object ACEs.
                if kind != 0 or self._sid(ace.value + 8) not in allowed:
                    raise OSError("transcript entry grants access to another principal")
        finally:
            self.kernel.LocalFree(descriptor)
