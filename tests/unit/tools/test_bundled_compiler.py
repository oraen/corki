import json
import os
import struct

import pytest

from corki.execution import bundled


def pe_image(machine=0x8664, flags=0x22, magic=0x20B):
    image = bytearray(512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<HH", image, 132, machine, 1)
    struct.pack_into("<HHH", image, 148, 240, flags, magic)
    return bytes(image)


@pytest.mark.parametrize("machine,host,tag", [
    (0x8664, "x86_64", "win_amd64"), (0xAA64, "arm64", "win_arm64"),
])
def test_windows_pe_architecture_and_wheel_tag(tmp_path, monkeypatch, machine, host, tag):
    binary = tmp_path / "helper.exe"
    binary.write_bytes(pe_image(machine))
    monkeypatch.setattr(bundled, "host_identity", lambda: ("windows", host))
    bundled.verify_binary_platform(binary, tag)
    with pytest.raises(ValueError, match="wheel platform"):
        bundled.verify_binary_platform(binary, "any")


@pytest.mark.parametrize("content", [
    b"MZ", pe_image(0xAA64), pe_image(flags=0x2022), pe_image(magic=0x10B),
    pe_image()[:180], b"MZ" + b"\xff" * 62,
])
def test_windows_rejects_foreign_dll_truncated_and_invalid_pe(tmp_path, monkeypatch, content):
    binary = tmp_path / "helper.exe"
    binary.write_bytes(content)
    monkeypatch.setattr(bundled, "host_identity", lambda: ("windows", "x86_64"))
    with pytest.raises(ValueError, match="architecture"):
        bundled.verify_binary_platform(binary, "win_amd64")


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "installed" / "sandbox"
    root.mkdir(parents=True)
    binary = root / ("corki-sandbox.exe" if os.name == "nt" else "corki-sandbox")
    binary.write_bytes(
        struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 1, 24, 0, 0)
        + struct.pack("<IIIIII", 0x32, 24, 1, 11 << 16, 15 << 16, 0)
    )
    binary.chmod(0o755)
    manifest = {
        "version": 1,
        "reference_commit": bundled.REFERENCE_COMMIT,
        "source_sha256": "a" * 64,
        "binary_sha256": bundled.file_sha256(binary),
        "system": "darwin",
        "machine": "arm64",
        "wheel_platform": "macosx_15_0_arm64",
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(bundled, "_BUNDLE", root)
    monkeypatch.setattr(bundled, "host_identity", lambda: ("darwin", "arm64"))
    monkeypatch.setattr(bundled.platform, "mac_ver", lambda: ("15.1", (), "arm64"))
    return root, binary, manifest


def test_loaded_only_from_verified_installation(bundle, tmp_path, monkeypatch):
    _, binary, _ = bundle
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    (tmp_path / "corki-sandbox").write_text("not the installed executable")
    assert bundled.bundled_compiler() == binary


def test_missing_is_distinct_from_dangling_bundle(tmp_path, monkeypatch):
    root = tmp_path / "missing"
    monkeypatch.setattr(bundled, "_BUNDLE", root)
    assert bundled.bundled_compiler() is None
    root.symlink_to(tmp_path / "nonexistent")
    with pytest.raises(ValueError, match="directory"):
        bundled.bundled_compiler()


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("reference_commit", "a" * 40),
        ("system", "linux"),
        ("machine", "x86_64"),
        ("source_sha256", "invalid"),
        ("binary_sha256", "b" * 64),
        ("wheel_platform", "any"),
        ("wheel_platform", "linux_arm64"),
        ("wheel_platform", "macosx_15_0_x86_64"),
        ("wheel_platform", "macosx_99_0_arm64"),
        ("wheel_platform", "macosx_15_1_arm64"),
    ],
)
def test_invalid_receipt_never_loads(bundle, field, value):
    root, _, manifest = bundle
    manifest[field] = value
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        bundled.bundled_compiler()


@pytest.mark.parametrize("raw", ["[]", "{", '{"version":1,"version":1}', " " * 8193])
def test_invalid_manifest_encoding_or_shape(bundle, raw):
    root, _, _ = bundle
    (root / "manifest.json").write_text(raw)
    with pytest.raises(ValueError):
        bundled.bundled_compiler()


@pytest.mark.parametrize("asset", ["corki-sandbox", "manifest.json"])
def test_symlink_asset_is_not_accepted(bundle, asset):
    root, binary, _ = bundle
    path = binary if asset == "corki-sandbox" else root / asset
    target = root / (asset + ".original")
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError):
        bundled.bundled_compiler()


@pytest.mark.parametrize("content", [b"#!/bin/sh\nexit 0\n", b"", b"corrupted"])
def test_digest_alone_does_not_validate_executable_format(bundle, content):
    root, binary, manifest = bundle
    binary.write_bytes(content)
    manifest["binary_sha256"] = bundled.file_sha256(binary)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        bundled.bundled_compiler()


def test_wrong_binary_architecture_cannot_borrow_host_receipt(bundle):
    root, binary, manifest = bundle
    binary.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, 0x1000007, 0, 2, 0, 0, 0, 0))
    manifest["binary_sha256"] = bundled.file_sha256(binary)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="architecture"):
        bundled.bundled_compiler()


@pytest.mark.skipif(os.name == "nt", reason="Windows does not implement Unix executable mode bits")
def test_non_executable_is_rejected(bundle):
    _, binary, _ = bundle
    binary.chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        bundled.bundled_compiler()


