"""Local image helpers only; no model, account, or network requests."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/imagegen/scripts"


def test_image_bundle_closes_prior_handles_when_open_fails(tmp_path):
    spec = importlib.util.spec_from_file_location("tested_image_helper", SCRIPTS / "image_gen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "图像.dat"
    path.write_bytes(b"fixture")
    handles = []

    class ObservedFile:
        def open(self, mode):
            handle = path.open(mode)
            handles.append(handle)
            return handle

    with (
        pytest.raises(FileNotFoundError),
        module._open_files([ObservedFile(), tmp_path / "missing"]),
    ):
        pytest.fail("missing second input must fail")
    assert handles and all(handle.closed for handle in handles)
    path.unlink()


def test_chroma_helper_unicode_paths_and_stdout(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "输入 image.png"
    output = tmp_path / "输出 image.png"
    with Image.new("RGB", (2, 1), (0, 255, 0)) as image:
        image.putpixel((1, 0), (255, 0, 0))
        image.save(source)
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(SCRIPTS / "remove_chroma_key.py"),
         "--input", str(source), "--out", str(output)],
        env={**os.environ, "PYTHONIOENCODING": "ascii", "CORKI_HOME": str(tmp_path / "home")},
        capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "输出 image.png" in result.stdout.decode("utf-8")
    with Image.open(output) as image:
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((1, 0)) == (255, 0, 0, 255)
    source.unlink()
    output.unlink()
