"""Durable Telegram/WhatsApp transcripts, private recipients and manual delivery."""

import asyncio
import json
import re
import sqlite3
from datetime import UTC, datetime

TYPES = ("telegram", "whatsapp")


def next_request_id(db):
    """Removed direct-contact URLs must never resolve to another user."""
    issued = db.execute(
        "SELECT value FROM gateway_meta WHERE key='direct-contact-sequence'"
    ).fetchone()
    current = db.execute("SELECT coalesce(max(rowid),0) FROM access_requests").fetchone()[0]
    identity = max(current, int(issued[0]) if issued else 0) + 1
    db.execute(
        "INSERT OR REPLACE INTO gateway_meta VALUES('direct-contact-sequence',?)", (str(identity),)
    )
    return identity


class DirectMessages:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS direct_contacts(
                    channel TEXT NOT NULL,sender TEXT NOT NULL,account TEXT NOT NULL,
                    message TEXT NOT NULL,PRIMARY KEY(channel,sender));
                CREATE TABLE IF NOT EXISTS direct_messages(
                    id INTEGER PRIMARY KEY,channel TEXT NOT NULL,thread TEXT NOT NULL,
                    sender TEXT NOT NULL,event_key TEXT NOT NULL,role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',images TEXT NOT NULL DEFAULT '[]',
                    files TEXT NOT NULL DEFAULT '[]',UNIQUE(channel,event_key));
                CREATE INDEX IF NOT EXISTS direct_history
                    ON direct_messages(channel,thread,id DESC);
                CREATE TABLE IF NOT EXISTS direct_open_contacts(
                    channel TEXT NOT NULL,sender TEXT NOT NULL,PRIMARY KEY(channel,sender));
            """)

    def inbound(self, row, message):
        with self.store.db() as db:
            if db.execute(
                "SELECT 1 FROM receipts WHERE channel=? AND id=?", (row["id"], message["id"])
            ).fetchone():
                return
            db.execute(
                "INSERT INTO direct_contacts VALUES(?,?,?,?) ON CONFLICT(channel,sender) "
                "DO UPDATE SET account=excluded.account,message=excluded.message",
                (row["id"], message["sender"], row["account"], json.dumps(message)),
            )
            # Keep all contacts visible in session management when approvals are off.
            if (
                row["config"].get("session_validation") != "true"
                and not db.execute(
                    "SELECT 1 FROM access_requests WHERE channel=? AND sender_id=?",
                    (row["id"], message["sender"]),
                ).fetchone()
            ):
                inserted = db.execute(
                    "INSERT OR IGNORE INTO access_requests(rowid,channel,sender_id,sender_name,"
                    "first_message,first_message_id,external_conversation_id,created_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?,'approved')",
                    (
                        next_request_id(db),
                        row["id"],
                        message["sender"],
                        message.get("name", message["sender"])[:80],
                        message["text"],
                        message["id"],
                        message["conversation"],
                        datetime.now(UTC).timestamp(),
                    ),
                ).rowcount
                if inserted:
                    db.execute(
                        "INSERT OR IGNORE INTO direct_open_contacts VALUES(?,?)",
                        (row["id"], message["sender"]),
                    )
        self.record(
            row["id"],
            message,
            "inbound:" + message["id"],
            "user",
            "user",
            message["text"],
            images=[
                {"status": "pending", "position": p} for p in message.get("image_positions", [])
            ],
            files=[
                {
                    "status": "pending",
                    "name": r.get("name", "attachment"),
                    "kind": r.get("kind", "file"),
                    "position": p,
                }
                for r, p in zip(
                    message.get("file_refs", []), message.get("file_positions", []), strict=True
                )
            ],
        )

    def record(
        self, channel, message, key, role, kind, text, status="sent", *, images=(), files=(), **_
    ):
        row = self.store.connection(channel)
        thread = self.store.wechat_messages.thread(
            row, message["sender"], create=True, name=message.get("name", "")
        )
        with self.store.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO direct_messages(channel,thread,sender,event_key,role,kind,"
                "content,created_at,status,images,files) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    channel,
                    thread,
                    message["sender"],
                    key,
                    role,
                    kind,
                    text[:120000],
                    datetime.now(UTC).isoformat(),
                    status,
                    json.dumps(images),
                    json.dumps(files),
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM direct_messages WHERE channel=? AND event_key=?", (channel, key)
                ).fetchone()
            )

    def finish(self, channel, key, state, error="", **_):
        with self.store.db() as db:
            db.execute(
                "UPDATE direct_messages SET status=?,error=? WHERE channel=? AND event_key=?",
                (state, error[:2000], channel, key),
            )

    def recover(self):
        with self.store.db() as db:
            db.execute(
                "UPDATE direct_messages SET status='failed',error=? WHERE status='sending'",
                ("发送中断，送达情况未知，未自动重发",),
            )

    def context(self, row, thread):
        _, sender, name = self.store.current_session_target(row["id"], thread)
        with self.store.db() as db:
            peer = db.execute(
                "SELECT account,message FROM direct_contacts WHERE channel=? AND sender=?",
                (row["id"], sender),
            ).fetchone()
        if not peer or not row["account"] or peer["account"] != row["account"]:
            return None, name
        return json.loads(peer["message"]), name

    def public(self, row):
        return {
            "id": "direct:" + str(row["id"]),
            "role": row["role"],
            "kind": row["kind"],
            "content": row["content"],
            "created_at": row["created_at"],
            "status": row["status"],
            "error": row["error"],
            "delivery_failed": row["status"] == "failed",
            "images": self.store.images.public(row["channel"], row["images"]),
            "files": self.store.wechat_files.public(row["channel"], json.loads(row["files"])),
        }

    def view(self, row, params, live, core_history):
        base = core_history(
            self.store,
            row["id"],
            {k: v for k, v in params.items() if k not in ("before", "thread")},
            live,
        )
        thread = base["thread"]
        if params.get("thread") not in (None, thread):
            raise ValueError("会话已重置，请刷新")
        base.update(can_send=False, messages=[], before=None)
        if not thread:
            return base
        context, name = self.context(row, thread)
        base.update(can_send=bool(row["enabled"] and context), recipient=name)
        before = params.get("before")
        if before is not None and (
            not isinstance(before, str)
            or not re.fullmatch(r"(log|legacy):[0-9]{1,19}", before)
            or int(before.split(":")[1]) >= 2**63
        ):
            raise ValueError("历史游标无效")
        with self.store.db() as db:
            first = db.execute(
                "SELECT min(created_at) FROM direct_messages WHERE channel=? AND thread=?",
                (row["id"], thread),
            ).fetchone()[0]
            rows = (
                []
                if before and before.startswith("legacy:")
                else db.execute(
                    "SELECT * FROM direct_messages WHERE channel=? AND thread=? "
                    + ("AND id<? " if before else "")
                    + "ORDER BY id DESC LIMIT 101",
                    (row["id"], thread, int(before[4:])) if before else (row["id"], thread),
                ).fetchall()
            )
        boundary = None
        database = self.store.home / "sessions/corki.db"
        if database.is_file():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                found = db.execute(
                    "SELECT max(sequence) FROM conversation_items WHERE thread_id=? "
                    + ("AND created_at<?" if first else ""),
                    (thread, first) if first else (thread,),
                ).fetchone()[0]
                boundary = found + 1 if found is not None else None
        if rows:
            base.update(
                messages=[self.public(r) for r in reversed(rows[:100])],
                before="log:" + str(rows[99]["id"])
                if len(rows) > 100
                else "legacy:" + str(boundary)
                if boundary is not None
                else None,
            )
        elif boundary is not None:
            legacy = core_history(
                self.store,
                row["id"],
                {
                    **params,
                    "before": int(before[7:])
                    if before and before.startswith("legacy:")
                    else boundary,
                },
                live,
            )
            base.update(
                messages=legacy["messages"],
                before="legacy:" + str(legacy["before"]) if legacy["before"] is not None else None,
            )
        return base


async def manual_send(host, data):
    channel, thread, key, text = (data.get(k) for k in ("id", "session", "request_id", "text"))
    row = host.store.connection(channel)
    if not row["enabled"] or channel not in host.connectors or host.stopping:
        raise ValueError("请先启用此接入")
    images, files = data.get("images", []), data.get("files", [])
    if not isinstance(images, list) or not isinstance(files, list) or len(images) + len(files) > 8:
        raise ValueError("一次最多发送 8 个附件")
    ledger = host.store.direct_messages
    message, _ = ledger.context(row, thread)
    if not message:
        raise ValueError("当前联系人尚未向此平台账号发送消息")
    event_key = "manual:" + key
    with host.store.db() as db:
        existing = db.execute(
            "SELECT * FROM direct_messages WHERE channel=? AND event_key=?", (channel, event_key)
        ).fetchone()
    if existing:
        if (
            existing["thread"] != thread
            or existing["content"] != text
            or [i["id"] for i in json.loads(existing["images"])] != images
            or [f["id"] for f in json.loads(existing["files"])] != files
        ):
            raise ValueError("发送请求标识已被其他消息使用")
        return {"message": ledger.public(existing)}
    if len(host.manual_tasks) >= 20:
        raise ValueError("发送任务较多，请稍后重试")
    image_assets = host.store.images.send_assets(channel, images)
    file_assets = host.store.wechat_files.send_assets(channel, files)
    entry = ledger.record(
        channel,
        message,
        event_key,
        "assistant",
        "manual",
        text,
        "sending",
        images=image_assets,
        files=file_assets,
    )

    async def deliver():
        from .messages import send_logged

        try:
            await send_logged(
                host,
                channel,
                {
                    "message": message,
                    "text": text,
                    "record_key": event_key,
                    "images": image_assets,
                    "files": file_assets,
                },
                timeout=180,
                kind="manual",
            )
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            host.manual_tasks.pop((channel, key), None)

    task = asyncio.create_task(deliver())
    host.manual_tasks[channel, key] = task

    def settled(done):
        if done.cancelled():
            ledger.finish(channel, event_key, "failed", "发送中断，送达情况未知，未自动重发")
        host.manual_tasks.pop((channel, key), None)

    task.add_done_callback(settled)
    await asyncio.shield(task)
    with host.store.db() as db:
        entry = db.execute("SELECT * FROM direct_messages WHERE id=?", (entry["id"],)).fetchone()
    return {"message": ledger.public(entry)}
