import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from corki import shell
from corki.config import CorkiSettings
from corki.core.step_shell import step_shell
from corki.shell import Shell, ShellType
from corki.tools.errors import FatalToolError


@pytest.mark.parametrize(
    "path,kind",
    [
        ("zsh", ShellType.ZSH),
        ("/bin/bash", ShellType.BASH),
        ("sh", ShellType.SH),
        ("pwsh.exe", ShellType.POWERSHELL),
        ("powershell", ShellType.POWERSHELL),
        ("cmd.exe", ShellType.CMD),
        ("bash.extra.exe", ShellType.BASH),
        ("bash.", ShellType.BASH),
        ("fish", None),
        ("BASH", None),
        (".bash", None),
        ("", None),
    ],
)
def test_source_shell_type_detection(path, kind):
    assert shell.detect_shell_type(path) is kind


@pytest.mark.parametrize(
    "kind,login,args",
    [
        (ShellType.ZSH, True, ["-lc"]),
        (ShellType.ZSH, False, ["-c"]),
        (ShellType.BASH, True, ["-lc"]),
        (ShellType.BASH, False, ["-c"]),
        (ShellType.SH, True, ["-lc"]),
        (ShellType.SH, False, ["-c"]),
        (ShellType.POWERSHELL, True, ["-Command"]),
        (ShellType.POWERSHELL, False, ["-NoProfile", "-Command"]),
        (ShellType.CMD, True, ["/c"]),
        (ShellType.CMD, False, ["/c"]),
    ],
)
def test_source_argv_preserves_shell_identity_and_command(kind, login, args):
    selected = Shell(kind, Path("/selected/shell"))
    command = "quoted ' argument; $literal"
    assert selected.exec_args(command, login=login) == [str(selected.path), *args, command]


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("darwin", [ShellType.ZSH, ShellType.BASH]),
        ("linux", [ShellType.BASH, ShellType.ZSH]),
        ("win32", [ShellType.POWERSHELL]),
    ],
)
def test_default_platform_order_and_ultimate_fallback(monkeypatch, platform, expected):
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(shell, "_user_shell_path", lambda: Path("/bin/fish"))
    attempts = []

    def missing(kind):
        attempts.append(kind)
        return None

    monkeypatch.setattr(shell, "get_shell", missing)
    result = shell.default_user_shell()
    assert attempts == expected
    assert result == (
        Shell(ShellType.CMD, Path("cmd.exe"))
        if platform == "win32"
        else Shell(ShellType.SH, Path("/bin/sh"))
    )


@pytest.mark.parametrize("user_exists,path_exists", [(True, True), (False, True), (False, False)])
def test_discovery_passwd_then_path_then_fallback(monkeypatch, user_exists, path_exists):
    monkeypatch.setattr(shell, "_user_shell_path", lambda: Path("/user/bash"))
    monkeypatch.setattr(
        shell,
        "_is_file",
        lambda p: user_exists if p == Path("/user/bash") else p == Path("/bin/bash"),
    )
    monkeypatch.setattr(shell.shutil, "which", lambda name: "/path/bash" if path_exists else None)
    expected = (
        Path("/user/bash")
        if user_exists
        else Path("/path/bash").absolute()
        if path_exists
        else Path("/bin/bash")
    )
    assert shell.get_shell(ShellType.BASH) == Shell(ShellType.BASH, expected)


def test_model_override_never_uses_supplied_executable_and_missing_type_falls_back(monkeypatch):
    monkeypatch.setattr(shell, "_user_shell_path", lambda: None)
    monkeypatch.setattr(shell, "_is_file", lambda path: str(path) == "/untrusted/bash")
    monkeypatch.setattr(
        shell.shutil, "which", lambda name: "/trusted/bash" if name == "bash" else None
    )
    assert shell.model_shell("/untrusted/bash") == Shell(
        ShellType.BASH, Path("/trusted/bash").absolute()
    )
    assert shell.model_shell("/untrusted/fish") == shell.fallback_shell()
    assert shell.model_shell("/unavailable/zsh") == shell.fallback_shell()


