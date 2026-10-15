"""Native text-retention budget is independent of provider request overhead."""

from dataclasses import replace

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.context.window import _retained_user_messages
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import ImageAttachment, TextContent


def test_native_small_budget_keeps_head_tail_and_source_metadata():
    text = "word " * 200
    source = UserMessageItem(
        "not the authoritative parts",
        "original-turn",
        content_items=(TextContent(text), ImageAttachment("data:fixture")),
        content_item_kinds=("user.text", "user.image"),
    )
    result = _retained_user_messages((source,), excluded_ids=set(), token_budget=16)
    assert len(result) == 1
    expected = replace(
        source,
        id=result[0].id,
        content=text[:32] + "…234 tokens truncated…" + text[-32:],
        content_items=(),
        content_item_kinds=("user.text",),
        retained_from_id=source.id,
    )
    assert result == (expected,)
    assert result[0].id != source.id
    assert source.content_items == (TextContent(text), ImageAttachment("data:fixture"))


@pytest.mark.parametrize("annotated", [False, True])
def test_budget_counts_each_user_text_not_message_or_annotation_overhead(annotated):
    values = ("old", "abcdabcd", "中文ab")
    # 2 tokens then 2 tokens. No room for the older message or its metadata.
    sources = tuple(
        UserMessageItem(text, "turn", content_item_kinds=("user.text",) if annotated else None)
        for text in values
    )
    result = _retained_user_messages(sources, excluded_ids=set(), token_budget=4)
    assert [i.content for i in result] == list(values[1:])
    assert [i.retained_from_id for i in result] == [i.id for i in sources[1:]]
    # Copies already retained through another compaction still refer to the original.
    again = _retained_user_messages(result, excluded_ids=set(), token_budget=4)
    assert [i.retained_from_id for i in again] == [i.id for i in sources[1:]]
    assert {i.id for i in again}.isdisjoint({i.id for i in result})


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_exclusion_and_exhaustion_do_not_consume_budget_or_append_empty_old_users(budget):
    empty = UserMessageItem("", "old-turn")
    selected = UserMessageItem("中文ab", "turn")
    protected = UserMessageItem("CURRENT_INPUT", "turn")
    result = _retained_user_messages(
        (empty, selected, protected), excluded_ids={protected.id}, token_budget=budget
    )
    if budget == 0:
        assert result == ()
    else:
        assert len(result) == 1 and result[0].retained_from_id == selected.id
        assert result[0].content == ("…1 tokens truncated…ab" if budget == 1 else selected.content)


@pytest.mark.parametrize("window_budget", [0, 12, 24, 50, 100, 300])
@pytest.mark.parametrize("text", ["HEAD" + "x" * 1000 + "TAIL", "首" + "中" * 1000 + "尾"])
def test_complete_request_cap_accounts_for_marker_unicode_and_annotation(window_budget, text):
    source = UserMessageItem(text, "turn", content_item_kinds=("user.text",))
    result = _retained_user_messages(
        (source,), excluded_ids=set(), token_budget=2000, request_token_budget=window_budget
    )
    assert sum(estimate_item_tokens(i) for i in result) <= window_budget
    if window_budget >= 50:
        assert len(result) == 1
        assert result[0].content.startswith("HEAD" if text.isascii() else "首")
        assert result[0].content.endswith("TAIL" if text.isascii() else "尾")
        if estimate_item_tokens(source) <= window_budget:
            assert result[0].content == text
        else:
            assert "tokens truncated" in result[0].content
    assert source.content == text


def test_complete_request_budget_applies_across_all_selected_messages():
    sources = tuple(UserMessageItem(text, "turn") for text in ("x" * 300, "y" * 100, "last"))
    result = _retained_user_messages(
        sources, excluded_ids=set(), token_budget=2000, request_token_budget=80
    )
    assert sum(estimate_item_tokens(i) for i in result) <= 80
    assert [i.content for i in result[-2:]] == ["y" * 100, "last"]
    assert result[0].retained_from_id == sources[0].id
    assert "tokens truncated" in result[0].content
