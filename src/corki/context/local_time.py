"""System timezone names for Turn admission, independent of the process TZ offset."""

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def timezone_name() -> str:
    """Refresh the OS name once per Turn; resolver failures use Codex's Etc/UTC."""
    try:
        if sys.platform == "darwin":
            return _darwin_timezone()
        if sys.platform == "win32":
            return _windows_timezone()
        if sys.platform.startswith("linux"):
            return _linux_timezone()
    except (OSError, UnicodeError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    return "Etc/UTC"


def _linux_timezone() -> str:
    try:
        target = os.readlink("/etc/localtime")
        # Rust rejects non-UTF8 symlink names rather than retaining surrogates.
        target.encode("utf-8")
        for prefix in (
            "/usr/share/zoneinfo/",
            "../usr/share/zoneinfo/",
            "/etc/zoneinfo/",
            "../etc/zoneinfo/",
        ):
            if target.startswith(prefix):
                return target[len(prefix) :]
    except (OSError, UnicodeError):
        pass
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").rstrip(_WHITESPACE)
    except (OSError, UnicodeError):
        pass
    return _openwrt_timezone(Path("/etc/config/system").read_text(encoding="utf-8"))


def _words(line: str):
    """OpenWrt source token rules: quotes delimit a word; backslash is literal."""
    while line:
        line = line.lstrip(_WHITESPACE)
        if not line or line.startswith("#"):
            return
        if line[0] in {"'", '"'}:
            quote = line[0]
            end = line.find(quote, 1)
            if end < 0:
                raise ValueError("unterminated timezone config quote")
            yield line[1:end]
            line = line[end + 1 :]
        else:
            end = next((i for i, char in enumerate(line) if char in _WHITESPACE), len(line))
            yield line[:end]
            line = line[end:]


def _openwrt_timezone(config: str) -> str:
    in_system = False
    fallback = None
    for line in config.split("\n"):
        words = iter(_words(line))
        keyword = next(words, None)
        if keyword == "config":
            in_system = next(words, None) == "system" and next(words, None) is None
        elif in_system and keyword == "option":
            key = next(words, None)
            if key in {"zonename", "timezone"}:
                value = next(words, None)
                if value is not None and next(words, None) is None:
                    if key == "zonename":
                        return value
                    fallback = value
    if fallback is None:
        raise ValueError("system timezone not configured")
    return fallback


def _darwin_timezone() -> str:
    library = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    signatures = {
        "CFTimeZoneResetSystem": ([], None),
        "CFTimeZoneCopySystem": ([], ctypes.c_void_p),
        "CFTimeZoneGetName": ([ctypes.c_void_p], ctypes.c_void_p),
        "CFStringGetCString": (
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32],
            ctypes.c_bool,
        ),
        "CFRelease": ([ctypes.c_void_p], None),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    library.CFTimeZoneResetSystem()
    zone = library.CFTimeZoneCopySystem()
    if not zone:
        raise OSError("system timezone unavailable")
    try:
        name = library.CFTimeZoneGetName(zone)
        buffer = ctypes.create_string_buffer(65)  # Source permits at most64 UTF-8 bytes.
        if not name or not library.CFStringGetCString(name, buffer, len(buffer), 0x08000100):
            raise ValueError("system timezone name unavailable")
        if not buffer.value:
            raise ValueError("empty system timezone name")
        return buffer.value.decode("utf-8")
    finally:
        library.CFRelease(zone)


def _windows_timezone() -> str:
    # Same WinRT API as iana-time-zone. A fixed, bounded PowerShell adapter avoids
    # adding an ABI-specific COM implementation or a global Windows→IANA mapping.
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    executable = system / "System32/WindowsPowerShell/v1.0/powershell.exe"
    shell = str(executable) if executable.is_file() else shutil.which("powershell.exe")
    if shell is None:
        raise OSError("Windows timezone adapter unavailable")
    result = subprocess.run(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Windows.Globalization.Calendar,Windows.Globalization,ContentType=WindowsRuntime]"
            "::new().GetTimeZone()",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=True,
        encoding="utf-8",
    )
    name = result.stdout.strip()
    if not name:
        raise ValueError("empty Windows timezone name")
    return name
