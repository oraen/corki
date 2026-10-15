import asyncio

import pytest

from corki.memory import LocalMemoryBackend, MemoryPathError
from corki.memory.context import MemoryContextContributor


@pytest.mark.parametrize(
    "content,offset,maximum,expected,truncated",
    [
        ("a\r\nb\rx\u2028z\n", 1, None, "a\r\nb\rx\u2028z\n", False),
        ("a\r\nb\rx\u2028z\n", 2, 1, "b\rx\u2028z\n", False),
        ("a\r\nb\n", 1, 1, "a\r\n", True),
        ("a\n", 2, None, "", False),
        ("", 1, None, "", False),
    ],
)
def test_memory_read_uses_lf_boundaries_and_preserves_bytes(
    tmp_path, content, offset, maximum, expected, truncated
):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_bytes(content.encode())
    result = asyncio.run(backend.read("MEMORY.md", line_offset=offset, max_lines=maximum))
    assert result["content"] == expected and result["truncated"] == truncated


@pytest.mark.parametrize("content", ["", "a", "a\n"])
def test_memory_read_rejects_offset_beyond_lf_eof(tmp_path, content):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_bytes(content.encode())
    with pytest.raises(ValueError, match="file length"):
        asyncio.run(backend.read("MEMORY.md", line_offset=content.count("\n") + 2))


@pytest.mark.parametrize(
    "content,limit,expected",
    [("abcdefghij", 1, "ab…2 tokens truncated…ij"), ("你好世界", 2, "你…1 tokens truncated…界")],
)
def test_read_and_summary_share_utf8_head_tail_budget(tmp_path, content, limit, expected):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_bytes(content.encode())
    (backend.root / "memory_summary.md").write_bytes(content.encode())
    result = asyncio.run(backend.read("MEMORY.md", max_tokens=limit))
    assert result["content"] == expected and result["truncated"]
    contributor = MemoryContextContributor(backend.root, enabled=True, token_limit=limit)
    fragment = contributor.contributions(cwd=tmp_path, user_input="", realtime_active=False)[0]
    assert fragment.variables["memory_summary"] == expected


@pytest.mark.parametrize(
    "content,query,normalized,sensitive,expected",
    [
        ("my/project.name-v2", "my project_name v2", True, True, True),
        ("版本：甲-2", "版本甲2", True, True, True),
        ("straße", "STRASSE", False, False, False),
        ("STRAẞE", "straße", False, False, True),
        ("Project.V2", "projectv2", True, True, False),
        ("Project.V2", "projectv2", True, False, True),
    ],
)
def test_search_comparison_matches_source_contract(
    tmp_path, content, query, normalized, sensitive, expected
):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_bytes(content.encode())
    result = asyncio.run(backend.search((query,), normalized=normalized, case_sensitive=sensitive))
    assert bool(result["matches"]) == expected


@pytest.mark.parametrize("query", ["---", "💡", " /_ . "])
def test_normalization_to_empty_is_an_error_not_match_all(tmp_path, query):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_text("private memory", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(backend.search((query,), normalized=True))


def test_search_line_numbers_use_lf_not_unicode_separators(tmp_path):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "MEMORY.md").write_bytes("first\r\na\rb\u2028c\nlast\r".encode())
    result = asyncio.run(backend.search(("c", "last")))
    assert [(m["match_line_number"], m["content"]) for m in result["matches"]] == [
        (2, "a\rb\u2028c"),
        (3, "last\r"),
    ]


def test_default_search_rejects_runtime_root_symlink(tmp_path):
    backend = LocalMemoryBackend(tmp_path / "memories")
    backend.root.rename(tmp_path / "saved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("target", encoding="utf-8")
    backend.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MemoryPathError):
        asyncio.run(backend.search(("target",)))


def test_dangling_link_is_rejected_as_link_not_missing_file(tmp_path):
    backend = LocalMemoryBackend(tmp_path / "memories")
    (backend.root / "missing").symlink_to(tmp_path / "not-created")
    with pytest.raises(MemoryPathError):
        asyncio.run(backend.read("missing"))
