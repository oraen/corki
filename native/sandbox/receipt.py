"""Build-time provenance for a host-platform compiler, independent of runtime imports."""

import hashlib
import platform
import runpy
from pathlib import Path


def source_digest(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        [
            source / "Cargo.toml",
            source / "Cargo.lock",
            *(source / "src").rglob("*.rs"),
            *(source / "patches").glob("*.patch"),
        ]
    ):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def receipt(source: Path, binary: Path, *, compiled_digest: str) -> dict:
    api = runpy.run_path(str(source.parents[1] / "src/corki/execution/bundled.py"))
    system, machine = api["host_identity"]()
    tag = system + "_" + {"arm64": "aarch64"}.get(machine, machine)
    if system == "darwin":
        # Stay in the build host's OS generation, not Python's older deployment
        # target. macOS 11+ wheel tags use major_0; reject binaries whose actual
        # minimum cannot be represented by that tag instead of understating it.
        major, minor, *_ = platform.mac_ver()[0].split(".")
        if int(major) >= 11:
            minor = "0"
        tag = f"macosx_{major}_{minor}_{machine}"
    api["verify_binary_platform"](binary, tag)
    return {
        "version": 1,
        "reference_commit": api["REFERENCE_COMMIT"],
        "source_sha256": compiled_digest,
        "binary_sha256": api["file_sha256"](binary),
        "system": system,
        "machine": machine,
        "wheel_platform": tag,
    }
