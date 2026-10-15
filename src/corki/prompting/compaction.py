"""One local checkpoint projection shared by wire, budget and readable history."""

from functools import cache

from corki.prompting.store import PromptStore

SUMMARY_CONTENT_KIND = "compaction.summary"


@cache
def compaction_summary_prefix() -> str:
    """Return the fixed native handoff prefix, including its one separator newline."""
    return PromptStore().render("context/compaction_summary_prefix").rstrip("\n") + "\n"


def render_compaction_summary(summary: str) -> str:
    """Project a bare durable summary without modifying its text or stored identity."""
    return compaction_summary_prefix() + summary
