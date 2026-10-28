"""Read-only, paginated view of the core's canonical display history."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from corki.cli.history_projection import HistoryProjection
from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.sessions.display import DisplayItemsCursor
from corki.storage.display_pages import read_display_items_page


def history(store, identity, params, live):
    row = store.connection(identity)
    if not row:
        raise ValueError("接入不存在")
    group = next(g for g in store.conversation_list() if g["id"] == row["conversation_id"])
    thread = group["thread"]
    before = params.get("before")
    if before is not None and (type(before) is not int or not 0 <= before < 2**63):
        raise ValueError("历史游标无效")
    if params.get("thread") not in (None, thread):
        raise ValueError("会话已重置，请刷新")
    database = store.home / "sessions/corki.db"
    messages, cursor = [], None
    start, end = None, None
    if database.is_file():

        @contextmanager
        def connect():
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

        page = read_display_items_page(
            connect,
            ThreadId(thread),
            DisplayItemsCursor(ThreadId(thread), before) if before is not None else None,
            100,
        )
        cursor = page.next_cursor.before_sequence if page.next_cursor else None
        if page.items:
            start = (
                datetime.fromisoformat(page.items[0].created_at).timestamp()
                if cursor is not None
                else None
            )
            end = (
                datetime.fromisoformat(page.items[-1].created_at).timestamp()
                if before is not None
                else None
            )
        for item in HistoryProjection(page.leading_replacements).feed(page.items):
            if isinstance(item, (UserMessageItem, AssistantMessageItem)):
                content = item.content or "\n".join(
                    getattr(p, "text", "") for p in getattr(item, "content_items", ())
                )
                messages.append(
                    {
                        "id": str(item.id),
                        "role": "user" if isinstance(item, UserMessageItem) else "assistant",
                        "content": content[:120000],
                        "created_at": item.created_at,
                    }
                )
    active = live.get(thread, {}) if before is None else {}
    known = {m["id"] for m in messages}
    messages.extend(dict(m) for key, m in active.items() if key not in known)
    failed = store.http_queue.failed_answers(
        identity, thread, [m["id"] for m in messages if m["role"] == "assistant"]
    )
    for message in messages:
        message["delivery_failed"] = message["id"] in failed
    for delivery in store.http_queue.failed_standalone(identity, thread, start, end):
        payload = json.loads(delivery["reply"])
        messages.append(
            {
                "id": "http-reply:" + payload["reply_id"],
                "role": "assistant",
                "content": payload["text"],
                "delivery_failed": True,
                "created_at": datetime.fromtimestamp(delivery["updated"], UTC).isoformat(),
            }
        )
    messages.sort(key=lambda m: m["created_at"])
    return {"thread": thread, "messages": messages, "before": cursor, "active": thread in live}


def bind_legacy_failures(store, channel, rows):
    """Upgrade old blocked replies without attaching their label to a later turn."""
    database = store.home / "sessions/corki.db"
    if not database.is_file():
        return
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        for row in rows:
            found = connection.execute(
                "SELECT id,payload_json FROM conversation_items WHERE thread_id=? "
                "AND kind='assistant_message' AND created_at<=? ORDER BY sequence DESC LIMIT 1",
                (row["thread"], datetime.fromtimestamp(row["updated"], UTC).isoformat()),
            ).fetchone()
            if found and json.loads(found[1]).get("content") == json.loads(row["reply"]).get(
                "text"
            ):
                store.http_queue.bind_answer(channel, row["id"], row["thread"], found[0])
    finally:
        connection.close()
