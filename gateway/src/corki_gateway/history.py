"""Read-only, paginated view of the core's canonical display history."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.sessions.display import DisplayItemsCursor
from corki.storage.display_pages import read_display_items_page


def history(store, identity, params, live):
    conversation = params.get("conversation_id")
    if conversation is not None:
        if (
            not isinstance(conversation, str) or not conversation.isascii()
            or not conversation.isdigit() or len(conversation) > 19
            or not 0 < int(conversation) < 2**63
        ):
            raise ValueError("会话 ID 无效")
        with store.db() as db:
            target = db.execute(
                "SELECT sender_id FROM access_requests WHERE channel=? AND rowid=?",
                (identity, int(conversation)),
            ).fetchone()
        if target is None:
            raise ValueError("会话不存在或不属于此接入")
        params = {**params, "sender": target["sender_id"]}
    row = store.connection(identity)
    if row and row["type_id"] == "dingtalk":
        return store.messages.view(row, params, live, core_history)
    if row and row["type_id"] == "wechat":
        return store.wechat_messages.view(row, params, live, core_history)
    if row and row["type_id"] in ("telegram", "whatsapp"):
        return store.direct_messages.view(row, params, live, core_history)
    return core_history(store, identity, params, live)


def core_history(store, identity, params, live):
    row = store.connection(identity)
    if not row:
        raise ValueError("接入不存在")
    sessions, sessions_before = store.sessions_page(row, params.get("sessions_before"))
    sender = params.get("sender")
    if sender is not None:
        if not isinstance(sender, str) or not 1 <= len(sender) <= 2000:
            raise ValueError("用户标识无效")
        with store.db() as db:
            own = db.execute(
                "SELECT thread FROM user_sessions WHERE binding=? AND sender=?",
                (row["conversation_id"], sender),
            ).fetchone()
        thread = own["thread"] if own else None
        sessions, sessions_before = [], None
    else:
        thread = store.session_thread(row, params.get("session"), sessions)
    session_info = {"sessions": sessions, "sessions_before": sessions_before}
    if params.get("thread") not in (None, thread):
        raise ValueError("会话已重置，请刷新")
    if thread is None:
        return {**session_info, "thread": None, "messages": [], "before": None, "active": False}
    before = params.get("before")
    if before is not None and (type(before) is not int or not 0 <= before < 2**63):
        raise ValueError("历史游标无效")
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
        visible = page.items
        turn_ids = {str(item.turn_id) for item in visible if isinstance(item, AssistantMessageItem)}
        turns = {}
        if turn_ids:
            with connect() as connection:
                turns = {
                    r["id"]: r
                    for r in connection.execute(
                        "SELECT t.id,t.status,(SELECT i.id FROM chat_history i "
                        "WHERE i.thread_id=t.thread_id AND i.turn_id=t.id "
                        "AND i.kind='assistant_message' AND t.status='completed' "
                        "AND json_extract(i.payload_json,'$.content')=t.final_answer "
                        "ORDER BY i.sequence DESC LIMIT 1) AS answer_id "
                        "FROM chat_turns t WHERE t.thread_id=? AND t.id IN ("
                        + ",".join("?" for _ in turn_ids)
                        + ")",
                        (thread, *turn_ids),
                    )
                }
        for item in visible:
            if isinstance(item, AssistantMessageItem):
                turn = turns.get(str(item.turn_id))
                if turn is not None:
                    if turn["status"] != "completed" or str(item.id) != turn["answer_id"]:
                        continue
                elif item.phase == "commentary":
                    # Legacy imports may have no turn metadata.
                    continue

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
    return {
        **session_info,
        "thread": thread,
        "messages": messages,
        "before": cursor,
        "active": thread in live,
    }


def bind_legacy_failures(store, channel, rows):
    """Upgrade old blocked replies without attaching their label to a later turn."""
    database = store.home / "sessions/corki.db"
    if not database.is_file():
        return
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        for row in rows:
            found = connection.execute(
                "SELECT id,payload_json FROM chat_history WHERE thread_id=? "
                "AND kind='assistant_message' AND created_at<=? ORDER BY sequence DESC LIMIT 1",
                (row["thread"], datetime.fromtimestamp(row["updated"], UTC).isoformat()),
            ).fetchone()
            if found and json.loads(found[1]).get("content") == json.loads(row["reply"]).get(
                "text"
            ):
                store.http_queue.bind_answer(channel, row["id"], row["thread"], found[0])
    finally:
        connection.close()
