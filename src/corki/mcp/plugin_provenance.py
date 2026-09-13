"""Model-facing notes derived from trusted provenance after raw catalog caching."""

from corki.config.mcp_headers import RUST_WHITESPACE


def plugin_tool_description(description, names: tuple[str, ...]):
    if not names:
        return description
    if len(names) == 1:
        note = f"This tool is part of plugin `{names[0]}`."
    else:
        note = "This tool is part of plugins " + ", ".join(f"`{n}`" for n in names) + "."
    text = (description or "").strip(RUST_WHITESPACE)
    if not text:
        return note
    return text + (" " if text[-1] in ".!?" else ". ") + note
