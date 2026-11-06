"""WeChat transport transcript and manual replies, separate from Agent context."""

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime


class WeChatMessages:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wechat_contacts(
                    channel TEXT NOT NULL,sender TEXT NOT NULL,account TEXT NOT NULL,
                    message TEXT NOT NULL,PRIMARY KEY(channel,sender));
                CREATE TABLE IF NOT EXISTS wechat_messages(
                    id INTEGER PRIMARY KEY,channel TEXT NOT NULL,thread TEXT NOT NULL,
                    sender TEXT NOT NULL,event_key TEXT NOT NULL,role TEXT NOT NULL,
                    kind TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,
                    status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
                    UNIQUE(channel,event_key));
                CREATE INDEX IF NOT EXISTS wechat_message_page
                    ON wechat_messages(channel,thread,id DESC);
            """)
            if "images" not in {
                r["name"] for r in db.execute("PRAGMA table_info(wechat_messages)")
            }:
                db.execute(
                    "ALTER TABLE wechat_messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
                )
            if "files" not in {r["name"] for r in db.execute("PRAGMA table_info(wechat_messages)")}:
                db.execute(
                    "ALTER TABLE wechat_messages ADD COLUMN files TEXT NOT NULL DEFAULT '[]'"
                )
            # Existing contacts can use ID-only sending until a new inbound
            # message refreshes their context. Retain saved legacy context too.
            rows = db.execute(
                "SELECT c.id,c.account,u.sender,u.name,d.message FROM connections c "
                "JOIN user_sessions u ON u.binding=c.conversation_id "
                "LEFT JOIN access_delivery d ON d.channel=c.id AND d.sender_id=u.sender "
                "WHERE c.type_id='wechat'"
            ).fetchall()
            for row in rows:
                message = json.loads(row["message"]) if row["message"] else {}
                message.update(sender=row["sender"], name=row["name"])
                message.setdefault("conversation", row["sender"])
                db.execute(
                    "INSERT OR IGNORE INTO wechat_contacts VALUES(?,?,?,?)",
                    (row["id"], row["sender"], row["account"], json.dumps(message)),
                )

    def thread(self, row, sender, *, create=False, name=""):
        with self.store.db() as db:
            if create:
                db.execute(
                    "INSERT OR IGNORE INTO user_sessions(binding,sender,name,thread) "
                    "VALUES(?,?,?,?)",
                    (row["conversation_id"], sender, (name or sender)[:80], str(uuid.uuid4())),
                )
            own = db.execute(
                "SELECT thread FROM user_sessions WHERE binding=? AND sender=?",
                (row["conversation_id"], sender),
            ).fetchone()
        return own[0] if own else None

    def inbound(self, row, message):
        with self.store.db() as db:
            if db.execute(
                "SELECT 1 FROM receipts WHERE channel=? AND id=?", (row["id"], message["id"])
            ).fetchone():
                return
            previous = db.execute(
                "SELECT account,message FROM wechat_contacts WHERE channel=? AND sender=?",
                (row["id"], message["sender"]),
            ).fetchone()
            context = (
                json.loads(previous["message"])
                if previous and previous["account"] == row["account"]
                else {}
            )
            context.update({k: v for k, v in message.items() if v not in (None, "")})
            db.execute(
                "INSERT INTO wechat_contacts VALUES(?,?,?,?) ON CONFLICT(channel,sender) "
                "DO UPDATE SET account=excluded.account,message=excluded.message",
                (row["id"], message["sender"], row["account"], json.dumps(context)),
            )
        positions = message.get("image_positions", [])
        self.record(
            row["id"],
            message,
            "inbound:" + message["id"],
            "user",
            "user",
            message["text"],
            images=[
                {"status": "pending", **({"position": positions[i]} if i < len(positions) else {})}
                for i, _ in enumerate(message.get("image_refs", []))
            ],
            files=[
                {
                    "status": "pending",
                    "name": ref.get("name", "attachment"),
                    "kind": ref.get("kind", "file"),
                    "position": message["file_positions"][i],
                }
                for i, ref in enumerate(message.get("file_refs", []))
            ],
        )

    def context(self, row, thread):
        _, sender, name = self.store.current_session_target(row["id"], thread)
        with self.store.db() as db:
            peer = db.execute(
                "SELECT account,message FROM wechat_contacts WHERE channel=? AND sender=?",
                (row["id"], sender),
            ).fetchone()
        # A newly logged-in bot must not reuse another bot's recipient/token.
        if not peer or peer["account"] != row["account"]:
            return None, name
        return json.loads(peer["message"]), name

    def record(
        self, channel, message, key, role, kind, text, status="sent", *, images=(), files=(), **_
    ):
        row = self.store.connection(channel)
        thread = self.thread(row, message["sender"], create=True, name=message.get("name", ""))
        with self.store.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO wechat_messages"
                "(channel,thread,sender,event_key,role,kind,content,created_at,"
                "status,images,files) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
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
                    "SELECT * FROM wechat_messages WHERE channel=? AND event_key=?", (channel, key)
                ).fetchone()
            )

    def finish(self, channel, key, state, error="", **_):
        with self.store.db() as db:
            db.execute(
                "UPDATE wechat_messages SET status=?,error=? WHERE channel=? AND event_key=?",
                (state, error[:2000], channel, key),
            )

    def recover(self):
        with self.store.db() as db:
            db.execute(
                "UPDATE wechat_messages SET status='failed',error=? WHERE status='sending'",
                ("发送中断，送达情况未知，未自动重发",),
            )

    def legacy_boundary(self, channel, thread):
        database = self.store.home / "sessions/corki.db"
        if not database.is_file():
            return None
        with self.store.db() as db:
            first = db.execute(
                "SELECT MIN(created_at) FROM wechat_messages WHERE channel=? AND thread=?",
                (channel, thread),
            ).fetchone()[0]
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            found = connection.execute(
                "SELECT MAX(sequence) FROM conversation_items WHERE thread_id=? "
                + ("AND created_at<?" if first else ""),
                (thread, first) if first else (thread,),
            ).fetchone()[0]
        finally:
            connection.close()
        return found + 1 if found is not None else None

    def view(self, row, params, live, core_history):
        sessions, sessions_before = self.store.sessions_page(row, params.get("sessions_before"))
        sender = params.get("sender")
        if sender is not None:
            if not isinstance(sender, str) or not 1 <= len(sender) <= 2000:
                raise ValueError("用户标识无效")
            thread = self.thread(row, sender)
            sessions, sessions_before = [], None
        else:
            thread = self.store.session_thread(row, params.get("session"), sessions)
        if params.get("thread") not in (None, thread):
            raise ValueError("会话已重置，请刷新")
        result = {
            "sessions": sessions,
            "sessions_before": sessions_before,
            "thread": thread,
            "messages": [],
            "before": None,
            "active": thread in live,
            "can_send": False,
        }
        if not thread:
            return result
        context, name = self.context(row, thread)
        result.update(recipient=name, can_send=bool(row["enabled"] and context))
        before = params.get("before")
        if before is not None and (
            not isinstance(before, str)
            or not re.fullmatch(r"(log|legacy):[0-9]{1,19}", before)
            or int(before.split(":")[1]) >= 2**63
        ):
            raise ValueError("历史游标无效")
        if before is None or before.startswith("log:"):
            last = int(before[4:]) if before else None
            with self.store.db() as db:
                rows = db.execute(
                    "SELECT * FROM wechat_messages WHERE channel=? AND thread=? "
                    + ("AND id<? " if last is not None else "")
                    + "ORDER BY id DESC LIMIT 101",
                    (row["id"], thread, last) if last is not None else (row["id"], thread),
                ).fetchall()
            if rows:
                result["messages"] = [public_message(r, self.store) for r in reversed(rows[:100])]
                boundary = self.legacy_boundary(row["id"], thread)
                result["before"] = (
                    "log:" + str(rows[99]["id"])
                    if len(rows) > 100
                    else "legacy:" + str(boundary)
                    if boundary is not None
                    else None
                )
                return result
            boundary = self.legacy_boundary(row["id"], thread)
        else:
            boundary = int(before[7:])
        if boundary is None:
            return result
        old = core_history(self.store, row["id"], {"session": thread, "before": boundary}, live)
        result.update(
            messages=old["messages"],
            before="legacy:" + str(old["before"]) if old["before"] is not None else None,
        )
        return result


def public_message(row, store):
    return {
        "id": "wxmsg:" + str(row["id"]),
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "created_at": row["created_at"],
        "status": row["status"],
        "error": row["error"],
        "delivery_failed": row["status"] == "failed",
        "images": store.images.public(row["channel"], row["images"]),
        "files": store.wechat_files.public(row["channel"], json.loads(row["files"])),
    }


async def manual_send(host, data):
    channel, session, key, text = (data.get(k) for k in ("id", "session", "request_id", "text"))
    row = host.store.connection(channel)
    if not row["enabled"] or channel not in host.connectors or host.stopping:
        raise ValueError("请先开启网关并启用此微信接入")
    ledger = host.store.wechat_messages
    identities = data.get("images", [])
    file_ids = data.get("files", [])
    if not isinstance(file_ids, list) or len(file_ids) > 8:
        raise ValueError("一次最多发送 8 个附件")
    if not isinstance(identities, list) or len(identities) > 8:
        raise ValueError("一次最多发送 8 张图片")
    if len(identities) + len(file_ids) > 8:
        raise ValueError("一次最多发送 8 个附件")
    message, _ = ledger.context(row, session)
    if not message:
        raise ValueError("请先使用当前绑定的微信发送一条消息")
    event_key = "manual:" + key
    with host.store.db() as db:
        existing = db.execute(
            "SELECT * FROM wechat_messages WHERE channel=? AND event_key=?", (channel, event_key)
        ).fetchone()
    if existing:
        if (
            existing["thread"] != session
            or existing["content"] != text
            or [image["id"] for image in json.loads(existing["images"])] != identities
            or [file["id"] for file in json.loads(existing["files"])] != file_ids
        ):
            raise ValueError("发送请求标识已被其他消息使用")
        return {"message": public_message(existing, host.store)}
    if len(host.manual_tasks) >= 20:
        raise ValueError("发送任务较多，请稍后重试")
    images = host.store.images.send_assets(channel, identities)
    files = host.store.wechat_files.send_assets(channel, file_ids)
    entry = ledger.record(
        channel,
        message,
        event_key,
        "assistant",
        "manual",
        text,
        "sending",
        images=images,
        files=files,
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
                    "images": images,
                    "files": files,
                },
                timeout=180 if images or files else 45,
                kind="manual",
            )
        except (Exception, asyncio.CancelledError):
            pass  # Persist failure; never retry an ambiguous side effect automatically.
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
        entry = db.execute("SELECT * FROM wechat_messages WHERE id=?", (entry["id"],)).fetchone()
    return {"message": public_message(entry, host.store)}
