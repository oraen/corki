"""Inspect the actual release wheel, including generated native payload hygiene."""

import hashlib
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from corki.execution.bundled import WINDOWS_HELPERS

WASM_ASSETS = (
    "mcp_regex.wasm", "mcp_url.wasm", "HOOK_REGEX_LICENSES.txt", "REGEX_LITE_LICENSE.txt",
    "MCP_URL_LICENSES.txt", "MCP_URL_RUST_COPYRIGHT.html",
)


def test_source_archive_only_contains_reviewed_native_assets():
    configured = os.environ.get("CORKI_TEST_SDIST")
    if not configured:
        pytest.skip("set CORKI_TEST_SDIST to a built source archive")
    with tarfile.open(configured) as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        assert len(names) == len(set(names)), "duplicate source archive entries"
        roots = {name.split("/", 1)[0] for name in names}
        assert len(roots) == 1
        prefix = roots.pop() + "/src/corki/_native/"
        native = {
            member.name for member in members
            if member.isfile() and member.name.startswith(prefix)
        }
        assert native == {prefix + name for name in WASM_ASSETS}
        for name in WASM_ASSETS:
            stream = archive.extractfile(prefix + name)
            assert stream is not None
            with stream:
                source = Path(__file__).resolve().parents[2] / "src/corki/_native" / name
                assert stream.read() == source.read_bytes()
        for component in ("sandbox", "file_search"):
            assert any(name.endswith(f"/native/{component}/.cargo/config.toml") for name in names)


def test_native_wheel_payload_is_current_and_provenance_checked():
    configured = os.environ.get("CORKI_TEST_WHEEL")
    if not configured:
        pytest.skip("set CORKI_TEST_WHEEL to a built platform wheel")
    wheel = Path(configured)
    assert wheel.is_file(), wheel
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "duplicate wheel entries"
        native = {name for name in names if name.startswith("corki/_native/")}
        prefix = "corki/_native/sandbox/"
        manifest = json.loads(archive.read(prefix + "manifest.json"))
        binary = "corki-sandbox.exe" if manifest["system"] == "windows" else "corki-sandbox"
        expected = {prefix + binary, prefix + "manifest.json"}
        expected.update("corki/_native/" + name for name in WASM_ASSETS)
        for name in WASM_ASSETS:
            content = archive.read("corki/_native/" + name)
            source = Path(__file__).resolve().parents[2] / "src/corki/_native" / name
            assert content == source.read_bytes(), f"reviewed asset changed: {name}"
        auxiliary = manifest.get("auxiliary_sha256", {})
        assert set(auxiliary) == (WINDOWS_HELPERS if manifest["system"] == "windows" else set())
        expected.update(prefix + name for name in auxiliary)
        digest = hashlib.sha256(archive.read(prefix + binary)).hexdigest()
        assert digest == manifest["binary_sha256"]
        for name, digest in auxiliary.items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest
        search = "corki/_native/file_search/"
        if search + "manifest.json" in native:
            executable = (
                "corki-file-search.exe" if manifest["system"] == "windows" else "corki-file-search"
            )
            expected.update(search + name for name in (
                executable, "manifest.json", "THIRD_PARTY.txt", "RUST_COPYRIGHT.html"
            ))
        assert native == expected, f"unexpected native payload: {sorted(native - expected)}"
