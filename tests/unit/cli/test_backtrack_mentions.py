import pytest

from corki.cli.backtrack import prompt_input
from corki.protocol.input_mentions import InputMention
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mail@sample", ()),
        ("@sample/pkg", ()),
        ("@sample\\pkg", ()),
        ("@sample.json", ()),
        ("@sample-next", ()),
        ("@sample_more", ()),
        ("@sample. next", (0,)),
        ("(@sample)", (1,)),
        ("中文@sample中文", (2,)),
        ("@sample @sample", (0,)),
        ("mail@sample @sample/pkg @sample", (24,)),
    ],
)
def test_plugin_restore_uses_plaintext_boundaries_and_single_binding(text, expected):
    selector = InputMention("sample", "plugin://sample@local")
    restored = prompt_input(UserMessageItem(text, "turn", mentions=(selector,)))
    assert tuple(start for start, _, _ in restored.draft.bindings) == expected


def test_same_name_distinct_targets_restore_in_original_order():
    first = InputMention("review", "/first/SKILL.md", "skill")
    second = InputMention("review", "/second/SKILL.md", "skill")
    restored = prompt_input(
        UserMessageItem("$review then $review then $review", "turn", mentions=(first, second))
    )
    assert restored.draft.bindings == ((0, "$review", first), (13, "$review", second))


def test_skill_restore_keeps_codex_dollar_boundaries():
    selector = InputMention("review", "/first/SKILL.md", "skill")
    restored = prompt_input(UserMessageItem("prefix$review:说明", "turn", mentions=(selector,)))
    assert restored.draft.bindings == ((6, "$review", selector),)
