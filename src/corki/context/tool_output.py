"""Ordered output budgeting: images/ciphertext survive; audio is charged as a whole."""

from corki.context.tokens import estimate_audio_tokens, estimate_text_tokens
from corki.context.truncation import truncate_text
from corki.protocol.tools import AudioAttachment, TextContent, ToolContent


def truncate_content(
    items: tuple[ToolContent, ...], tokens: int, *, formatted_text: bool = False
) -> tuple[ToolContent, ...]:
    if formatted_text and all(isinstance(item, TextContent) for item in items):
        combined = ""
        for item in items:
            if combined:
                combined += "\n"
            combined += item.text
        cost = estimate_text_tokens(combined)
        if cost <= tokens:
            return items
        snippet = _snippet(combined, max(0, tokens))
        return (
            TextContent(
                f"Warning: truncated output (original token count: {cost})\n"
                f"Total output lines: {len(combined.splitlines())}\n\n{snippet}"
            ),
        )
    output = []
    omitted_text = omitted_audio = 0
    remaining = max(0, tokens)
    for item in items:
        if isinstance(item, TextContent):
            if not item.text:
                continue
            if not remaining:
                omitted_text += 1
                continue
            cost = estimate_text_tokens(item.text)
            if cost <= remaining:
                output.append(item)
                remaining -= cost
            else:
                # Use a byte-safe bound for non-ASCII instead of assuming that
                # four Python characters always cost one token.
                snippet = _snippet(item.text, remaining)
                if snippet:
                    output.append(TextContent(snippet))
                else:
                    omitted_text += 1
                remaining = 0
        elif isinstance(item, AudioAttachment):
            cost = estimate_audio_tokens(item)
            if cost <= remaining:
                output.append(item)
                remaining -= cost
            else:
                omitted_audio += 1
        else:
            # Images and opaque encrypted blocks are indivisible. Ciphertext is
            # already budgeted at its source; the full request still counts it.
            output.append(item)
    if omitted_text:
        output.append(TextContent(f"[omitted {omitted_text} text items ...]"))
    if omitted_audio:
        output.append(TextContent(f"[omitted {omitted_audio} audio items ...]"))
    return tuple(output)


def _snippet(text: str, tokens: int) -> str:
    snippet = truncate_text(text, tokens * 4)
    while snippet and estimate_text_tokens(snippet) > tokens:
        snippet = truncate_text(snippet, max(0, len(snippet) // 2))
    return snippet
