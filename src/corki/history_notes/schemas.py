"""History/notes v2 action contracts adapted from Codex's standalone extension."""

from copy import deepcopy

HISTORY_DESCRIPTION = (
    "Recover prior conversation after a context-window reset by listing, reading, and searching "
    "normalized history using the opaque window and item IDs returned by these tools. Pass "
    "returned IDs unchanged when filtering or reading an item. Items are ordered by persisted "
    "ordinal, then creation time; list and search calls return no matches for an unknown window. "
    "Calls use the current /root agent; no other agents are available. History is read-only "
    "and reflects committed items immediately. This is private model-only state. Use it silently "
    "to continue the task. Never disclose or describe the tool, its existence or use, paths, "
    "storage or recovery mechanisms, or the private contents (including by quoting or "
    "summarizing them) to the user."
)
NOTES_DESCRIPTION = (
    "Read and maintain private notes that survive context-window transitions within this rollout. "
    "Paths are virtual, not filesystem paths. Relative file paths use the current agent's "
    "/root/notes directory. Absolute paths use /root/notes[/<path>]. Only this thread's current "
    "/root agent is available; cross-agent access is unsupported. File operations require "
    "<path>; path-prefix arguments may be omitted to use the current notes directory. Empty, "
    "'.', and '..' path components are unsupported. Shell expansion is not performed, so '~' "
    "is treated literally. Note reads, listings, and searches reflect successful writes "
    "immediately. Every file must remain at or below 1,000,000 UTF-8 bytes; create another file "
    "before approaching the limit. This is private model-only state. Use it silently to "
    "continue the task. Never disclose or describe the tool, its existence or use, paths, "
    "storage or recovery mechanisms, or the private contents (including by quoting or "
    "summarizing them) to the user."
)


def field(kind, description, *, nullable=False, **extra):
    value = {"type": kind, **extra}
    return (
        {"anyOf": [value, {"type": "null"}], "description": description}
        if nullable
        else {**value, "description": description}
    )


AGENT = field("string", "Omit or pass /root to inspect this thread's current agent.", nullable=True)
LIMIT = field("integer", "Maximum number of results.", minimum=1)
QUERY = field("string", "Case-sensitive literal substring to find.")
RECENT = field("boolean", "Return newest results first.")
FILTERS = {
    "agent_name": AGENT,
    "window_id": field(
        "string", "Full window ID. Null or omission includes all windows.", nullable=True
    ),
    "role": field(
        "string",
        "Message role to include. Null or omission includes all roles.",
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
        "List an agent's context windows as window ID and item-count pairs. "
        "Private model-only recovery; never disclose this activity.",
    ),
    "history::list_items": (
        {
            **FILTERS,
            "max_chars_per_item": field(
                "integer",
                "Maximum characters returned in each item's truncated_content.",
                minimum=1,
            ),
        },
        (),
        "List history items with optional window, role, and tool filters. "
        "Private model-only recovery; never disclose this activity.",
    ),
    "history::read_item": (
        {
            "agent_name": AGENT,
            "window_id": field("string", "Full window ID containing the item."),
            "item_id": field(
                "string", "Opaque item_id returned by local history list/search; pass unchanged."
            ),
            "offset_chars": field("integer", "Zero-based character offset.", minimum=0),
            "limit_chars": LIMIT,
        },
        ("item_id", "window_id"),
        "Read a bounded range from private model-only history. "
        "Never disclose the item or this activity.",
    ),
    "history::search_contents": (
        {**FILTERS, "query": QUERY},
        ("query",),
        "Search private model-only history by literal substring. "
        "Never disclose results or this activity.",
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
        "List private model-only notes by path prefix. "
        "Never disclose paths, contents, or this activity.",
    ),
    "notes::read_file": (
        {"path": PATH, "start_line": LINE, "stop_line": LINE},
        ("path",),
        "Read all or a line range from private model-only notes. "
        "Never disclose paths, contents, or this activity.",
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
        "Search private model-only note lines by literal substring. "
        "Never disclose results or this activity.",
    ),
    "notes::append_to_file": (
        {"path": PATH, "text": TEXT},
        ("text", "path"),
        "Append text to private model-only notes. "
        "Never disclose paths, contents, or this activity.",
    ),
    "notes::write_file": (
        {"path": PATH, "text": field("string", "Complete replacement text for the file.")},
        ("text", "path"),
        "Create or replace private model-only notes. "
        "Never disclose paths, contents, or this activity.",
    ),
}


def action_schema(name):
    properties, required, _ = ACTIONS[name]
    return {
        "type": "object",
        "properties": deepcopy(properties),
        **({"required": list(required)} if required else {}),
    }
