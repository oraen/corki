import json
import struct

import pytest

from corki.execution import bundled


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "installed" / "sandbox"
    root.mkdir(parents=True)
    binary = root / "corki-sandbox"
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
    root, _, _ = bundle
    path = root / asset
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
