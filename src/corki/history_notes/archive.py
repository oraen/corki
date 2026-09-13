"""Readable, append-order history projection; never treat active history as the archive."""

import json
from collections import Counter

from corki.context.token_budget import window_identities
from corki.history_notes.output import bounded_records, bounded_text
from corki.prompting.compaction import render_compaction_summary
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    HostedToolItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
)
from corki.protocol.tool_names import split_tool_name
from corki.protocol.tools import ImageAttachment, content_text


def project_history(stored, thread_id):
    """Preserve durable item/window identities; omit private reasoning and silent state."""
    window = 0
    for ordinal, item in enumerate(stored):
        namespace = name = None
        call_metadata = {}
        if isinstance(item, CompactionItem):
            window += 1
            if item.context_reset or item.remote_payload_json is not None:
                continue
            role, text = "user", render_compaction_summary(item.summary)
        elif isinstance(item, ContextItem):
            if item.is_snapshot_only:
                continue
            role, text = str(item.role), item.content
        elif isinstance(item, (UserMessageItem, TurnAbortedItem)):
            role = "user"
            parts = getattr(item, "content_items", ())
            text = content_text(parts) if parts else item.content
        elif isinstance(item, AssistantMessageItem):
            role, text = "assistant", item.content
        elif isinstance(item, BudgetNoticeItem):
            role, text = "developer", item.content
        elif isinstance(item, HostedToolItem):
            payload = json.loads(item.payload_json)
            role, text = "tool", item.payload_json
            namespace, name = payload.get("namespace"), payload.get("name", payload["type"])
        elif isinstance(item, ToolCallItem):
            call = item.call
            call_metadata = {"call_id": str(call.id), "input_kind": str(call.input_kind)}
            if call.parse_error is not None:
                call_metadata["parse_error"] = call.parse_error
            role = "assistant"
            namespace, name = split_tool_name(call.name)
            namespace = namespace or "functions"
            # The stored invocation is a fact, not a fresh serialization of parsed input.
            # Keep malformed/empty input and valid JSON spelling; older scripted calls
            # with only parsed arguments still have a readable fallback.
            text = (
                call.raw_arguments
                if (
                    call.raw_arguments
                    or call.input_kind == "freeform"
                    or call.arguments is None
                    or call.parse_error is not None
                )
                else json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            )
        elif isinstance(item, ToolResultItem):
            call_metadata = {"call_id": str(item.call_id), "is_error": item.is_error}
            role = "tool"
            namespace, name = split_tool_name(item.tool_name)
            namespace = namespace or "functions"
            text = content_text(item.content_items) if item.content_items else item.content
        else:
            continue
        yield {
            **call_metadata,
            "item_id": str(item.id),
            "window_id": f"{thread_id}:{window}",
            "ordinal": ordinal,
            "role": role,
            "tool_namespace": namespace,
            "tool_name": name,
            "text": text or "",
        }


def history_action(action, arguments, stored, thread_id, budget):
    """Apply v2 filters over normalized durable history, with bounded readable results."""
    rows = list(project_history(stored, thread_id))
    if arguments.get("agent_name") not in (None, "/root"):
        rows = []
    if action == "list_windows":
        counts = Counter(r["window_id"] for r in rows)
        windows = [
            {
                "window_id": f"{thread_id}:{index}",
                "context_window_id": identity,
                "item_count": counts[f"{thread_id}:{index}"],
            }
            for index, identity in enumerate(window_identities(stored, thread_id))
        ]
        if arguments.get("agent_name") not in (None, "/root"):
            windows = []
        if arguments.get("recent_first", False):
            windows.reverse()
        return bounded_records("windows", windows, arguments.get("limit", 100), budget)
    for field in ("window_id", "role", "tool_namespace", "tool_name"):
        if arguments.get(field) is not None:
            rows = [row for row in rows if row[field] == arguments[field]]
    if action == "read_item":
        row = next((r for r in rows if r["item_id"] == arguments["item_id"]), None)
        if row is None:
            raise FileNotFoundError("History item was not found in the specified window")
        offset = arguments.get("offset_chars", 0)
        text = row["text"][offset : offset + arguments.get("limit_chars", budget)]
        result = bounded_text(
            {
                **{key: value for key, value in row.items() if key != "text"},
                "offset_chars": offset,
                "text": text,
                "truncated": offset + len(text) < len(row["text"]),
            },
            "text",
            budget - 64,
        )
        result["next_offset_chars"] = offset + len(result["text"]) if result["truncated"] else None
        item = next(item for item in stored if str(item.id) == row["item_id"])
        parts = getattr(item, "content_items", ()) or getattr(item, "attachments", ())
        images = []
        for part in parts:
            if isinstance(part, ImageAttachment) and part.data_url.startswith("data:"):
                header, separator, data = part.data_url.partition(";base64,")
                if separator:
                    images.append({"data": data, "mime_type": header[5:], "detail": part.detail})
        if images:
            result["images"] = images
        return result
    if action == "search_contents":
        rows = [row for row in rows if arguments["query"] in row["text"]]
    elif action != "list_items":
        raise ValueError("Unknown local history action")
    if arguments.get("recent_first", False):
        rows.reverse()
    maximum = arguments.get("max_chars_per_item", 1000)

    def records():
        for row in rows:
            match = row["text"].find(arguments["query"]) if action == "search_contents" else None
            start = max(0, match - min(200, maximum // 4)) if match is not None else 0
            yield {
                **{k: v for k, v in row.items() if k != "text"},
                **({"match_offset_chars": match} if match is not None else {}),
                "preview_offset_chars": start,
                "truncated_content": row["text"][start : start + maximum],
                "truncated": start > 0 or len(row["text"]) > maximum,
            }

    return bounded_records("items", records(), arguments.get("limit", 100), budget)
