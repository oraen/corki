"""Locate a verified policy compiler in this installation, never in cwd or PATH."""

import hashlib
import json
import os
import platform
import re
import struct
from pathlib import Path

REFERENCE_COMMIT = "ddf04ad26789d040f9ef6a96736f76602e35a6cc"
_BUNDLE = Path(__file__).resolve().parent.parent / "_native" / "sandbox"


def host_identity() -> tuple[str, str]:
    machine = platform.machine().lower()
    return platform.system().lower(), {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate compiler manifest field")
        result[key] = value
    return result


def _macos_minimum(binary: Path) -> tuple[int, int, int]:
    with binary.open("rb") as stream:
        header = stream.read(32)
        count, size = struct.unpack_from("<II", header, 16)
        if not 0 < count <= 65536 or not 0 < size <= 1024 * 1024:
            raise ValueError("invalid compiler load commands")
        commands = stream.read(size)
    offset = 0
    versions = []
    for _ in range(count):
        if offset + 8 > len(commands):
            raise ValueError("truncated compiler load command")
        kind, length = struct.unpack_from("<II", commands, offset)
        if length < 8 or offset + length > len(commands):
            raise ValueError("invalid compiler load command size")
        version = None
        if kind == 0x32 and length >= 24:  # LC_BUILD_VERSION
            target, version = struct.unpack_from("<II", commands, offset + 8)
            if target != 1:
                raise ValueError("compiler is not a macOS executable")
        elif kind == 0x24 and length >= 16:  # LC_VERSION_MIN_MACOSX
            version = struct.unpack_from("<I", commands, offset + 8)[0]
        if version is not None:
            versions.append((version >> 16, (version >> 8) & 255, version & 255))
        offset += length
    if offset != size or not versions:
        raise ValueError("compiler has no valid macOS deployment target")
    return max(versions)


def verify_binary_platform(binary: Path, tag: str) -> None:
    """Check native format/architecture; do not infer them from the build host."""
    system, machine = host_identity()
    with binary.open("rb") as stream:
        header = stream.read(64)
    actual = None
    valid_tag = False
    if system == "darwin":
        if len(header) >= 32 and header[:4] == b"\xcf\xfa\xed\xfe":
            cpu, _, kind = struct.unpack_from("<III", header, 4)
            if kind == 2:
                actual = {0x1000007: "x86_64", 0x100000C: "arm64"}.get(cpu)
        match = re.fullmatch(r"macosx_(\d+)_(\d+)_" + re.escape(machine), tag)
        if match:
            current = tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2])
            requested = tuple(map(int, match.groups()))
            valid_tag = requested <= current and (requested[0] < 11 or requested[1] == 0)
            if actual == machine and valid_tag:
                valid_tag = _macos_minimum(binary) <= (*requested, 0)
    elif system == "linux":
        if len(header) == 64 and header[:6] == b"\x7fELF\x02\x01":
            kind, cpu = struct.unpack_from("<HH", header, 16)
            if kind in (2, 3):  # executable or position-independent executable
                actual = {62: "x86_64", 183: "arm64"}.get(cpu)
        valid_tag = tag == "linux_" + {"arm64": "aarch64"}.get(machine, machine)
    if actual != machine or actual is None:
        raise ValueError("bundled compiler format or architecture does not match this host")
    if not valid_tag:
        raise ValueError("bundled compiler wheel platform does not match this host")


def verify_compiler(binary: Path, manifest_path: Path) -> dict:
    """Detect stale, damaged or foreign assets; this is not a release signature."""
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 8192
    ):
        raise ValueError("invalid bundled compiler manifest")
    try:
        manifest = json.loads(manifest_path.read_bytes(), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid bundled compiler manifest") from error
    fields = {
        "version",
        "reference_commit",
        "source_sha256",
        "binary_sha256",
        "system",
        "machine",
        "wheel_platform",
    }
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise ValueError("invalid bundled compiler manifest fields")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("unsupported bundled compiler manifest version")
    if manifest["reference_commit"] != REFERENCE_COMMIT:
        raise ValueError("bundled compiler reference revision mismatch")
    if (manifest["system"], manifest["machine"]) != host_identity():
        raise ValueError("bundled compiler does not match this platform")
    for key in ("source_sha256", "binary_sha256"):
        if (
            not isinstance(manifest[key], str)
            or re.fullmatch("[0-9a-f]{64}", manifest[key]) is None
        ):
            raise ValueError("invalid bundled compiler digest")
    tag = manifest["wheel_platform"]
    if not isinstance(tag, str) or re.fullmatch("[a-z0-9_]+", tag) is None or tag == "any":
        raise ValueError("bundled compiler requires a platform wheel")
    if (
        binary.is_symlink()
        or not binary.is_file()
        or not 0 < binary.stat().st_size <= 64 * 1024 * 1024
    ):
        raise ValueError("invalid bundled compiler executable")
    if not os.access(binary, os.X_OK) or file_sha256(binary) != manifest["binary_sha256"]:
        raise ValueError("bundled compiler is not executable or its digest differs")
    verify_binary_platform(binary, tag)
    return manifest


def bundled_compiler() -> Path | None:
    if not _BUNDLE.exists() and not _BUNDLE.is_symlink():
        return None
    if _BUNDLE.is_symlink() or not _BUNDLE.is_dir():
        raise ValueError("invalid bundled compiler directory")
    name = "corki-sandbox.exe" if os.name == "nt" else "corki-sandbox"
    binary = _BUNDLE / name
    verify_compiler(binary, _BUNDLE / "manifest.json")
    return binary
