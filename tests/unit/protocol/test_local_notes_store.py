import concurrent.futures

import pytest

from corki.history_notes.notes_store import NotesStore


def call(store, action, **arguments):
    return store.call(action, arguments, 20000)


@pytest.mark.parametrize(
    "path", ["", ".", "..", "../p", "a//p", "p/", "/other/notes/p", "/root/notes/../p", "a\0b"]
)
def test_invalid_virtual_paths_do_not_write(tmp_path, path):
    store = NotesStore(tmp_path / "notes.db", "thread")
    with pytest.raises(ValueError):
        call(store, "write_file", path=path, text="not written")
    assert call(store, "list_files_by_prefix")["files"] == []


def test_exact_utf8_file_limit_and_failed_append_are_atomic(tmp_path):
    store = NotesStore(tmp_path / "notes.db", "thread")
    text = "界" * 333333 + "."
    assert call(store, "write_file", path="p", text=text)["bytes"] == 1_000_000
    for action in ("append_to_file", "write_file"):
        with pytest.raises(ValueError, match="not applied"):
            call(store, action, path="p", text=text + "x")
    result = store.call("read_file", {"path": "p"}, 1_100_000)
    assert result["text"] == text and not result["truncated"]


@pytest.mark.parametrize(
    "start,stop,expected",
    [
        (None, None, "a\r\nb\n界\n"),
        (-1, None, "界\n"),
        (-2, -1, "b\n界\n"),
        (1, 1, "a\r\n"),
        (4, None, ""),
        (2, 1, ""),
    ],
)
def test_note_line_ranges_preserve_exact_newlines(tmp_path, start, stop, expected):
    store = NotesStore(tmp_path / "notes.db", "thread")
    call(store, "write_file", path="p", text="a\r\nb\n界\n")
    assert call(store, "read_file", path="p", start_line=start, stop_line=stop)["text"] == expected


def test_isolation_prefix_literals_ordering_and_line_search(tmp_path):
    first, second = (NotesStore(tmp_path / "notes.db", thread) for thread in ("one", "two"))
    call(first, "write_file", path="a%/p", text="Hit\nHit again\nhit\n")
    call(first, "append_to_file", path="~/p", text="Hit second\n")
    call(second, "write_file", path="a%/p", text="OTHER_THREAD")
    assert call(second, "read_file", path="/root/notes/a%/p")["text"] == "OTHER_THREAD"
    result = call(first, "list_files_by_prefix", prefix="a%/")
    assert [r["path"] for r in result["files"]] == ["/root/notes/a%/p"]
    call(first, "append_to_file", path="a%/p", text="final")
    ordered = call(
        first, "list_files_by_prefix", file_order_by="updated_at", file_order="descending"
    )
    assert ordered["files"][0]["path"] == "/root/notes/a%/p"
    matches = call(first, "search_contents", query="Hit", max_matches_per_file=1, max_files=1)
    assert matches["matches"] == [{"path": "/root/notes/a%/p", "line": 1, "text": "Hit\n"}]
    assert matches["truncated"]
    assert call(first, "search_contents", query="hit")["matches"][0]["line"] == 3


def test_concurrent_appends_do_not_lose_updates(tmp_path):
    store = NotesStore(tmp_path / "notes.db", "thread")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: call(store, "append_to_file", path="p", text=f"{i}\n"), range(24)))
    assert sorted(int(v) for v in call(store, "read_file", path="p")["text"].splitlines()) == list(
        range(24)
    )


def test_small_budget_retains_json_and_full_identity(tmp_path):
    import json

    store = NotesStore(tmp_path / "notes.db", "thread")
    call(store, "write_file", path="界.md", text='"界\\' * 1000)
    for action, args in (("read_file", {"path": "界.md"}), ("search_contents", {"query": "界"})):
        result = store.call(action, args, 300)
        assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 300
        assert result["truncated"]
        if action == "read_file":
            assert result["path"] == "/root/notes/界.md"
