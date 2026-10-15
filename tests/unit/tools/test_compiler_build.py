import runpy
from pathlib import Path

import pytest

HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[3] / "native/sandbox/receipt.py"))


@pytest.mark.parametrize(
    "name",
    ["Cargo.toml", "Cargo.lock", "src/main.rs", "src/nested/policy.rs", "patches/metadata.patch"],
)
def test_provenance_digest_tracks_every_compiled_input(tmp_path, name):
    for path in (
        "Cargo.toml",
        "Cargo.lock",
        "src/main.rs",
        "src/nested/policy.rs",
        "patches/metadata.patch",
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original")
    before = HELPERS["source_digest"](tmp_path)
    (tmp_path / name).write_text("modified")
    assert HELPERS["source_digest"](tmp_path) != before
    (tmp_path / name).write_text("original")
    assert HELPERS["source_digest"](tmp_path) == before
    (tmp_path / "README.md").write_text("documentation-only")
    assert HELPERS["source_digest"](tmp_path) == before


def test_receipt_uses_compiled_snapshot_not_later_source(monkeypatch, tmp_path):
    expected = {
        "version": 1,
        "reference_commit": "fixed-reference",
        "source_sha256": "compiled-before-source-changed",
        "binary_sha256": "binary-digest",
        "system": "linux",
        "machine": "arm64",
        "wheel_platform": "linux_aarch64",
    }
    checked = []
    api = {
        "host_identity": lambda: ("linux", "arm64"),
        "file_sha256": lambda _: "binary-digest",
        "REFERENCE_COMMIT": "fixed-reference",
        "verify_binary_platform": lambda *args: checked.append(args),
    }
    monkeypatch.setattr(runpy, "run_path", lambda _: api)
    binary = tmp_path / "compiler"
    manifest = HELPERS["receipt"](
        tmp_path / "native/sandbox", binary, compiled_digest="compiled-before-source-changed"
    )
    assert manifest == expected
    assert checked == [(binary, "linux_aarch64")]
