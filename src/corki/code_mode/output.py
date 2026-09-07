"""Source-specific cell body budgeting before the unbudgeted status header."""

from corki.context.function_output import truncate_function_content
from corki.context.hosted_output import truncate_output_text
from corki.protocol.tools import TextContent, ToolContent
from corki.protocol.truncation import TruncationPolicy


def truncate_cell_output(items: tuple[ToolContent, ...], tokens: int) -> tuple[ToolContent, ...]:
    policy = TruncationPolicy("tokens", tokens)
    if not all(isinstance(item, TextContent) for item in items):
        return truncate_function_content(items, policy)
    combined = ""
    for item in items:
        if combined:
            combined += "\n"
        combined += item.text
    size = len(combined.encode("utf-8"))
    if size <= policy.byte_budget:
        return items
    lines = combined.count("\n") + (not combined.endswith("\n"))
    return (
        TextContent(
            f"Warning: truncated output (original token count: {(size + 3) // 4})\n"
            f"Total output lines: {lines}\n\n{truncate_output_text(combined, policy)}"
        ),
    )
