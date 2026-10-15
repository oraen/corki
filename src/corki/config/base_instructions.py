"""Resolve host-owned base instructions once, before Runtime construction."""

from pathlib import Path


def resolve_base_instructions(document: dict) -> str | None:
    text = document.get("instructions")
    if text is not None and not isinstance(text, str):
        raise ValueError("instructions must be a string")
    path = document.get("model_instructions_file")
    if path is None:
        return text
    if not isinstance(path, str) or not path:
        raise ValueError("model_instructions_file must be a non-empty path string")
    # The layer loader resolves source-relative paths before merging layers.
    try:
        content = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"failed to read model_instructions_file {path}: {error}") from error
    if not content:
        raise ValueError(f"model_instructions_file is empty: {path}")
    return content
