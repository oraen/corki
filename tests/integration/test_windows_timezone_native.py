"""Native WinRT calls; no PowerShell, registry changes or timezone changes."""

import ctypes as C
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from corki.context import local_time
from corki.platforms import windows_timezone

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows WinRT")


def test_actual_timezone_matches_os_offset_without_shell(monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError("timezone lookup must not create a subprocess")

    monkeypatch.setattr(local_time.subprocess, "run", no_process)
    name = windows_timezone.timezone_name()
    assert local_time.timezone_name() == name
    now = datetime.now(UTC)
    assert now.astimezone(ZoneInfo(name)).utcoffset() == now.astimezone().utcoffset()


@pytest.mark.parametrize("apartment", [None, 2, 0], ids=["fresh", "STA", "MTA"])
def test_actual_caller_apartment_survives_repeated_lookups(apartment):
    def run():
        ole = C.WinDLL("ole32", winmode=0x800)
        ole.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint32]
        ole.CoInitializeEx.restype = C.c_int32
        ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
        if apartment is not None:
            assert ole.CoInitializeEx(None, apartment) in (0, 1)
        try:
            names = {windows_timezone.timezone_name() for _ in range(10)}
            assert len(names) == 1
            flag = 2 if apartment is None else apartment
            status = ole.CoInitializeEx(None, flag)
            assert status >= 0, f"Caller apartment changed: HRESULT 0x{status & 0xffffffff:08x}"
            try:
                assert status == (0 if apartment is None else 1)
            finally:
                ole.CoUninitialize()
        finally:
            if apartment is not None:
                ole.CoUninitialize()

    # A fresh thread makes its initial COM state independent of pytest/plugins.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(run).result(timeout=60)


def test_actual_concurrent_lookups():
    expected = windows_timezone.timezone_name()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(lambda _: windows_timezone.timezone_name(), range(40))) == {expected}
