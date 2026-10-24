"""Search artifact provenance covers the flags needed on a clean Windows host."""

import runpy
from pathlib import Path

import pytest

API = runpy.run_path(str(Path(__file__).resolve().parents[3] / "native/file_search/artifact.py"))
INPUTS = ("Cargo.toml", "Cargo.lock", ".cargo/config.toml", "src/main.rs")


@pytest.mark.parametrize("name", INPUTS)
def test_search_receipt_tracks_compiled_inputs(tmp_path, name):
    for item in INPUTS:
        path = tmp_path / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    before = API["source_digest"](tmp_path)
    (tmp_path / name).write_text("changed", encoding="utf-8")
    assert API["source_digest"](tmp_path) != before
    (tmp_path / name).write_text("original", encoding="utf-8")
    assert API["source_digest"](tmp_path) == before
    (tmp_path / "README.md").write_text("documentation", encoding="utf-8")
    assert API["source_digest"](tmp_path) == before
