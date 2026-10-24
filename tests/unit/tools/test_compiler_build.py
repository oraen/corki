import runpy
from pathlib import Path

import pytest

HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[3] / "native/sandbox/receipt.py"))


@pytest.mark.parametrize(
    "name",
    [
        "Cargo.toml", "Cargo.lock", ".cargo/config.toml", "src/main.rs",
        "src/nested/policy.rs", "patches/metadata.patch",
    ],
)
def test_provenance_digest_tracks_every_compiled_input(tmp_path, name):
    for path in (
        "Cargo.toml",
        "Cargo.lock",
        ".cargo/config.toml",
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


def test_reference_export_uses_git_and_safe_python_tar(tmp_path, monkeypatch):
    import io
    import tarfile

    build = runpy.run_path(str(Path(__file__).resolve().parents[3] / "native/sandbox/build.py"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        with tarfile.open(fileobj=kwargs["stdout"], mode="w:") as archive:
            payload = "中文 source".encode()
            member = tarfile.TarInfo("src/example.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(build["subprocess"], "run", run)
    destination = tmp_path / "export"
    destination.mkdir()
    build["export_reference"](tmp_path, destination)
    assert (destination / "src/example.txt").read_text(encoding="utf-8") == "中文 source"
    assert len(calls) == 1
    assert calls[0][0] == "git"
    assert calls[0][-1] == build["COMMIT"]


def test_windows_receipt_requires_and_checks_both_helpers(monkeypatch, tmp_path):
    from corki.execution.bundled import WINDOWS_HELPERS

    checked = []
    api = {
        "host_identity": lambda: ("windows", "x86_64"),
        "file_sha256": lambda path: path.name + "-digest",
        "REFERENCE_COMMIT": "fixed-reference",
        "WINDOWS_HELPERS": WINDOWS_HELPERS,
        "verify_binary_platform": lambda *args: checked.append(args),
    }
    monkeypatch.setattr(runpy, "run_path", lambda _: api)
    source, binary = tmp_path / "native/sandbox", tmp_path / "compiler.exe"
    with pytest.raises(ValueError, match="setup and command runner"):
        HELPERS["receipt"](source, binary, compiled_digest="snapshot")
    auxiliary = {name: tmp_path / name for name in WINDOWS_HELPERS}
    checked.clear()
    manifest = HELPERS["receipt"](
        source, binary, compiled_digest="snapshot", auxiliary=auxiliary
    )
    assert manifest["version"] == 2
    assert manifest["auxiliary_sha256"] == {name: name + "-digest" for name in WINDOWS_HELPERS}
    assert set(checked) == {(path, "win_amd64") for path in (binary, *auxiliary.values())}
