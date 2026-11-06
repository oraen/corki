"""DingTalk transport history. It never participates in the Agent's model context."""

import asyncio
import json
import re
import sqlite3
import time
from datetime import UTC, datetime

from .errors import DeliveryError
from .recall import public_message, receipt


class Messages:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS dingtalk_contacts(
                    id INTEGER PRIMARY KEY,channel TEXT NOT NULL,sender TEXT NOT NULL,
                    name TEXT NOT NULL,message TEXT NOT NULL,UNIQUE(channel,sender));
                CREATE TABLE IF NOT EXISTS dingtalk_messages(
                    id INTEGER PRIMARY KEY,channel TEXT NOT NULL,sender TEXT NOT NULL,
                    event_key TEXT NOT NULL,role TEXT NOT NULL,kind TEXT NOT NULL,
                    content TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',UNIQUE(channel,event_key));
                CREATE INDEX IF NOT EXISTS dingtalk_message_activity
                    ON dingtalk_messages(channel,sender,created_at DESC);
                CREATE INDEX IF NOT EXISTS dingtalk_message_page
                    ON dingtalk_messages(channel,sender,id DESC);
                CREATE TABLE IF NOT EXISTS dingtalk_history_meta(key TEXT PRIMARY KEY,value REAL);
            """)
            db.execute(
                "INSERT OR IGNORE INTO dingtalk_history_meta VALUES('cutover',?)", (time.time(),)
            )
            seeded = db.execute("SELECT 1 FROM dingtalk_history_meta WHERE key='seeded'").fetchone()
            columns = {r["name"] for r in db.execute("PRAGMA table_info(dingtalk_messages)")}
            if "images" not in columns:
                db.execute(
                    "ALTER TABLE dingtalk_messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
                )
            for name, default in (('recall_receipt', '{}'), ('recall_state', ''), ('recall_error', ''), ('recalled_at', '')):
                if name not in columns:
                    db.execute(f"ALTER TABLE dingtalk_messages ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        if not seeded:
            self.seed()
            with store.db() as db:
                db.execute("INSERT OR IGNORE INTO dingtalk_history_meta VALUES('seeded',1)")

    def seed(self):
        # Preserve the latest notices we actually retained; never invent older attempts.
        with self.store.db() as db:
            deliveries = db.execute(
                "SELECT d.*,a.sender_name FROM access_delivery d "
                "JOIN connections c ON c.id=d.channel "
                "JOIN access_requests a ON a.channel=d.channel AND a.sender_id=d.sender_id "
                "WHERE c.type_id='dingtalk'"
            ).fetchall()
            routes = db.execute(
                "SELECT r.* FROM routes r JOIN connections c ON c.id=r.channel "
                "WHERE c.type_id='dingtalk' AND r.sender_id IS NOT NULL ORDER BY r.last_seen"
            ).fetchall()
        for r in deliveries:
            message = json.loads(r["message"])
            self.contact(r["channel"], message)
            with self.store.db() as db:
                denied = db.execute(
                    "SELECT updated FROM receipts WHERE channel=? AND id=? "
                    "AND status='access_denied'",
                    (r["channel"], message.get("id")),
                ).fetchone()
            if denied:
                self.record(
                    r["channel"],
                    message,
                    "inbound:" + message["id"],
                    "user",
                    "user",
                    message["text"],
                    created_at=denied["updated"],
                )
            if r["text"] and r["notice_id"]:
                self.record(
                    r["channel"],
                    message,
                    "notice:" + r["notice_id"],
                    "assistant",
                    "notice",
                    r["text"],
                    r["status"] or "failed",
                    error=r["error"] or "",
                    created_at=r["updated"],
                )
        for r in routes:
            self.contact(r["channel"], {"sender": r["sender_id"], "name": r["sender_name"]})

    def contact(self, channel, message):
        sender = message["sender"]
        with self.store.db() as db:
            previous = db.execute(
                "SELECT message FROM dingtalk_contacts WHERE channel=? AND sender=?",
                (channel, sender),
            ).fetchone()
            context = json.loads(previous[0]) if previous else {}
            # A route-only seed or an incomplete callback must not erase known recipient IDs.
            context.update({k: v for k, v in message.items() if v not in (None, "")})
            name = str(context.get("name") or sender)[:80]
            db.execute(
                "INSERT INTO dingtalk_contacts(channel,sender,name,message) VALUES(?,?,?,?) "
                "ON CONFLICT(channel,sender) DO UPDATE SET name=excluded.name,"
                "message=excluded.message",
                (channel, sender, name, json.dumps(context, ensure_ascii=False)),
            )

    def record(
        self,
        channel,
        message,
        key,
        role,
        kind,
        text,
        status="sent",
        *,
        error="",
        created_at=None,
        images=(),
    ):
        stamp = datetime.fromtimestamp(created_at or time.time(), UTC).isoformat()
        with self.store.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO dingtalk_messages"
                "(channel,sender,event_key,role,kind,content,created_at,status,error,images) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    channel,
                    message["sender"],
                    key,
                    role,
                    kind,
                    text[:120000],
                    stamp,
                    status,
                    error,
                    json.dumps(images),
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM dingtalk_messages WHERE channel=? AND event_key=?",
                    (channel, key),
                ).fetchone()
            )

    def finish(self, channel, key, state, error="", *, delivery_receipt=None):
        saved = receipt(delivery_receipt)
        with self.store.db() as db:
            db.execute(
                "UPDATE dingtalk_messages SET status=?,error=?,"
                "recall_receipt=COALESCE(?,recall_receipt) WHERE channel=? AND event_key=?",
                (state, error[:2000], json.dumps(saved) if saved else None, channel, key),
            )

    def recover(self):
        with self.store.db() as db:
            db.execute(
                "UPDATE dingtalk_messages SET status='failed',error=? WHERE status='sending'",
                ("发送中断，送达情况未知，未自动重发",),
            )

            db.execute("UPDATE dingtalk_messages SET recall_state='failed',recall_error=? WHERE recall_state='recalling'",
                       ('撤回中断，结果未确认，请稍后重试',))

    def peer(self, channel, session):
        if not isinstance(session, str) or not re.fullmatch(r"dt:[1-9][0-9]{0,18}", session):
            raise ValueError("请选择联系人")
        if int(session[3:]) >= 2**63:
            raise ValueError("联系人标识无效")
        with self.store.db() as db:
            row = db.execute(
                "SELECT * FROM dingtalk_contacts WHERE channel=? AND id=?",
                (channel, int(session[3:])),
            ).fetchone()
        if not row:
            raise ValueError("联系人不属于此接入")
        return dict(row)

    def legacy_thread(self, row, sender):
        with self.store.db() as db:
            own = db.execute(
                "SELECT thread FROM user_sessions WHERE binding=? AND sender=?",
                (row["conversation_id"], sender),
            ).fetchone()
            if own:
                return own[0]
            route = db.execute(
                "SELECT thread FROM routes WHERE channel=? AND sender_id=? AND binding_id=? "
                "ORDER BY last_seen DESC LIMIT 1",
                (row["id"], sender, row["conversation_id"]),
            ).fetchone()
        return route[0] if route else None

    def view(self, row, params, live, core_history):
        before_contacts = params.get("sessions_before")
        if before_contacts is not None and (
            type(before_contacts) is not int or not 1 <= before_contacts < 2**63
        ):
            raise ValueError("联系人游标无效")
        with self.store.db() as db:
            contacts = db.execute(
                "SELECT id,name,sender FROM dingtalk_contacts WHERE channel=? "
                + ("AND id<? " if before_contacts else "")
                + "ORDER BY id DESC LIMIT 101",
                (row["id"], before_contacts) if before_contacts else (row["id"],),
            ).fetchall()
        sessions = [
            {"thread": "dt:" + str(c["id"]), "name": c["name"], "sender": c["sender"]}
            for c in contacts[:100]
        ]
        sender = params.get("sender")
        if sender is not None:
            if not isinstance(sender, str) or not 1 <= len(sender) <= 2000:
                raise ValueError("用户标识无效")
            with self.store.db() as db:
                contact = db.execute(
                    "SELECT id FROM dingtalk_contacts WHERE channel=? AND sender=?",
                    (row["id"], sender),
                ).fetchone()
            session = "dt:" + str(contact["id"]) if contact else None
            sessions, contacts = [], []
        else:
            session = params.get("session") or (sessions[0]["thread"] if sessions else None)
        result = {
            "sessions": sessions,
            "sessions_before": contacts[99]["id"] if len(contacts) > 100 else None,
            "thread": session,
            "messages": [],
            "before": None,
            "active": False,
            "can_send": False,
        }
        if not session:
            return result
        peer = self.peer(row["id"], session)
        context = json.loads(peer["message"])
        result.update(
            recipient=peer["name"], can_send=row["enabled"] and bool(context.get("staff_id"))
        )
        thread = self.legacy_thread(row, peer["sender"])
        result["active"] = thread in live
        before = params.get("before")
        if before is not None and (
            not isinstance(before, str) or not re.fullmatch(r"(log|legacy):[0-9]{1,19}", before)
        ):
            raise ValueError("历史游标无效")
        if before is not None and int(before.split(":")[1]) >= 2**63:
            raise ValueError("历史游标无效")
        if params.get("thread") not in (None, session):
            raise ValueError("联系人已切换，请刷新")
        if before is None or before.startswith("log:"):
            last = int(before[4:]) if before else None
            with self.store.db() as db:
                rows = db.execute(
                    "SELECT * FROM dingtalk_messages WHERE channel=? AND sender=? "
                    + ("AND id<? " if last is not None else "")
                    + "ORDER BY id DESC LIMIT 101",
                    (row["id"], peer["sender"], last)
                    if last is not None
                    else (row["id"], peer["sender"]),
                ).fetchall()
            if rows:
                result["messages"] = [
                    {
                        **public_message(m),
                        "id": "dtmsg:" + str(m["id"]),
                        "images": self.store.images.public(row["id"], m["images"]),
                        "delivery_failed": m["status"] == "failed",
                    }
                    for m in reversed(rows[:100])
                ]
                result["before"] = (
                    "log:" + str(rows[99]["id"])
                    if len(rows) > 100
                    else ("legacy:0" if thread else None)
                )
                return result
        # Old Agent messages remain in their canonical database. Only read items
        # before the transport ledger cutover, so new replies cannot appear twice.
        if not thread:
            return result
        boundary = int(before.split(":")[1]) if before and before.startswith("legacy:") else 0
        if not boundary:
            database = self.store.home / "sessions/corki.db"
            if not database.is_file():
                return result
            with self.store.db() as db:
                cutoff = db.execute(
                    "SELECT value FROM dingtalk_history_meta WHERE key='cutover'"
                ).fetchone()[0]
            stamp = datetime.fromtimestamp(cutoff, UTC).isoformat()
            db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                sequence = db.execute(
                    "SELECT MAX(sequence) FROM conversation_items "
                    "WHERE thread_id=? AND created_at<?",
                    (thread, stamp),
                ).fetchone()[0]
            finally:
                db.close()
            if sequence is None:
                return result
            boundary = sequence + 1
        old = core_history(self.store, row["id"], {"session": thread, "before": boundary}, live)
        result.update(
            messages=old["messages"],
            before="legacy:" + str(old["before"]) if old["before"] is not None else None,
        )
        return result


async def send_logged(host, channel, params, *, timeout=45, kind="agent"):
    connector = host.connectors.get(channel)
    if connector is None:
        raise ValueError("渠道已断开")
    row = host.store.connection(channel)
    if row["type_id"] not in ("dingtalk", "wechat"):
        return await connector.call("send", params, timeout=timeout)
    ledger = host.store.wechat_messages if row["type_id"] == "wechat" else host.store.messages
    message = params["message"]
    key = (
        ("notice:" + params["notice_id"])
        if "notice_id" in params
        else params.get("record_key") or "answer:" + message["id"]
    )
    entry = ledger.record(
        channel, message, key, "assistant", kind, params["text"], "sending", images=params.get("images", ()),
        **({"files": params.get("files", ())} if row["type_id"] == "wechat" else {})
    )
    if entry["status"] not in ("sending", "queued"):
        if entry["status"] == "sent":
            return {"sent": True}
        raise DeliveryError(entry["error"] or "此前发送失败，未自动重发")
    if entry["status"] == "queued":
        ledger.finish(channel, key, "sending")
    try:
        result = await connector.call("send", params, timeout=timeout)
        if not isinstance(result, dict) or result.get("sent") is not True:
            raise DeliveryError("通知未获发送成功回执")
        ledger.finish(channel, key, "sent", delivery_receipt=result.get("receipt"))
        return result
    except BaseException as error:
        reason = str(error) if isinstance(error, DeliveryError) else "发送失败或中断，送达情况未知"
        ledger.finish(channel, key, "failed", reason)
        raise


async def manual_send(host, data):
    channel, session, key, text = (data.get(k) for k in ("id", "session", "request_id", "text"))
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", key):
        raise ValueError("发送请求标识无效")
    row = host.store.connection(channel)
    image_only = row and row["type_id"] == "wechat" and bool(data.get("images") or data.get("files"))
    if not isinstance(text, str) or len(text) > 2000 or (not text.strip() and not image_only):
        raise ValueError("请输入消息或添加附件" if row and row["type_id"] == "wechat" else "请输入 1–2000 字的消息")
    if row and row["type_id"] == "wechat":
        from .wechat_messages import manual_send as wechat_manual_send

        return await wechat_manual_send(host, data)
    if (
        not row
        or row["type_id"] != "dingtalk"
        or not row["enabled"]
        or channel not in host.connectors
        or host.stopping
    ):
        raise ValueError("请先开启网关并启用此钉钉接入")
    peer = host.store.messages.peer(channel, session)
    message = json.loads(peer["message"])
    event_key = "manual:" + key
    with host.store.db() as db:
        existing = db.execute(
            "SELECT * FROM dingtalk_messages WHERE channel=? AND event_key=?", (channel, event_key)
        ).fetchone()
    if existing:
        if existing["sender"] != peer["sender"] or existing["content"] != text:
            raise ValueError("发送请求标识已被其他消息使用")
        return {"message": public_message(existing)}
    if len(host.manual_tasks) >= 20:
        raise ValueError("发送任务较多，请稍后重试")
    # Reserve before yielding; concurrent windows/retries cannot execute the send twice.
    entry = host.store.messages.record(
        channel, message, event_key, "assistant", "manual", text, "sending"
    )

    async def deliver():
        try:
            await send_logged(
                host,
                channel,
                {"message": message, "text": text, "proactive": True, "record_key": event_key},
                kind="manual",
            )
        except (Exception, asyncio.CancelledError):
            pass  # The durable message retains its outcome; never automatically replay it.
        finally:
            host.manual_tasks.pop((channel, key), None)

    task = asyncio.create_task(deliver())
    host.manual_tasks[channel, key] = task
    await asyncio.shield(task)
    with host.store.db() as db:
        entry = db.execute("SELECT * FROM dingtalk_messages WHERE id=?", (entry["id"],)).fetchone()
    return {"message": public_message(entry)}
