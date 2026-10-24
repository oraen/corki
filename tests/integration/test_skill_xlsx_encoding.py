"""Standalone helpers keep UTF-8 stdin/stdout even under an ANSI host locale."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_spreadsheet_pipes_ignore_legacy_host_encoding(tmp_path):
    pytest.importorskip("openpyxl")
    scripts = Path(__file__).resolve().parents[2] / "skills/xlsx/scripts"
    destination = tmp_path / "中文 workbook.xlsx"
    environment = dict(os.environ, PYTHONIOENCODING="ascii", PYTHONUTF8="0")

    def run(script, *args, content=None):
        result = subprocess.run(
            [sys.executable, "-X", "utf8=0", str(scripts / script), *map(str, args)],
            input=content, capture_output=True, cwd=tmp_path, env=environment,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        return result.stdout

    spec = {"sheets": [{"name": "数据", "rows": [["城市", "数量"], ["北京", 2]]}]}
    created = json.loads(run("xlsx_create.py", "-", destination,
                             content=json.dumps(spec, ensure_ascii=False).encode()))
    assert created["sheets"] == ["数据"]
    result = json.loads(run("xlsx_read.py", destination, "--json"))
    assert result["rows"] == spec["sheets"][0]["rows"]
    csv_bytes = run("xlsx_read.py", destination, "--csv")
    assert csv_bytes == "城市,数量\r\n北京,2\r\n".encode()
    run("xlsx_edit.py", destination, "--set", "A2=上海")
    assert json.loads(run("xlsx_read.py", destination, "--json"))["rows"][1][0] == "上海"
    destination.unlink()