def test_wheel_tag_cannot_understate_native_minimum(bundle):
    root, binary, manifest = bundle
    content = bytearray(binary.read_bytes())
    struct.pack_into("<I", content, 44, (15 << 16) | (1 << 8))
    binary.write_bytes(content)
    manifest["binary_sha256"] = bundled.file_sha256(binary)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="wheel platform"):
        bundled.bundled_compiler()


@pytest.mark.parametrize(
    "configuration",
    [
        '[execution]\nprofile={type="read-only"}',
        'default_permissions=":read-only"',
        "[execution]",
    ],
)
def test_explicit_configuration_uses_installed_compiler(bundle, tmp_path, configuration):
    from corki.config import CorkiSettings

    _, binary, _ = bundle
    config = tmp_path / "config.toml"
    config.write_text(configuration)
    permissions = CorkiSettings.for_directory(tmp_path, config_file=config).execution_permissions
    assert permissions.compiler == binary
    assert permissions.policy_cwd == tmp_path.resolve()
    assert json.loads(permissions.profile_json)["type"] in ("read-only", "selection")


def test_explicit_host_override_does_not_load_broken_bundle(bundle, tmp_path):
    from corki.config.permissions import parse_execution_permissions

    root, _, _ = bundle
    (root / "manifest.json").write_text("broken")
    override = tmp_path / "host-owned-compiler"
    permissions = parse_execution_permissions(
        {"compiler": str(override), "profile": {"type": "read-only"}}, tmp_path
    )
    assert permissions.compiler == override


def test_corrupted_bundle_cannot_fall_back_to_unrestricted_configuration(bundle, tmp_path):
    from corki.config import CorkiSettings

    root, _, _ = bundle
    (root / "manifest.json").write_text("broken")
    config = tmp_path / "config.toml"
    config.write_text('[execution]\nprofile={type="read-only"}')
    with pytest.raises(ValueError, match="manifest"):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.fixture
def windows_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled, "host_identity", lambda: ("windows", "x86_64"))
    binary = tmp_path / "corki-sandbox.exe"
    binary.write_bytes(pe_image())
    binary.chmod(0o755)
    for name in bundled.WINDOWS_HELPERS:
        (tmp_path / name).write_bytes(pe_image())
    manifest = {
        "version": 2,
        "reference_commit": bundled.REFERENCE_COMMIT,
        "source_sha256": "a" * 64,
        "binary_sha256": bundled.file_sha256(binary),
        "system": "windows",
        "machine": "x86_64",
        "wheel_platform": "win_amd64",
        "auxiliary_sha256": {
            name: bundled.file_sha256(tmp_path / name) for name in bundled.WINDOWS_HELPERS
        },
    }
    receipt = tmp_path / "manifest.json"
    receipt.write_text(json.dumps(manifest))
    return binary, receipt, manifest


def test_windows_bundle_requires_all_verified_helpers(windows_bundle):
    binary, receipt, manifest = windows_bundle
    assert bundled.verify_compiler(binary, receipt) == manifest
    for name in bundled.WINDOWS_HELPERS:
        helper = binary.parent / name
        original = helper.read_bytes()
        helper.unlink()
        with pytest.raises(ValueError, match="helper"):
            bundled.verify_compiler(binary, receipt)
        helper.write_bytes(original + b"damaged")
        with pytest.raises(ValueError, match="helper"):
            bundled.verify_compiler(binary, receipt)
        helper.write_bytes(original)


@pytest.mark.parametrize("change", ["legacy", "extra", "path", "digest", "foreign"])
def test_windows_helper_manifest_cannot_bypass_verification(windows_bundle, change):
    binary, receipt, manifest = windows_bundle
    name = sorted(bundled.WINDOWS_HELPERS)[0]
    if change == "legacy":
        manifest["version"] = 1
        del manifest["auxiliary_sha256"]
    elif change in {"extra", "path"}:
        extra = "extra.exe" if change == "extra" else "../helper.exe"
        manifest["auxiliary_sha256"][extra] = "a" * 64
    elif change == "digest":
        manifest["auxiliary_sha256"][name] = True
    else:
        helper = binary.parent / name
        helper.write_bytes(pe_image(0xAA64))
        manifest["auxiliary_sha256"][name] = bundled.file_sha256(helper)
    receipt.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        bundled.verify_compiler(binary, receipt)


def test_source_install_preserves_windows_helper_bundle(windows_bundle, tmp_path, monkeypatch):
    import runpy
    from pathlib import Path

    installer = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / "native/sandbox/install.py")
    )
    binary, receipt, manifest = windows_bundle
    receipt.rename(binary.with_name(binary.name + ".json"))
    source = tmp_path / "checkout"
    (source / "src/corki/_native").mkdir(parents=True)
    api = {"verify_compiler": bundled.verify_compiler}
    digest_api = {"source_digest": lambda _: manifest["source_sha256"]}
    monkeypatch.setattr(
        runpy, "run_path", lambda path: api if path.endswith("bundled.py") else digest_api
    )
    destination = installer["install"](binary, source)
    installed = bundled.verify_compiler(destination / binary.name, destination / "manifest.json")
    assert installed == manifest
    assert {path.name for path in destination.iterdir()} == {
        binary.name, "manifest.json", *bundled.WINDOWS_HELPERS
    }
    with pytest.raises(FileExistsError):
        installer["install"](binary, source)
