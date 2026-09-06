import asyncio
from pathlib import Path

import pytest

from corki.memory import LocalMemoryBackend, MemoryPathError


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
