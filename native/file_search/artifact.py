"""Build-time validation for the platform-specific local search asset."""

import hashlib
import json
import runpy
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(root):
    value = hashlib.sha256()
    for name in ("Cargo.toml", "Cargo.lock", "src/main.rs"):
        value.update(name.encode() + b"\0" + (root / name).read_bytes())
    return value.hexdigest()


def verify(root, directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != 1:
        raise ValueError("unsupported local search artifact")
    binary = directory / "corki-file-search"
    if binary.is_symlink() or not binary.is_file() or binary.stat().st_size > 64_000_000:
        raise ValueError("invalid local search executable")
    if manifest["source_sha256"] != source_digest(root / "native/file_search"):
        raise ValueError("local search source does not match artifact")
    for name in ("corki-file-search", "THIRD_PARTY.txt", "RUST_COPYRIGHT.html"):
        if digest(directory / name) != manifest["files"][name]:
            raise ValueError("local search artifact checksum mismatch")
    api = runpy.run_path(str(root / "src/corki/execution/bundled.py"))
    api["verify_binary_platform"](binary, manifest["wheel_platform"])
    return manifest
