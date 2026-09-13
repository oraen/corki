"""Readable native patch-review changes; rendering never reparses or applies a patch."""

import re
from contextlib import suppress
from pathlib import Path

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from corki.cli.syntax import highlight_code

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _source_lines(content):
    """Match Rust str.lines: LF/CRLF only, with no synthetic final empty line."""
    parts = content.split("\n")
    yield from (part.removesuffix("\r") for part in parts[:-1])
    if parts[-1]:
        yield parts[-1]


def _visible(content):
    return "".join(char if char.isprintable() else " " for char in content.replace("\t", "    "))


def _syntax_rows(rows, path, *, light=False):
    bodies = [content for number, _, content in rows if number is not None]
    if len(bodies) > 10_000 or sum(len(text.encode("utf-8")) + 1 for text in bodies) > 512 * 1024:
        return {}
    highlighted_rows = {}
    block = []

    def finish():
        if not block:
            return
        code = "\n".join(_visible(rows[index][2]) for index in block)
        highlighted = highlight_code(
            code, filename=str(path), theme="default" if light else "monokai"
        )
        if highlighted is not None:
            lines = highlighted.split("\n", allow_blank=True)
            if len(lines) == len(block):
                highlighted_rows.update(zip(block, lines, strict=True))
        block.clear()

    for index, (number, _, _) in enumerate(rows):
        if number is None:
            finish()
        else:
            block.append(index)
    finish()
    return highlighted_rows


def wrap_preview(text, console, width):
    """Hard-wrap generated diff bodies, retaining their gutter and Rich styles."""
    rows = []
    for line in text.split("\n"):
        prefix = line.get_style_at_offset(console, 0).meta.get("diff_gutter") if line else None
        if prefix is None:
            rows.extend(line.wrap(console, max(1, width)))
            continue
        body = line[prefix:]
        sign = line.plain[prefix - 1]
        light = line.get_style_at_offset(console, 0).meta.get("diff_light", False)
        background = None
        if console.color_system == "truecolor":
            background = (
                {"+": "#dafbe1", "-": "#ffebe9"} if light else {"+": "#213a2b", "-": "#4a221d"}
            ).get(sign)
        elif console.color_system == "256":
            background = (
                {"+": "color(194)", "-": "color(224)"}
                if light
                else {"+": "color(22)", "-": "color(52)"}
            ).get(sign)
        available = max(1, width - prefix)
        offsets = []
        columns = 0
        start = 0
        for index, char in enumerate(body.plain):
            size = cell_len(char)
            if columns + size > available and index > start:
                offsets.append(index)
                start = index
                columns = 0
            columns += size
        for index, chunk in enumerate(body.divide(offsets)):
            gutter = line[:prefix] if index == 0 else Text(" " * prefix, style="dim")
            row = gutter + chunk
            if background is not None:
                row.pad_right(max(0, width - row.cell_len))
                row.stylize(Style(bgcolor=background))
            if light and sign in {"+", "-"}:
                gutter_background = None
                foreground = "black"
                if console.color_system == "truecolor":
                    foreground = "#1f2328"
                    gutter_background = {"+": "#aceebb", "-": "#ffcecb"}[sign]
                elif console.color_system == "256":
                    foreground = "color(236)"
                    gutter_background = {"+": "color(157)", "-": "color(217)"}[sign]
                row.stylize(
                    Style(color=foreground, bgcolor=gutter_background, dim=False), 0, prefix - 1
                )
                if background is not None and line.get_style_at_offset(console, 0).meta.get(
                    "diff_plain"
                ):
                    row.stylize(Style(color="default"), prefix)
            rows.append(row)
    return rows


def render_changes(changes, cwd=None, *, light=False):
    result = Text("Changes:\n")
    for entry in sorted(changes, key=lambda entry: entry["path"]):
        change = entry["change"]
        kind = change["kind"]
        rows = []
        if kind in {"add", "delete"}:
            sign = "+" if kind == "add" else "-"
            rows = [
                (index, sign, line)
                for index, line in enumerate(_source_lines(change["content"]), 1)
            ]
        elif kind == "update":
            old = new = None
            for line in _source_lines(change["diff"]):
                hunk = _HUNK.match(line)
                if hunk:
                    if old is not None:
                        rows.append((None, "gap", "⋮"))
                    old, new = map(int, hunk.groups())
                elif old is None and line.startswith(("--- ", "+++ ")):
                    continue
                elif old is not None and line.startswith(("+", "-", " ")):
                    sign = line[0]
                    rows.append((old if sign == "-" else new, sign, line[1:]))
                    old += sign != "+"
                    new += sign != "-"
                else:
                    rows.append((None, "", line))
        else:
            continue
        path = Path(entry["path"])
        if cwd:
            with suppress(ValueError):
                path = path.relative_to(cwd)
        result.append(str(path))
        if change.get("move_path"):
            result.append(" → " + change["move_path"])
        added, removed = (sum(sign == wanted for _, sign, _ in rows) for wanted in ("+", "-"))
        result.append(" (")
        result.append(f"+{added}", style="green")
        result.append(" ")
        result.append(f"-{removed}", style="red")
        result.append(")\n\n")
        gutter = max((len(str(number)) for number, _, _ in rows if number is not None), default=1)
        syntax_rows = _syntax_rows(rows, change.get("move_path") or entry["path"], light=light)
        for index, (number, sign, content) in enumerate(rows):
            if number is None:
                prefix = " " * (gutter + 3) if sign == "gap" else "  "
                result.append(prefix + content + "\n", style="dim")
            else:
                result.append(
                    f"  {number:>{gutter}} ",
                    style=Style(
                        dim=True,
                        meta={
                            "diff_gutter": gutter + 4,
                            "diff_light": light,
                            "diff_plain": index not in syntax_rows,
                        },
                    ),
                )
                # Sanitize before Text.append, which otherwise silently removes
                # some controls and joins adjacent source characters.
                syntax = syntax_rows.get(index)
                color = {"+": "green", "-": "red"}.get(sign)
                if syntax is None:
                    result.append(sign + _visible(content) + "\n", style=color)
                else:
                    result.append(sign, style=color)
                    if sign == "-":
                        syntax.stylize("dim")
                    result.append(syntax)
                    result.append("\n")
        result.append("\n")
    return result
