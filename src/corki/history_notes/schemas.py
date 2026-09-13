"""History/notes v2 action contracts adapted from Codex's standalone extension."""

from copy import deepcopy

HISTORY_DESCRIPTION = (
    "Recover prior conversation after a context-window reset by listing, reading and searching "
    "normalized history. Pass returned window and item IDs unchanged. Items are ordered by "
    "persisted ordinal then creation time; unknown windows return no matches. "
    "Only /root is available. "
    "History is read-only and reflects committed items. This is private model-only recovery state. "
    "Use it "
    "silently to continue the task; do not disclose its contents, paths or recovery mechanism."
)
NOTES_DESCRIPTION = (
    "Maintain private notes across context windows within this rollout. Paths are virtual, not "
    "filesystem paths. Relative paths use the current agent's <agent_name>/notes directory; "
    "cross-agent paths must be absolute: <agent_name>/notes/<path>. File operations require a "
    "path; prefixes default to the current notes directory. Empty, '.' and '..' components are "
    "unsupported; shell expansion is not performed and '~' is literal. Reads reflect successful "
    "writes immediately, including listings/searches. Each file is limited to "
    "1,000,000 UTF-8 bytes. Private model-only state: use silently, do not disclose contents, "
    "paths or the recovery mechanism."
)


def field(kind, description, *, nullable=False, **extra):
    value = {"type": kind, **extra}
    return (
        {"anyOf": [value, {"type": "null"}], "description": description}
        if nullable
        else {**value, "description": description}
    )


AGENT = field("string", "Agent name, absolute or relative to the current agent.", nullable=True)
LIMIT = field("integer", "Maximum number of results.", minimum=1)
QUERY = field("string", "Case-sensitive literal substring to find.")
RECENT = field("boolean", "Return newest results first.")
FILTERS = {
    "agent_name": AGENT,
    "window_id": field("string", "Full window ID; omission includes all windows.", nullable=True),
    "role": field(
        "string",
        "Message role filter.",
        nullable=True,
        enum=["user", "assistant", "tool", "system", "developer"],
    ),
    "tool_namespace": field(
        "string", "Tool namespace filter; excludes non-tool messages.", nullable=True
    ),
    "tool_name": field("string", "Tool name filter; excludes non-tool messages.", nullable=True),
    "limit": LIMIT,
    "recent_first": RECENT,
}
PATH = field("string", "Virtual note file path.")
TEXT = field("string", "Text written exactly as provided.")
LINE = field(
    "integer",
    "Inclusive 1-based line number; negative counts backward from the final line.",
    nullable=True,
)
ACTIONS = {
    "history::list_windows": (
        {"agent_name": AGENT, "limit": LIMIT, "recent_first": RECENT},
        (),
        "List context windows and item counts.",
    ),
    "history::list_items": (
        {**FILTERS, "max_chars_per_item": LIMIT},
        (),
        "List history items and bounded content previews.",
    ),
    "history::read_item": (
        {
            "agent_name": AGENT,
            "window_id": field("string", "Full window ID containing the item."),
            "item_id": field("string", "Item ID from its trailing [id: ...] marker."),
            "offset_chars": field("integer", "Zero-based character offset.", minimum=0),
            "limit_chars": LIMIT,
        },
        ("item_id", "window_id"),
        "Read a bounded history item range.",
    ),
    "history::search_contents": (
        {**FILTERS, "query": QUERY},
        ("query",),
        "Search history content by literal substring.",
    ),
    "notes::list_files_by_prefix": (
        {
            "prefix": field("string", "Note path prefix.", nullable=True),
            "max_results": LIMIT,
            "file_order_by": field(
                "string", "Sort field.", enum=["name", "created_at", "updated_at"]
            ),
            "file_order": field("string", "Sort direction.", enum=["ascending", "descending"]),
        },
        (),
        "List note files by prefix.",
    ),
    "notes::read_file": (
        {"path": PATH, "start_line": LINE, "stop_line": LINE},
        ("path",),
        "Read all or a line range of a note.",
    ),
    "notes::search_contents": (
        {
            "query": QUERY,
            "max_matches_per_file": LIMIT,
            "max_files": LIMIT,
            "recent_file_first": RECENT,
            "path_prefix": field("string", "Note path prefix.", nullable=True),
        },
        ("query",),
        "Search note lines by literal substring.",
    ),
    "notes::append_to_file": (
        {"path": PATH, "text": TEXT},
        ("text", "path"),
        "Append text to a note.",
    ),
    "notes::write_file": (
        {"path": PATH, "text": TEXT},
        ("text", "path"),
        "Create or replace a note.",
    ),
}


def action_schema(name):
    properties, required, _ = ACTIONS[name]
    return {
        "type": "object",
        "properties": deepcopy(properties),
        **({"required": list(required)} if required else {}),
    }
