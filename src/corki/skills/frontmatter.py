"""Codex skill header extraction and limited line-oriented scalar repair."""

import re

import yaml

from corki.skills import yaml_fields


def extract(contents: str) -> str:
    lines = iter(contents.split("\n"))
    if next(lines, "").strip() != "---":
        raise ValueError("missing YAML frontmatter delimited by ---")
    header = []
    for line in lines:
        if line.strip() == "---":
            if not header:
                break
            return "\n".join(header)
        header.append(line.removesuffix("\r"))
    raise ValueError("missing YAML frontmatter delimited by ---")


def repair(header: str) -> str | None:
    changed, block_indent, lines = False, None, []
    for line in header.split("\n"):
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                lines.append(line)
                continue
            block_indent = None
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or (value and not value[0].isspace()):
            lines.append(line)
            continue
        scalar = value.lstrip()
        leading, comment = value[: len(value) - len(scalar)], ""
        for i, char in enumerate(scalar):
            if char == "#" and (i == 0 or scalar[i - 1].isspace()):
                start = len(scalar[:i].rstrip())
                scalar, comment = scalar[:start], scalar[start:]
                break
        scalar = scalar.rstrip()
        if not scalar or scalar[0] in "'\"":
            lines.append(line)
            continue
        if scalar[0] in "|>":
            block_indent = indent
            lines.append(line)
            continue
        invalid_flow = False
        if scalar[0] in "[{@`":
            try:
                yaml_fields.parse(scalar)
            except yaml.YAMLError:
                invalid_flow = True
        if re.search(r":\s", scalar) or invalid_flow:
            lines.append(key + ":" + leading + "'" + scalar.replace("'", "''") + "'" + comment)
            changed = True
        else:
            lines.append(line)
    return "\n".join(lines) if changed else None


def parse(contents: str, default_name: str) -> tuple[str, str, str | None]:
    header = extract(contents)

    def read(text):
        values = yaml_fields.fields(yaml_fields.parse(text), ("name", "description", "metadata"))
        metadata = (
            yaml_fields.fields(values["metadata"], ("short-description",))
            if "metadata" in values
            else {}
        )
        return (
            yaml_fields.string(values.get("name")),
            yaml_fields.string(values.get("description")),
            yaml_fields.string(metadata.get("short-description")),
        )

    try:
        name, description, short = read(header)
    except (yaml.YAMLError, ValueError) as original:
        repaired = repair(header)
        if repaired is None:
            raise
        try:
            name, description, short = read(repaired)
        except (yaml.YAMLError, ValueError):
            raise original from None
    name = " ".join((name or "").split()) or default_name
    description = " ".join((description or "").split())
    short = " ".join((short or "").split()) or None
    if not name or len(name) > 64:
        raise ValueError("name must contain between 1 and 64 characters")
    if not description:
        raise ValueError("missing non-empty description")
    return name, description, short
