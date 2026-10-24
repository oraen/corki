import os
import runpy
import shutil
from pathlib import Path

import pytest

from corki.execution import bundled


@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows file ACLs")
def test_install_staging_inherits_acl_after_publish(tmp_path):
    import ctypes
    from ctypes import wintypes

    root = Path(__file__).resolve().parents[2]
    staging_directory = runpy.run_path(str(root / "native/sandbox/install.py"))[
        "staging_directory"
    ]
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetFileSecurityW.restype = wintypes.BOOL
    api.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetSecurityDescriptorControl.restype = wintypes.BOOL

    def assert_inheritance_enabled(path):
        size = wintypes.DWORD()
        api.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
        assert size.value > 0, ctypes.get_last_error()
        descriptor = ctypes.create_string_buffer(size.value)
        assert api.GetFileSecurityW(str(path), 4, descriptor, size, ctypes.byref(size))
        control, revision = wintypes.WORD(), wintypes.DWORD()
        assert api.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        )
        assert not control.value & 0x1000  # SE_DACL_PROTECTED

    destination = tmp_path / "published"
    with staging_directory(tmp_path) as staging:
        assert_inheritance_enabled(staging)
        staging.rename(destination)
    assert_inheritance_enabled(destination)
    with (
        pytest.raises(RuntimeError, match="failed verification"),
        staging_directory(tmp_path) as abandoned,
    ):
        (abandoned / "partial").write_bytes(b"fixture")
        raise RuntimeError("failed verification")
    assert not abandoned.exists()
    assert destination.is_dir()


@pytest.fixture
def compiler(tmp_path):
    supplied = os.environ.get("CORKI_TEST_BUNDLED_COMPILER")
    if supplied:
        binary = Path(supplied)
        manifest = binary.with_name(binary.name + ".json")
    else:
        binary = bundled.bundled_compiler()
        if binary is None:
            pytest.skip("requires a host compiler with a verified build receipt")
        manifest = binary.parent / "manifest.json"
    metadata = bundled.verify_compiler(binary, manifest)
    assets = tmp_path / "verified-build"
    assets.mkdir()
    copy = assets / binary.name
    shutil.copy2(binary, copy)
    shutil.copy2(manifest, assets / "manifest.json")
    for name in metadata.get("auxiliary_sha256", {}):
        shutil.copy2(binary.parent / name, assets / name)
    return copy


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
    metadata = bundled.verify_compiler(compiler, compiler.parent / "manifest.json")
    for name in metadata.get("auxiliary_sha256", {}):
        shutil.copy2(compiler.parent / name, binary.parent / name)
    install = runpy.run_path(str(root / "native/sandbox/install.py"))["install"]
    destination = install(binary, source)
    binary_name = "corki-sandbox.exe" if os.name == "nt" else "corki-sandbox"
    assert (destination / binary_name).read_bytes() == binary.read_bytes()
    assert (destination / "manifest.json").read_bytes() == receipt.read_bytes()
    installed = bundled.verify_compiler(destination / binary_name, destination / "manifest.json")
    assert installed == metadata
    with pytest.raises(FileExistsError, match="not replaced"):
        install(binary, source)
    assert (destination / binary_name).read_bytes() == binary.read_bytes()
