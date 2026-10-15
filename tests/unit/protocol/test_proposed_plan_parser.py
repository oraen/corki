"""Map Codex's tagged-line parser boundaries without interpreting Markdown fences."""

import pytest

from corki.protocol.proposed_plan import ProposedPlanParser, split_proposed_plan


@pytest.mark.parametrize("chunk_size", [1, 3, 1000])
@pytest.mark.parametrize(
    ("source", "normal", "plan"),
    [
        ("before\n<proposed_plan>\nstep\n</proposed_plan>\nafter", "before\nafter", "step\n"),
        ("  <proposed_plan> extra\n", "  <proposed_plan> extra\n", None),
        ("<proposed_plan>\nstep", "", "step"),
        ("<proposed_plan>", "", ""),
        ("<proposed_", "<proposed_", None),
        ("</proposed_plan>\n", "</proposed_plan>\n", None),
        ("<proposed_plan>\n<proposed_plan>\n</proposed_plan>", "", "<proposed_plan>\n"),
        ("```\n<proposed_plan>\nx\n</proposed_plan>\n```", "```\n```", "x\n"),
        ("\t<proposed_plan> \r\nx\n </proposed_plan>\t", "", "x\n"),
        ("<proposed_plan>\na\n</proposed_plan>\n<proposed_plan>\nb", "", "b"),
        ("\x1c<proposed_plan>\n", "\x1c<proposed_plan>\n", None),
        ("<proposed_plan>\nx\n</proposed_", "", "x\n</proposed_"),
    ],
)
def test_plan_tags_are_chunk_independent(source, normal, plan, chunk_size):
    parser = ProposedPlanParser()
    segments = []
    for offset in range(0, len(source), chunk_size):
        segments.extend(parser.push(source[offset : offset + chunk_size]))
    segments.extend(parser.finish())
    assert parser.finish() == ()
    assert "".join(s.text for s in segments if s.kind == "normal") == normal
    extracted = None
    for segment in segments:
        if segment.kind == "start":
            extracted = ""
        elif segment.kind == "delta":
            extracted += segment.text
    assert extracted == plan
    assert split_proposed_plan(source) == (normal, plan)


def test_only_an_undecided_line_prefix_waits_for_more_input():
    parser = ProposedPlanParser()
    assert parser.push("<proposed_") == ()
    assert [(s.kind, s.text) for s in parser.push("plan> x")] == [("normal", "<proposed_plan> x")]
    assert [(s.kind, s.text) for s in parser.push(" more")] == [("normal", " more")]
