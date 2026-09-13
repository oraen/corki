import runpy
import shutil
from pathlib import Path

import pytest
from test_bundled_execution import compiler as compiler


def test_source_install_verifies_assets_and_never_overwrites_existing_bundle(tmp_path, compiler):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    (source / "src/corki/execution").mkdir(parents=True)
    (source / "src/corki/_native").mkdir()
    shutil.copy2(root / "src/corki/execution/bundled.py", source / "src/corki/execution/bundled.py")
    shutil.copytree(root / "native/sandbox", source / "native/sandbox")
    binary = tmp_path / "built-compiler"
    shutil.copy2(compiler, binary)
    receipt = binary.with_name(binary.name + ".json")
    shutil.copy2(compiler.parent / "manifest.json", receipt)
    install = runpy.run_path(str(root / "native/sandbox/install.py"))["install"]
    destination = install(binary, source)
    assert (destination / "corki-sandbox").read_bytes() == binary.read_bytes()
    assert (destination / "manifest.json").read_bytes() == receipt.read_bytes()
    with pytest.raises(FileExistsError, match="not replaced"):
        install(binary, source)
    assert (destination / "corki-sandbox").read_bytes() == binary.read_bytes()
