from corki.cli.backtrack import editable_prompts
from corki.cli.backtrack_pages import BacktrackPages, parts
from corki.protocol.items import AssistantMessageItem, ToolResultItem, UserMessageItem


def test_large_tool_history_is_paged_without_omission_or_unbounded_view():
    items = (
        UserMessageItem("first 中文", "turn1"),
        ToolResultItem("call", "exec_command", "界" * 8_000_001, "turn1", is_error=True),
        UserMessageItem("second", "turn2"),
        AssistantMessageItem("done", "turn2", "step2"),
    )
    pages = BacktrackPages(items, editable_prompts(items))
    cursor, chunks = (0, 0), []
    while cursor is not None:
        text, _, following = pages.page(cursor)
        assert len(text) <= 32768
        assert text
        if following is not None:
            assert pages.previous(following) == cursor
        chunks.append(text)
        cursor = following
    assert "".join(chunks) == "".join(part for item in items for part in parts(item))
    assert pages.prompts == (0, 2)
    text, ranges, _ = pages.page((pages.prompts[1], 0))
    assert text[slice(*ranges[2])] == "› second\n\n"
    assert "[Tool failed]" in "".join(chunks)


def test_control_sequences_are_sanitized_at_page_boundaries():
    items = (UserMessageItem("a\x1b[2Jb\x00c", "turn"),)
    pages = BacktrackPages(items, items, page_chars=4)
    cursor, chunks = (0, 0), []
    while cursor is not None:
        text, _, cursor = pages.page(cursor)
        assert "\x1b" not in text and "\x00" not in text
        chunks.append(text)
    assert "a" in "".join(chunks) and "c" in "".join(chunks)
