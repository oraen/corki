import pytest

from corki.cli.reference_completion import Reference, fuzzy_match, reference_match_score


@pytest.mark.parametrize(
    ("label", "query", "expected"),
    [
        ("hello", "hl", ((0, 2), -99)),
        ("İstanbul", "is", ((0, 1), -99)),
        ("straße", "strasse", None),
        ("abc", "abc", ((0, 1, 2), -100)),
        ("a-b-c", "abc", ((0, 2, 4), -98)),
        ("file_name", "file", ((0, 1, 2, 3), -100)),
        ("my_file_name", "file", ((3, 4, 5, 6), 0)),
        ("anything", "", ((), 2**31 - 1)),
        ("FooBar", "foO", ((0, 1, 2), -100)),
        ("İ", "i\u0307", ((0,), -100)),
        ("中文文件", "中件", ((0, 3), -98)),
    ],
)
def test_codex_fuzzy_examples(label, query, expected):
    assert fuzzy_match(label, query) == expected


def test_tool_ranking_prioritizes_display_hits_before_alias_hits():
    display_hit = Reference("@internal", "", display_name="a-b", search_terms=("ab",))
    alias_hit = Reference("@ab", "", display_name="unrelated", search_terms=("ab",))
    assert reference_match_score(display_hit, "ab") == (0, -99)
    assert reference_match_score(alias_hit, "ab") == (1, -100)
    assert reference_match_score(display_hit, "ab") < reference_match_score(alias_hit, "ab")
    assert reference_match_score(display_hit, "") == (0, 0)
