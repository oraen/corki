"""WinRT ownership and apartment failure contracts, without loading a Windows DLL."""

import ctypes as C

import pytest

from corki.platforms import windows_timezone as module

_FAIL = -2147467259
_CHANGED_MODE = -2147417850


def store(pointer, value):
    C.cast(pointer, C.POINTER(C.c_void_p)).contents.value = value


class API:
    def __init__(self, failure=None, statuses=()):
        self.failure = failure
        self.statuses = list(statuses)
        self.initializations = []
        self.uninitializations = 0
        self.deleted = []
        self.released = []
        self.name = "America/Los_Angeles"

    def RoInitialize(self, mode):
        self.initializations.append(mode)
        return self.statuses.pop(0) if self.statuses else 0

    def RoUninitialize(self):
        self.uninitializations += 1

    def WindowsCreateString(self, name, length, out):
        assert name == "Windows.Globalization.Calendar" and length == len(name)
        if self.failure == "class":
            return _FAIL
        store(out, 10)
        return 0

    def WindowsDeleteString(self, value):
        self.deleted.append(value.value)
        return 0

    def RoActivateInstance(self, name, out):
        assert name.value == 10
        if self.failure == "activate":
            return _FAIL
        if self.failure != "null_calendar":
            store(out, 20)
        return 0

    def query(self, pointer, iid, out):
        assert pointer.value == 20
        if self.failure == "query":
            return _FAIL
        if self.failure != "null_interface":
            store(out, 30)
        return 0

    def get_timezone(self, pointer, out):
        assert pointer.value == 30
        if self.failure == "get":
            return _FAIL
        store(out, 40)
        return 0

    def WindowsGetStringRawBuffer(self, value, length):
        assert value.value == 40
        text = "" if self.failure == "empty" else self.name
        self.buffer = C.create_unicode_buffer(text)
        C.cast(length, C.POINTER(C.c_uint32)).contents.value = len(text)
        return None if self.failure == "buffer" else C.addressof(self.buffer)

    def method(self, pointer, index, result, arguments):
        if not pointer:
            raise OSError("WinRT timezone interface unavailable")
        if index == 2:
            return lambda value: self.released.append(value.value)
        return {(20, 0): self.query, (30, 6): self.get_timezone}[pointer.value, index]


def install(monkeypatch, api):
    monkeypatch.setattr(module, "_api", lambda: api)
    monkeypatch.setattr(module, "_method", api.method)


@pytest.mark.parametrize(
    "failure, deleted, released",
    [
        (None, [40, 10], [30, 20]),
        ("class", [], []),
        ("activate", [10], []),
        ("null_calendar", [10], []),
        ("query", [10], [20]),
        ("null_interface", [10], [20]),
        ("get", [10], [30, 20]),
        ("empty", [40, 10], [30, 20]),
        ("buffer", [40, 10], [30, 20]),
    ],
)
def test_owned_strings_and_references_released_on_each_failure(
    monkeypatch, failure, deleted, released,
):
    api = API(failure)
    install(monkeypatch, api)
    if failure:
        with pytest.raises((OSError, ValueError)):
            module.timezone_name()
    else:
        assert module.timezone_name() == api.name
    assert api.deleted == deleted
    assert api.released == released
    assert api.initializations == [1]
    assert api.uninitializations == 1


@pytest.mark.parametrize(
    "statuses, calls, owned",
    [([1], [1], True), ([_CHANGED_MODE, 1], [1, 0], True),
     ([_FAIL], [1], False), ([_CHANGED_MODE, _FAIL], [1, 0], False)],
)
def test_balance_only_successful_apartment_initialization(monkeypatch, statuses, calls, owned):
    api = API(statuses=statuses)
    install(monkeypatch, api)
    if owned:
        assert module.timezone_name() == api.name
    else:
        with pytest.raises(OSError, match="HRESULT"):
            module.timezone_name()
    assert api.initializations == calls
    assert api.uninitializations == int(owned)
    if not owned:
        assert api.deleted == api.released == []


def test_reads_new_system_value_instead_of_caching_zone(monkeypatch):
    api = API()
    install(monkeypatch, api)
    assert module.timezone_name() == "America/Los_Angeles"
    api.name = "Asia/Shanghai"
    assert module.timezone_name() == "Asia/Shanghai"
    assert api.uninitializations == 2


def test_missing_interface_is_rejected_before_native_dereference():
    with pytest.raises(OSError, match="interface unavailable"):
        module._method(C.c_void_p(), 6, C.c_int32, [C.POINTER(C.c_void_p)])
