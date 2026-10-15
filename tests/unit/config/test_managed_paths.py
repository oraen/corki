"""System authority must not follow user environment or workspace redirection."""

import ctypes
from pathlib import Path

import pytest

from corki.config import managed_paths


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_unix_system_path_ignores_user_roots(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(managed_paths.sys, "platform", platform)
    monkeypatch.setenv("CORKI_HOME", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert managed_paths.system_requirements_path() == Path("/etc/corki/requirements.toml")


@pytest.mark.parametrize("failure", [False, True])
def test_windows_uses_known_folder_or_warned_fixed_fallback(monkeypatch, caplog, failure):
    monkeypatch.setattr(managed_paths.sys, "platform", "win32")
    monkeypatch.setenv("ProgramData", "Z:/user-controlled")

    def query():
        if failure:
            raise OSError("known-folder failure")
        return Path("D:/SystemData")

    monkeypatch.setattr(managed_paths, "_windows_program_data", query)
    expected = Path("C:/ProgramData" if failure else "D:/SystemData")
    assert managed_paths.system_requirements_path() == expected / "Corki/requirements.toml"
    assert ("using default path" in caplog.text) == failure


@pytest.mark.parametrize("status,pointer", [(0, True), (-1, True), (0, False)])
def test_known_folder_pointer_is_freed_on_success_and_error(monkeypatch, status, pointer):
    calls, frees = [], []
    value = ctypes.create_unicode_buffer("D:/SystemData")

    class Function:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def query(folder_id, flags, token, output):
        calls.append((ctypes.string_at(folder_id, 16), flags, token))
        if pointer:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(value)
        return status

    class Shell:
        SHGetKnownFolderPath = Function(query)

    class Ole:
        CoTaskMemFree = Function(lambda output: frees.append(output.value))

    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name: Shell() if name == "shell32" else Ole(), raising=False
    )
    if status or not pointer:
        with pytest.raises(OSError, match="known-folder"):
            managed_paths._windows_program_data()
    else:
        assert managed_paths._windows_program_data() == Path("D:/SystemData")
    assert len(calls) == 1
    assert calls[0][1:] == (0, None)
    assert frees == ([ctypes.addressof(value)] if pointer else [])
