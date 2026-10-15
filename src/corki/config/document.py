"""JSON settings with comments, trailing commas, and legacy TOML read support."""

import json
import math
import re
import tomllib
from pathlib import Path


def _clean(contents: str) -> str:
    chars = list(contents)
    index = 0
    while index < len(contents):
        if contents[index] == '"':
            index += 1
            while index < len(contents):
                if contents[index] == "\\":
                    index += 2
                elif contents[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        elif contents.startswith("//", index) or contents.startswith("/*", index):
            start = index
            if contents.startswith("//", index):
                end = contents.find("\n", index)
                index = len(contents) if end < 0 else end
            else:
                end = contents.find("*/", index + 2)
                if end < 0:
                    raise ValueError("Unterminated settings comment")
                index = end + 2
            for offset in range(start, index):
                if chars[offset] not in "\r\n":
                    chars[offset] = " "
        else:
            index += 1
    clean = "".join(chars)
    # Match strings first so commas inside strings are never changed.
    return re.sub(
        r'"(?:[^"\\]|\\.)*"|,(?=\s*[}\]])',
        lambda match: " " if match.group() == "," else match.group(),
        clean,
    )


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate settings key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite numbers are not valid settings")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        return _constant(value)
    return number


def loads(contents: str, path: Path | None = None) -> dict:
    if path is not None and path.suffix == ".toml":
        return tomllib.loads(contents)
    clean = _clean(contents)
    if not clean.strip():
        return {}
    try:
        value = json.loads(
            clean, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float
        )
    except json.JSONDecodeError as error:
        # Do not echo source lines: configuration can contain credentials.
        raise ValueError(
            f"Invalid settings JSON at line {error.lineno}, column {error.colno}"
        ) from None
    if not isinstance(value, dict):
        raise ValueError("Settings must contain a JSON object")
    return value


def dumps(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def select_file(folder: Path) -> Path:
    current = folder / "settings.json"
    legacy = folder / "config.toml"
    return (
        legacy if not current.exists() and not current.is_symlink() and legacy.exists() else current
    )


def set_value(contents: str, segments: tuple[str, ...], value: object) -> str:
    """Replace one literal-key value while retaining unrelated JSONC decoration."""
    if not segments or any(not isinstance(key, str) for key in segments):
        raise ValueError("config edits require literal string keys")
    loads(contents)
    if not _clean(contents).strip():
        contents += "\n{}\n"
    clean = _clean(contents)
    decoder = json.JSONDecoder()

    def skip(index):
        while index < len(clean) and clean[index].isspace():
            index += 1
        return index

    def edit(start, remaining):
        if clean[start] != "{":
            raise ValueError("config edit parent is not an object")
        index = skip(start + 1)
        last_end = None
        while clean[index] != "}":
            key, key_end = decoder.raw_decode(clean, index)
            value_start = skip(skip(key_end) + 1)
            _, value_end = decoder.raw_decode(clean, value_start)
            if key == remaining[0]:
                if len(remaining) > 1:
                    return edit(value_start, remaining[1:])
                indent = re.match(r"[ \t]*", contents[contents.rfind("\n", 0, index) + 1 :])[0]
                rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
                rendered = rendered.replace("\n", "\n" + indent)
                return contents[:value_start] + rendered + contents[value_end:]
            last_end = value_end
            index = skip(value_end)
            if clean[index] == ",":
                index = skip(index + 1)
        nested = value
        for key in reversed(remaining[1:]):
            nested = {key: nested}
        line_start = contents.rfind("\n", 0, start) + 1
        indent = re.match(r"[ \t]*", contents[line_start:])[0]
        rendered = json.dumps({remaining[0]: nested}, ensure_ascii=False, indent=2, allow_nan=False)
        body = "\n".join(indent + line for line in rendered.splitlines()[1:-1])
        prefix = contents[:index]
        if last_end is not None and "," not in _clean(contents[last_end:index]):
            # _clean removes an existing trailing comma only when its closing brace is present.
            prefix = contents[:last_end] + "," + contents[last_end:index]
        return prefix.rstrip(" \t") + "\n" + body + "\n" + indent + contents[index:]

    updated = edit(skip(0), segments)
    loads(updated)
    return updated
