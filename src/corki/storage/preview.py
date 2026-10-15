"""Sticky thread previews derived from accepted canonical user history."""

import json
import sqlite3

from corki.protocol.items import ConversationItem, UserMessageItem, item_from_payload
from corki.protocol.tools import AudioAttachment, ImageAttachment, TextContent

# Rust str::trim follows Unicode White_Space, unlike Python's additional C0
# separators. Preserve those separators as actual user evidence.
_WHITESPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
_USER_PREFIX = "## My request for Codex:"


def user_preview(item: ConversationItem) -> str:
    """Project accepted input only; retained copies are not new user evidence."""
    if not isinstance(item, UserMessageItem) or item.retained_from_id is not None:
        return ""
    parts = item.content_items
    text = (
        "\n".join(part.text for part in parts if isinstance(part, TextContent))
        if parts
        else item.content
    )
    if _USER_PREFIX in text:
        text = text.split(_USER_PREFIX, 1)[1]
    text = text.strip(_WHITESPACE)
    if text:
        return text
    if item.attachments or any(isinstance(part, ImageAttachment) for part in parts):
        return "[Image]"
    if any(isinstance(part, AudioAttachment) for part in parts):
        return "[Audio]"
    return ""


def migrate_thread_previews(connection: sqlite3.Connection) -> None:
    """Backfill once, in the schema owner's transaction after legacy item import."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
    if "preview" in columns:
        return
    connection.execute("ALTER TABLE threads ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
    last_populated: str | None = None
    for row in connection.execute(
        "SELECT thread_id, payload_json FROM conversation_items "
        "WHERE kind='user_message' ORDER BY thread_id, sequence"
    ):
        if row["thread_id"] == last_populated:
            continue
        item = item_from_payload("user_message", json.loads(row["payload_json"]))
        if value := user_preview(item):
            connection.execute("UPDATE threads SET preview=? WHERE id=?", (value, row["thread_id"]))
            last_populated = row["thread_id"]
