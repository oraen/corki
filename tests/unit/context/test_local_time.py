from pathlib import Path
from types import SimpleNamespace

import pytest

from corki.context import local_time


@pytest.mark.parametrize(
    "prefix",
    ["/usr/share/zoneinfo/", "../usr/share/zoneinfo/", "/etc/zoneinfo/", "../etc/zoneinfo/"],
)
def test_linux_source_prefixes_without_canonicalization_or_tz_override(monkeypatch, prefix):
    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    monkeypatch.setattr(local_time.os, "readlink", lambda path: prefix + "Europe/Paris")
    assert local_time.timezone_name() == "Europe/Paris"


@pytest.mark.parametrize("name", ["Asia/Shanghai\n", "Europe/Paris \r\n", "", "  Etc/UTC"])
def test_linux_timezone_file_uses_source_trailing_only_whitespace(monkeypatch, name):
    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(local_time.os, "readlink", lambda path: "/unknown/path")
    monkeypatch.setattr(Path, "read_text", lambda path, **kwargs: name)
    assert local_time.timezone_name() == name.rstrip()


@pytest.mark.parametrize(
    "config, expected",
    [
        (
            "config system\n option timezone 'CST-8'\n option zonename 'Asia/Shanghai'",
            "Asia/Shanghai",
        ),
        ("config system\n option timezone 'CST-8'", "CST-8"),
        ("config system 'named'\n option zonename 'ignored'", "Etc/UTC"),
        ("config other\n option zonename 'ignored'", "Etc/UTC"),
        ("config system\n option zonename 'Europe/Paris' # comment", "Europe/Paris"),
        ("config system\n option timezone A\n option zonename 'unterminated", "Etc/UTC"),
        ("config system\n option timezone A\n option zonename B extra", "A"),
        ("config system\n option timezone A\n option other 'unterminated", "A"),
        ("config system\n option zonename 'x\\y'", "x\\y"),
        ("config system\n option timezone A\n option timezone B", "B"),
        ("config system\n option zonename 'A\u2028B'", "A\u2028B"),
    ],
)
def test_linux_openwrt_fallback_matches_source_tokens(monkeypatch, config, expected):
    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="linux"))

    def readlink(path):
        raise OSError("not a symlink")

    def read(path, **kwargs):
        if path == Path("/etc/timezone"):
            raise UnicodeError("not UTF8")
        assert path == Path("/etc/config/system")
        return config

    monkeypatch.setattr(local_time.os, "readlink", readlink)
    monkeypatch.setattr(Path, "read_text", read)
    assert local_time.timezone_name() == expected


@pytest.mark.parametrize("name", [b"Europe/Paris", b"", b"x" * 65, b"\xff"])
def test_darwin_refreshes_system_cache_and_releases_owned_reference(monkeypatch, name):
    calls = []

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    def convert(reference, buffer, size, encoding):
        assert reference == 20 and size == 65 and encoding == 0x08000100
        if len(name) >= size:
            return False
        buffer.value = name
        return True

    library = SimpleNamespace(
        CFTimeZoneResetSystem=Function(lambda: calls.append("reset")),
        CFTimeZoneCopySystem=Function(lambda: 10),
        CFTimeZoneGetName=Function(lambda reference: 20),
        CFStringGetCString=Function(convert),
        CFRelease=Function(lambda reference: calls.append(("release", reference))),
    )
    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(local_time.ctypes, "CDLL", lambda path: library)
    assert local_time.timezone_name() == ("Europe/Paris" if name == b"Europe/Paris" else "Etc/UTC")
    assert calls == ["reset", ("release", 10)]


@pytest.mark.parametrize("failure", [None, OSError, ValueError, UnicodeError, AttributeError])
def test_windows_native_resolver_preserves_fallback_without_starting_shell(monkeypatch, failure):
    from corki.platforms import windows_timezone

    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="win32"))

    def resolve():
        if failure:
            raise failure("timezone unavailable")
        return "America/Los_Angeles"

    def unexpected_process(*args, **kwargs):
        raise AssertionError("timezone resolution must not launch a subprocess")

    monkeypatch.setattr(windows_timezone, "timezone_name", resolve)
    monkeypatch.setattr(local_time.subprocess, "run", unexpected_process)
    assert local_time.timezone_name() == ("Etc/UTC" if failure else "America/Los_Angeles")


def test_unsupported_platform_uses_explicit_fallback(monkeypatch):
    monkeypatch.setattr(local_time, "sys", SimpleNamespace(platform="unimplemented"))
    assert local_time.timezone_name() == "Etc/UTC"