def test_relative_path_selection_is_bound_before_tool_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "_user_shell_path", lambda: None)
    monkeypatch.setattr(shell.shutil, "which", lambda name: "bin/bash")
    assert shell.get_shell(ShellType.BASH).path == tmp_path / "bin/bash"


def test_powershell_order_and_windows_type_path(monkeypatch):
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(shell, "_user_shell_path", lambda: None)
    monkeypatch.setattr(shell, "_is_file", lambda path: False)
    attempts = []

    def which(name):
        attempts.append(name)
        return "/host/powershell.exe" if name == "powershell" else None

    monkeypatch.setattr(shell.shutil, "which", which)
    assert shell.detect_shell_type(r"C:\untrusted\pwsh.exe") is ShellType.POWERSHELL
    assert shell.default_user_shell() == Shell(
        ShellType.POWERSHELL, Path("/host/powershell.exe").absolute()
    )
    assert attempts == ["pwsh", "powershell"]


@pytest.mark.parametrize("value", [None, 0, 1, "false", []])
def test_login_config_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="allow_login_shell"):
        CorkiSettings(working_directory=tmp_path, allow_login_shell=value)


def test_login_config_default_and_toml(tmp_path):
    assert CorkiSettings(working_directory=tmp_path).allow_login_shell is True
    config = tmp_path / "config.toml"
    config.write_text("[tools]\nallow_login_shell=false\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).allow_login_shell is False


@pytest.mark.parametrize("kind", list(ShellType))
@pytest.mark.parametrize("login", [False, True])
@pytest.mark.parametrize("tty", [False, True])
def test_physical_spawn_uses_typed_shell_argv(tmp_path, monkeypatch, kind, login, tty):
    from corki.tools.builtin import process

    captured = []
    selected = Shell(kind, tmp_path / "selected")
    sentinel = object()

    async def pipe(*argv, **kwargs):
        captured.append((list(argv), kwargs["cwd"], "pipe"))
        return sentinel

    async def pty(argv, **kwargs):
        captured.append((argv, kwargs["cwd"], "pty"))
        assert kwargs["slave_fd"] == 123
        return sentinel

    # Isolate the branch selector without changing the host's os.name/Path class.
    monkeypatch.setattr(process, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(process.asyncio, "create_subprocess_exec", pipe)
    monkeypatch.setattr(process, "spawn_pty", pty)
    result = asyncio.run(
        process._spawn(
            "echo literal",
            cwd=tmp_path,
            shell=selected,
            login=login,
            stdin=123 if tty else None,
            stdout=123 if tty else None,
            stderr=123 if tty else None,
        )
    )
    assert result is sentinel
    assert captured == [
        (selected.exec_args("echo literal", login=login), tmp_path, "pty" if tty else "pipe")
    ]


def test_passwd_type_has_priority_over_platform_default(monkeypatch):
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(shell, "_user_shell_path", lambda: Path("/bin/sh"))
    calls = []

    def select(kind):
        calls.append(kind)
        return Shell(kind, Path("/chosen/sh"))

    monkeypatch.setattr(shell, "get_shell", select)
    assert shell.default_user_shell() == Shell(ShellType.SH, Path("/chosen/sh"))
    assert calls == [ShellType.SH]


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"version": True},
        {"version": 99, "kind": "bash", "path": "/bin/bash"},
        {"version": 1, "kind": "unknown", "path": "/bin/bash"},
        {"version": 1, "kind": "bash", "path": ""},
        {"version": 1, "kind": "bash", "path": "bad\0path"},
        {"version": 1, "kind": "bash", "path": 123},
    ],
)
def test_corrupt_prepared_shell_never_silently_reselects(value):
    with pytest.raises(FatalToolError, match="invalid prepared shell snapshot"):
        step_shell({"shell_snapshot": value}, Shell(ShellType.ZSH, Path("/session/zsh")))


def test_step_shell_restores_exact_path_and_legacy_uses_explicit_session_fallback():
    current = Shell(ShellType.BASH, Path("/current/bash"))
    assert step_shell(
        {"shell_snapshot": {"version": 1, "kind": "zsh", "path": "/old/zsh"}}, current
    ) == Shell(ShellType.ZSH, Path("/old/zsh"))
    assert step_shell({}, current) is current
