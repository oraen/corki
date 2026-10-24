import os

import pytest

from corki.platforms.windows_program import prepare_command, resolve_program


def test_child_path_and_extensions_override_parent(tmp_path, monkeypatch):
    child = tmp_path / "child tools"
    child.mkdir()
    executable = child / "fixture.EXE"
    executable.touch()
    monkeypatch.setenv("PATH", str(tmp_path / "unrelated"))
    assert resolve_program("fixture", {"Path": str(child), "PathExt": ".EXE"}, tmp_path) == str(
        executable
    )


def test_relative_path_entries_are_relative_to_command_cwd(tmp_path):
    directory = tmp_path / "工具"
    directory.mkdir()
    script = directory / "fixture.CMD"
    script.touch()
    assert resolve_program("fixture", {"PATH": "工具", "PATHEXT": ".CMD"}, tmp_path) == str(script)


def test_configured_path_order_and_explicit_path(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for directory in (one, two):
        directory.mkdir()
        (directory / "fixture.EXE").touch()
    env = {"PATH": os.pathsep.join((str(one), str(two))), "PATHEXT": ".EXE"}
    assert resolve_program("fixture", env, tmp_path) == str(one / "fixture.EXE")
    assert resolve_program(str(two / "fixture.EXE"), env, tmp_path) == str(two / "fixture.EXE")


def test_batch_arguments_are_preserved_for_native_launcher(tmp_path, monkeypatch):
    from corki.execution import bundled

    script = tmp_path / "fixture.CMD"
    script.touch()
    compiler = tmp_path / "compiler.exe"
    monkeypatch.setattr(bundled, "bundled_compiler", lambda: compiler)
    arguments = ("fixture", "a b", '%literal%', 'quote"&argument', "中文")
    assert prepare_command(
        arguments, env={"PATH": str(tmp_path), "PATHEXT": ".CMD"}, cwd=tmp_path,
    ) == (str(compiler), "--corki-windows-program", str(script), *arguments[1:])


def test_missing_batch_helper_does_not_invoke_implicit_shell(tmp_path, monkeypatch):
    from corki.execution import bundled

    monkeypatch.setattr(bundled, "bundled_compiler", lambda: None)
    with pytest.raises(OSError, match="bundled Corki process helper"):
        prepare_command((str(tmp_path / "fixture.cmd"), "argument"), cwd=tmp_path)


def test_missing_executable_retains_os_error_handling(tmp_path):
    assert prepare_command(("missing", "argument"), env={}, cwd=tmp_path) == (
        "missing", "argument",
    )


def test_dotted_program_name_still_expands_pathext(tmp_path):
    script = tmp_path / "fixture.v1.CMD"
    script.touch()
    env = {"PATH": str(tmp_path), "PATHEXT": ".EXE;.CMD"}
    assert resolve_program("fixture.v1", env, tmp_path) == str(script)


def test_known_executable_suffix_is_not_appended_again(tmp_path):
    (tmp_path / "fixture.exe.CMD").touch()
    env = {"PATH": str(tmp_path), "PATHEXT": ".EXE;.CMD"}
    assert resolve_program("fixture.exe", env, tmp_path) == "fixture.exe"
