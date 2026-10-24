"""Standalone spreadsheet skill must preserve the native process environment."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def recalc():
    path = Path(__file__).resolve().parents[2] / "skills/xlsx/scripts/xlsx_recalc.py"
    spec = importlib.util.spec_from_file_location("recalc_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conversion_preserves_environment_and_uses_private_profile(recalc, tmp_path, monkeypatch):
    source = tmp_path / "中文 workbook.xlsx"
    source.write_bytes(b"original")
    destination = tmp_path / "result.xlsx"
    monkeypatch.setattr(recalc.shutil, "which", lambda _: "soffice")
    monkeypatch.setattr(recalc, "count_cached", lambda _: (1, 1))
    observed = []

    def convert(argv, **kwargs):
        assert "env" not in kwargs  # Includes SystemRoot, TEMP, PATH and locale.
        assert kwargs["timeout"] == 12
        assert argv[-1] == str(source)
        output = Path(argv[argv.index("--outdir") + 1])
        profile = output / "profile"
        assert argv[1] == "-env:UserInstallation=" + profile.as_uri()
        assert output != source.parent
        (output / source.name).write_bytes(b"converted")
        observed.append(output)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(recalc.subprocess, "run", convert)
    assert recalc.main([str(source), "--out", str(destination), "--timeout", "12"]) == 0
    assert source.read_bytes() == b"original"
    assert destination.read_bytes() == b"converted"
    assert observed and not observed[0].exists()


@pytest.mark.parametrize("fail_second", [False, True])
def test_workbooks_close_before_temporary_output_is_removed(recalc, monkeypatch, fail_second):
    closed = []
    calls = []

    def load(path, *, data_only):
        calls.append(data_only)
        if data_only and fail_second:
            raise OSError("fixture read failure")
        return SimpleNamespace(sheetnames=[], close=lambda: closed.append(data_only))

    monkeypatch.setitem(sys.modules, "openpyxl", SimpleNamespace(load_workbook=load))
    if fail_second:
        with pytest.raises(OSError, match="fixture read failure"):
            recalc.count_cached("fixture.xlsx")
        assert closed == [False]
    else:
        assert recalc.count_cached("fixture.xlsx") == (0, 0)
        assert closed == [True, False]
    assert calls == [False, True]
