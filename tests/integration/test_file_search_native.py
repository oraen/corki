"""Local filesystem fixtures only; never use clipboard, model or account services."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from corki.execution.owned_process import run_owned


@pytest.fixture
def search_binary():
    configured = os.environ.get("CORKI_TEST_FILE_SEARCH")
    if not configured:
        pytest.skip("set CORKI_TEST_FILE_SEARCH to a built local search helper")
    path = Path(configured)
    assert path.is_absolute() and path.is_file()
    return path


def search(binary, cwd, query, limit=100):
    return json.loads(
        asyncio.run(
            run_owned(
                [str(binary)],
                json.dumps({"query": query, "limit": limit}).encode(),
                cwd=cwd,
                output_limit=8_000_000,
                timeout=5,
            )
        )
    )


def test_empty_and_hidden_directories_and_ignore_rules(search_binary, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    for directory in ("中文 空目录", ".hidden-empty", "ignored"):
        (tmp_path / directory).mkdir()
    assert search(search_binary, tmp_path, "空目录")[0]["directory"] is True
    assert search(search_binary, tmp_path, "hidden")[0]["path"] == ".hidden-empty"
    assert search(search_binary, tmp_path, "ignored") == []
    # Codex's empty exclude list does not hide the .git directory itself.
    assert any(
        row["path"] == ".git" and row["directory"]
        for row in search(search_binary, tmp_path, ".git")
    )
    assert search(search_binary, tmp_path, "") == []


def test_non_git_parent_ignore_does_not_hide_child(search_binary, tmp_path):
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    child = tmp_path / "project"
    child.mkdir()
    (child / "visible empty").mkdir()
    assert search(search_binary, child, "visible")[0]["path"] == "visible empty"


def test_followed_links_keep_spelling_and_loop_does_not_fail(search_binary, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "empty").mkdir()
    (project / "资料 link").symlink_to(external, target_is_directory=True)
    (project / "loop").symlink_to(project, target_is_directory=True)
    rows = search(search_binary, project, "empty")
    assert [row["path"] for row in rows] == ["资料 link/empty"]
    assert rows[0]["directory"]


def test_nucleo_normalization_and_path_boundary_ranking(search_binary, tmp_path):
    (tmp_path / "nested").mkdir()
    for name in ("café.md", "nested/foo.rs", "longfoogap.rs", "f_o_o.rs"):
        (tmp_path / name).touch()
    assert search(search_binary, tmp_path, "cafe")[0]["path"] == "café.md"
    rows = search(search_binary, tmp_path, "foo")
    assert rows[0]["path"] == "nested/foo.rs"
    assert [row["score"] for row in rows] == sorted((r["score"] for r in rows), reverse=True)
    assert search(search_binary, tmp_path, "^nested/")[0]["path"] == "nested/foo.rs"


def test_top_matches_not_first_discovered_entries(search_binary, tmp_path):
    for index in range(10001):
        (tmp_path / f"ordinary-{index:05}").mkdir()
    (tmp_path / "zz-exact-needle").mkdir()
    assert search(search_binary, tmp_path, "needle", 1)[0]["path"] == "zz-exact-needle"
    assert len(search(search_binary, tmp_path, "ordinary", 20)) == 20


@pytest.mark.parametrize("query,limit", [("a", 0), ("a", 101), ("字" * 1001, 10)])
def test_invalid_request_bounds_fail_explicitly(search_binary, tmp_path, query, limit):
    with pytest.raises(ValueError, match="status 1"):
        search(search_binary, tmp_path, query, limit)


def test_cli_candidates_keep_native_ranking_and_normalization(search_binary, tmp_path, monkeypatch):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from corki.cli import native_file_search
    from corki.cli.command_completion import CommandCompleter

    monkeypatch.setattr(native_file_search, "executable", lambda: search_binary)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "foo.rs").touch()
    (tmp_path / "longfoogap.rs").touch()
    (tmp_path / "café 空目录").mkdir()

    async def collect(query):
        completer = CommandCompleter(tmp_path)
        return [
            row
            async for row in completer.get_completions_async(Document("@" + query), CompleteEvent())
        ]

    rows = asyncio.run(collect("foo"))
    assert rows[0].reference.label == "nested/foo.rs"
    rows = asyncio.run(collect("cafe"))
    assert len(rows) == 1 and rows[0].reference.description == "Directory"
    assert rows[0].text == '"café 空目录"'
    assert rows[0].reference.file_score is not None
