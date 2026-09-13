"""Display-only normalization of closed Markdown fences containing tables."""

import re

from corki.cli.stream_table import _segments, _unquote


def _scan_line(line):
    line = line.removesuffix("\n")
    indent = 0
    for char in line:
        if char not in " \t":
            break
        indent += 4 if char == "\t" else 1
        if indent >= 4:
            return None
    return line.lstrip(" \t")


def _contains_table(lines, quoted):
    previous_header = False
    for line in lines:
        text = _unquote(line) if quoted else line
        parts = _segments(text, strip_quotes=False)
        delimiter = parts is not None and all(re.fullmatch(r":?-{3,}:?", p) for p in parts)
        if previous_header and delimiter:
            return True
        previous_header = parts is not None and any(parts) and not delimiter
    return False


def unwrap_markdown_fences(source: str) -> str:
    """Keep partial/code examples literal; remove only table wrapper lines.

    Callers retain the original source for transcript and streaming offsets.
    In particular, closing a candidate may invalidate previously parsed blocks.
    """
    if "```" not in source and "~~~" not in source:
        return source
    lines = source.split("\n")
    lines = [line + "\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    out = []
    active = None
    opening = 0
    for index, line in enumerate(lines):
        trimmed = _scan_line(line)
        if active is None:
            match = (
                re.match(r"(`{3,}|~{3,})(.*)", _unquote(trimmed)) if trimmed is not None else None
            )
            if match is None:
                out.append(line)
                continue
            marker, info = match.groups()
            language = info.split()[0].lower() if info.split() else ""
            active = (marker, trimmed.startswith(">"), language in {"md", "markdown"})
            opening = index
            continue
        marker, quoted, markdown = active
        candidate = _unquote(trimmed) if trimmed is not None and quoted else trimmed
        closed = (
            candidate is not None
            and (not quoted or trimmed.startswith(">"))
            and re.fullmatch(re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", candidate)
        )
        if closed:
            body = lines[opening + 1 : index]
            out.extend(
                body if markdown and _contains_table(body, quoted) else lines[opening : index + 1]
            )
            active = None
    if active is not None:
        out.extend(lines[opening:])
    return "".join(out)
