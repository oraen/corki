import asyncio
from pathlib import Path

import pytest

from corki.memory import LocalMemoryBackend, MemoryPathError


@pytest.mark.parametrize("note", ["no newline", "line\n\n", "界\r\nline  \t", "  keep  "])
def test_ad_hoc_note_preserves_exact_utf8_bytes(tmp_path, note):
    backend = LocalMemoryBackend(tmp_path / "memories")
    filename = "2026-09-07T12-00-00-exact.md"
    backend.add_note(filename, note)
    target = backend.root / "extensions/ad_hoc/notes" / filename
    assert target.read_bytes() == note.encode("utf-8")
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        backend.add_note(filename, "replacement")
    assert target.read_bytes() == note.encode("utf-8")


def test_ad_hoc_note_rejects_non_ascii_timestamp(tmp_path):
    backend = LocalMemoryBackend(tmp_path / "memories")
    with pytest.raises(ValueError):
        backend.add_note("２０２６-09-07T12-00-00-invalid.md", "note")
    assert not list((backend.root / "extensions/ad_hoc/notes").iterdir())


@pytest.mark.parametrize(
    "component", ["", "extensions", "extensions/ad_hoc", "extensions/ad_hoc/notes"]
)
def test_ad_hoc_note_rechecks_replaced_directory_before_writing(tmp_path, component):
    backend = LocalMemoryBackend(tmp_path / "memories")
    target = backend.root / component
    saved = tmp_path / "saved"
    target.rename(saved)
    outside = tmp_path / "outside"
    outside.mkdir()
    # Mirror the expected suffix to expose an actual escaped write, not merely ENOENT.
    suffix = (
        "extensions/ad_hoc/notes"
        if not component
        else "extensions/ad_hoc/notes"[len(component) :].lstrip("/")
    )
    (outside / suffix).mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        backend.add_note("2026-09-07T12-00-00-escape.md", "must stay private")
    assert not list(outside.rglob("*.md"))
    assert target.is_symlink() and saved.is_dir()


def test_ad_hoc_note_recreates_missing_notes_directory(tmp_path):
    backend = LocalMemoryBackend(tmp_path / "memories")
    directory = backend.root / "extensions/ad_hoc/notes"
    directory.rmdir()
    backend.add_note("2026-09-07T12-00-00-recreated.md", "note")
    assert (directory / "2026-09-07T12-00-00-recreated.md").read_bytes() == b"note"


def test_ad_hoc_model_schema_explains_filename_and_verbatim_contract(tmp_path):
    from corki.memory.tools import MemoryAddNoteTool

    spec = MemoryAddNoteTool(LocalMemoryBackend(tmp_path / "memories")).spec
    filename = spec.parameters["properties"]["filename"]
    assert filename["type"] == "string"
    assert "YYYY-MM-DDTHH-MM-SS-<slug>.md" in filename["description"]
    assert "pattern" not in filename and "minLength" not in filename
    assert "Verbatim" in spec.parameters["properties"]["note"]["description"]


def test_memory_backend_lists_reads_searches_and_adds_notes(tmp_path: Path) -> None:
    root = tmp_path / "memories"
    backend = LocalMemoryBackend(root)
    (root / "MEMORY.md").write_text(
        "v1\n\nproject alpha\ncommand pytest\nsecond alpha fact\n", encoding="utf-8"
    )

    listing = backend.list()
    assert any(entry["path"] == "MEMORY.md" for entry in listing["entries"])

    async def scenario() -> None:
        read = await backend.read("MEMORY.md", line_offset=3, max_lines=1)
        assert read["content"] == "project alpha\n"
        matches = await backend.search(
            ("alpha", "pytest"),
            match_mode="all_within_lines",
            within_lines=2,
            context_lines=1,
        )
        assert matches["matches"][0]["path"] == "MEMORY.md"
        assert "command pytest" in matches["matches"][0]["content"]

    asyncio.run(scenario())
    filename = "2026-09-06T10-00-00-remember-style.md"
    note = backend.add_note(filename, "Prefer concise explanations.")
    assert note["path"].endswith(filename)
    with pytest.raises(FileExistsError):
        backend.add_note(filename, "duplicate")
    with pytest.raises(ValueError):
        backend.add_note(f"2026-09-06T10-00-00-{'x' * 81}.md", "too long")


def test_memory_backend_rejects_escape_hidden_and_symlink_paths(tmp_path: Path) -> None:
    root = tmp_path / "memories"
    backend = LocalMemoryBackend(root)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(MemoryPathError):
        backend.list("../outside.md")
    with pytest.raises(FileNotFoundError):
        backend.list(".hidden")

    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(MemoryPathError):
        asyncio.run(backend.read("link.md"))


def test_memory_layout_rejects_symlinked_managed_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "memories"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "extensions").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(ValueError, match="symbolic link"):
        LocalMemoryBackend(root)
