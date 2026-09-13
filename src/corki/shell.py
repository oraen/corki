"""Selected host shells and source-mapped executable discovery, shared with context."""

import os
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath


class ShellType(StrEnum):
    ZSH = "zsh"
    BASH = "bash"
    POWERSHELL = "powershell"
    SH = "sh"
    CMD = "cmd"


@dataclass(frozen=True, slots=True)
class Shell:
    kind: ShellType
    path: Path

    @property
    def name(self) -> str:
        return self.kind.value

    def exec_args(self, command: str, *, login: bool) -> list[str]:
        if self.kind is ShellType.POWERSHELL:
            return [str(self.path), *([] if login else ["-NoProfile"]), "-Command", command]
        if self.kind is ShellType.CMD:
            return [str(self.path), "/c", command]
        return [str(self.path), "-lc" if login else "-c", command]


def detect_shell_type(path: str | Path) -> ShellType | None:
    """Recognize type by recursively stripping path/stem, without executing it."""
    name = str(path)
    while True:
        if name == "pwsh":
            return ShellType.POWERSHELL
        try:
            return ShellType(name)
        except ValueError:
            pass
        basename = (PureWindowsPath(name) if sys.platform == "win32" else PurePosixPath(name)).name
        stem = basename.rsplit(".", 1)[0] if "." in basename[1:] else basename
        if stem == name:
            return None
        name = stem


def _user_shell_path() -> Path | None:
    if sys.platform == "win32":
        return None
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_shell)
    except (ImportError, KeyError, OSError):
        return None


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _resolve(kind: ShellType, binary: str, fallbacks: tuple[str, ...]) -> Shell | None:
    user = _user_shell_path()
    if user is not None and detect_shell_type(user) is kind and _is_file(user):
        return Shell(kind, user)
    found = shutil.which(binary)
    if found is not None:
        # Relative PATH entries belong to discovery's cwd, not the later tool workdir.
        return Shell(kind, Path(found).absolute())
    for candidate in fallbacks:
        path = Path(candidate)
        if _is_file(path):
            return Shell(kind, path)
    return None


def get_shell(kind: ShellType) -> Shell | None:
    if kind is ShellType.POWERSHELL:
        windows = sys.platform == "win32"
        return _resolve(
            kind,
            "pwsh",
            (r"C:\Program Files\PowerShell\7\pwsh.exe" if windows else "/usr/local/bin/pwsh",),
        ) or _resolve(
            kind,
            "powershell",
            ((r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",) if windows else ()),
        )
    fallbacks = {
        ShellType.ZSH: ("/bin/zsh",),
        ShellType.BASH: ("/bin/bash", "/usr/bin/bash"),
        ShellType.SH: ("/bin/sh",),
        ShellType.CMD: (),
    }
    return _resolve(kind, kind.value, fallbacks[kind])


def fallback_shell() -> Shell:
    return (
        Shell(ShellType.CMD, Path("cmd.exe"))
        if sys.platform == "win32"
        else Shell(ShellType.SH, Path("/bin/sh"))
    )


def default_user_shell() -> Shell:
    if sys.platform == "win32":
        return get_shell(ShellType.POWERSHELL) or fallback_shell()
    user = _user_shell_path()
    kind = detect_shell_type(user) if user is not None else None
    selected = get_shell(kind) if kind is not None else None
    if selected is not None:
        return selected
    order = (
        (ShellType.ZSH, ShellType.BASH)
        if sys.platform == "darwin"
        else (ShellType.BASH, ShellType.ZSH)
    )
    for kind in order:
        selected = get_shell(kind)
        if selected is not None:
            return selected
    return fallback_shell()


def model_shell(path: str) -> Shell:
    """Model paths select a supported type, never authorize that exact executable."""
    kind = detect_shell_type(path)
    return (get_shell(kind) if kind is not None else None) or fallback_shell()
