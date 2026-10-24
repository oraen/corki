"""Read the live Windows IANA zone through WinRT, as iana-time-zone does.

Calendar.GetTimeZone is independent of shell startup, execution policy and
Windows-to-IANA mapping tables. Only DLL bindings are cached, never the zone.
"""

import ctypes as C
from functools import cache
from uuid import UUID


class _GUID(C.Structure):
    _fields_ = [
        ("a", C.c_uint32), ("b", C.c_uint16), ("c", C.c_uint16), ("d", C.c_ubyte * 8),
    ]


# ITimeZoneOnCalendar, also used by iana-time-zone 0.1.65's generated bindings.
_TIMEZONE_IID = _GUID.from_buffer_copy(
    UUID("bb3c25e5-46cf-4317-a3f5-02621ad54478").bytes_le,
)


@cache
def _api():
    # Load only the system DLL, without consulting cwd or PATH.
    api = C.WinDLL("combase", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    signatures = {
        "RoInitialize": ([C.c_uint32], C.c_int32),
        "RoUninitialize": ([], None),
        "WindowsCreateString": ([C.c_wchar_p, C.c_uint32, C.POINTER(C.c_void_p)], C.c_int32),
        "WindowsDeleteString": ([C.c_void_p], C.c_int32),
        "WindowsGetStringRawBuffer": ([C.c_void_p, C.POINTER(C.c_uint32)], C.c_void_p),
        "RoActivateInstance": ([C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _check(result: int) -> None:
    value = result & 0xFFFFFFFF
    if value & 0x80000000:
        raise OSError(f"WinRT timezone call failed (HRESULT 0x{value:08x})")


def _method(pointer, index, result, arguments):
    if not pointer:
        raise OSError("WinRT timezone interface unavailable")
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(table[index])


def timezone_name() -> str:
    """Return the OS zone; callers retain the existing resolver-error fallback."""
    api = _api()
    initialized = False
    class_name, calendar, interface, value = (C.c_void_p() for _ in range(4))
    try:
        status = api.RoInitialize(1)  # RO_INIT_MULTITHREADED
        if status & 0xFFFFFFFF == 0x80010106:  # RPC_E_CHANGED_MODE: preserve an existing STA.
            status = api.RoInitialize(0)  # RO_INIT_SINGLETHREADED
        _check(status)
        initialized = True  # S_FALSE also owns one initialization reference.
        runtime_class = "Windows.Globalization.Calendar"
        _check(api.WindowsCreateString(runtime_class, len(runtime_class), C.byref(class_name)))
        _check(api.RoActivateInstance(class_name, C.byref(calendar)))
        query = _method(calendar, 0, C.c_int32, [C.POINTER(_GUID), C.POINTER(C.c_void_p)])
        _check(query(calendar, C.byref(_TIMEZONE_IID), C.byref(interface)))
        # IInspectable contributes six entries before GetTimeZone.
        get_timezone = _method(interface, 6, C.c_int32, [C.POINTER(C.c_void_p)])
        _check(get_timezone(interface, C.byref(value)))
        length = C.c_uint32()
        data = api.WindowsGetStringRawBuffer(value, C.byref(length))
        if not data or not length.value:
            raise ValueError("empty Windows timezone name")
        return C.wstring_at(data, length.value)
    finally:
        if value:
            api.WindowsDeleteString(value)
        # QueryInterface owns its own reference even if both addresses coincide.
        for pointer in (interface, calendar):
            if pointer:
                _method(pointer, 2, C.c_uint32, [])(pointer)
        if class_name:
            api.WindowsDeleteString(class_name)
        if initialized:
            api.RoUninitialize()
