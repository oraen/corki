import json
import os
import runpy
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bundle():
    from corki.cli.native_file_search import executable

    binary = executable()
    if binary is None:
        pytest.skip("build the local file search bundle first")
    return binary.parent


def test_installed_bundle_matches_source_platform_and_licenses(bundle):
    api = runpy.run_path(str(ROOT / "native/file_search/artifact.py"))
    manifest = api["verify"](ROOT, bundle)
    assert manifest["version"] == 1
    notices = (bundle / "THIRD_PARTY.txt").read_text()
    assert "nucleo-matcher 0.3.1" in notices and "ignore 0.4.25" in notices
    assert "Permission is hereby granted" in notices
    assert "Rust Standard Library" in (bundle / "RUST_COPYRIGHT.html").read_text()


@pytest.mark.parametrize("asset", ["corki-file-search", "THIRD_PARTY.txt", "RUST_COPYRIGHT.html"])
def test_damaged_asset_rejected_before_packaging(bundle, tmp_path, asset):
    target = tmp_path / "bundle"
    shutil.copytree(bundle, target)
    with (target / asset).open("ab") as stream:
        stream.write(b"damage")
    api = runpy.run_path(str(ROOT / "native/file_search/artifact.py"))
    with pytest.raises(ValueError, match="checksum"):
        api["verify"](ROOT, target)


def test_build_refuses_existing_destination_before_starting_cargo(tmp_path):
    (tmp_path / "keep").write_text("user data")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "native/file_search/build.py"),
            str(tmp_path),
            "--cargo",
            str(tmp_path / "not-a-command"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "existing artifacts are preserved" in result.stderr
    assert (tmp_path / "keep").read_text() == "user data"


def test_wheel_contains_executable_and_uses_it_without_test_override(tmp_path):
    configured = os.environ.get("CORKI_TEST_FILE_SEARCH_WHEEL")
    if not configured:
        pytest.skip("set CORKI_TEST_FILE_SEARCH_WHEEL to a freshly built wheel")
    wheel = Path(configured)
    installed = tmp_path / "installed"
    installed.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        binary = archive.getinfo("corki/_native/file_search/corki-file-search")
        assert (binary.external_attr >> 16) & 0o111
        assert "macosx" in wheel.name or "linux" in wheel.name
        for entry in archive.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and ".." not in path.parts
        archive.extractall(installed)
        # zipfile does not preserve Unix executable permission when extracting.
        (installed / binary.filename).chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    (project / "中文 空目录").mkdir()
    program = """
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from corki.cli.native_file_search import executable
from corki.cli.reference_completion import files
binary = executable()
assert binary.is_relative_to(Path(sys.argv[1]))
rows = asyncio.run(files(Path(sys.argv[2]), "空目录"))
print(json.dumps([(r.label, r.description, r.file_score) for r in rows]))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(installed), str(project)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    assert len(rows) == 1 and rows[0][:2] == ["中文 空目录", "Directory"]
    assert isinstance(rows[0][2], int)
