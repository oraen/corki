"""Per-connection admission decisions and bounded notification state."""

import json
import time
import uuid

REPLY_DEFAULTS = {
    "validation_first_reply": "您暂无机器人使用权限，请联系管理员",
    "validation_approved_reply": "管理员已为您开通权限，现在可以开始对话了",
    "validation_disabled_reply": "管理员已经关闭了您的权限",
}
REPLY_KEYS = {
    "pending": "validation_first_reply",
    "approved": "validation_approved_reply",
    "disabled": "validation_disabled_reply",
}


class AccessRequests:
    def allows_message(self, row, message, *, remember=True):
        if row["config"].get("session_validation", "false") != "true":
            return True
        channel, sender = row["id"], message["sender"]
        with self.db() as db:
            found = db.execute(
                "SELECT status FROM access_requests WHERE channel=? AND sender_id=?",
                (channel, sender),
            ).fetchone()
            if found:
                if (
                    remember
                    and not db.execute(
                        "SELECT 1 FROM receipts WHERE channel=? AND id=?", (channel, message["id"])
                    ).fetchone()
                ):
                    # Keep the newest reply credentials; never change the first question.
                    # Execution-time checks must not overwrite this with an older queued input.
                    db.execute(
                        "UPDATE access_delivery SET message=? WHERE channel=? AND sender_id=?",
                        (json.dumps(message, ensure_ascii=False), channel, sender),
                    )
                    if found[0] != "approved":
                        self.queue_access_notice(db, row, sender, "pending")
                if found[0] != "approved":
                    self.record_denied(db, channel, message)
                return found[0] == "approved"
            db.execute("BEGIN IMMEDIATE")
            name = message.get("name")
            inserted = db.execute(
                "INSERT OR IGNORE INTO access_requests "
                "(channel,sender_id,sender_name,first_message,first_message_id,"
                "external_conversation_id,created_at,status) VALUES(?,?,?,?,?,?,?,'pending')",
                (
                    channel,
                    sender,
                    name[:80] if isinstance(name, str) else sender[:80],
                    message["text"],
                    message["id"],
                    message["conversation"],
                    time.time(),
                ),
            ).rowcount
            if inserted:
                db.execute(
                    "INSERT INTO access_delivery(channel,sender_id,message) VALUES(?,?,?)",
                    (channel, sender, json.dumps(message, ensure_ascii=False)),
                )
                self.queue_access_notice(db, row, sender, "pending")
            approved = (
                db.execute(
                    "SELECT status FROM access_requests WHERE channel=? AND sender_id=?",
                    (channel, sender),
                ).fetchone()[0]
                == "approved"
            )
            if not approved:
                # Commit registration, notice and rejection receipt together. A crash
                # before the transport ACK must not turn this old request into Agent work.
                self.record_denied(db, channel, message)
            return approved

    @staticmethod
    def record_denied(db, channel, message):
        db.execute(
            "INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,'access_denied',?,'')",
            (channel, message["id"], "", "", time.time()),
        )

    def queue_access_notice(self, db, row, sender, kind):
        key = REPLY_KEYS[kind]
        text = row["config"].get(key) or REPLY_DEFAULTS[key]
        notice_id, updated = uuid.uuid4().hex, time.time()
        # Admin decisions supersede unsent notices; distinct denied questions each
        # retain their own reply context until delivery, including during a slow send.
        if kind != "pending":
            db.execute(
                "DELETE FROM access_notice_queue WHERE channel=? AND sender_id=? "
                "AND status='queued'",
                (row["id"], sender),
            )
        db.execute(
            "UPDATE access_delivery SET notice_id=?,kind=?,text=?,status='queued',"
            "error='',updated=? WHERE channel=? AND sender_id=?",
            (notice_id, kind, text, updated, row["id"], sender),
        )
        db.execute(
            "INSERT INTO access_notice_queue SELECT notice_id,channel,sender_id,message,"
            "kind,text,'queued',updated FROM access_delivery WHERE notice_id=?",
            (notice_id,),
        )

    def deny_message(self, channel, message):
        # Platform retries after approval must not execute previously rejected input.
        with self.db() as db:
            self.record_denied(db, channel, message)
            db.execute(
                "DELETE FROM receipts WHERE updated<? AND status='access_denied'",
                (time.time() - 30 * 86400,),
            )

    def applications(self, channel, before=None, *, name="", status=""):
        if not self.connection(channel):
            raise ValueError("接入不存在")
        if before is not None and (type(before) is not int or not 0 < before < 2**63):
            raise ValueError("申请列表游标无效")
        if not isinstance(name, str) or len(name) > 80:
            raise ValueError("用户名称搜索最长 80 字符")
        if status not in ("", "pending", "approved", "disabled"):
            raise ValueError("申请筛选状态无效")
        conditions, args = ["a.channel=?"], [channel]
        if before is not None:
            conditions.append("a.rowid<?")
            args.append(before)
        if name.strip():
            conditions.append("instr(lower(a.sender_name),lower(?))>0")
            args.append(name.strip())
        if status:
            conditions.append("a.status=?")
            args.append(status)
        with self.db() as db:
            rows = db.execute(
                "SELECT a.rowid AS cursor,a.sender_id,a.sender_name,"
                "substr(a.first_message,1,240) AS first_message,a.created_at,a.status,"
                "d.status AS notification_status,d.error AS notification_error, "
                "d.kind AS notification_kind,d.updated AS notification_time "
                "FROM access_requests a LEFT JOIN access_delivery d "
                "ON d.channel=a.channel AND d.sender_id=a.sender_id WHERE "
                + " AND ".join(conditions)
                + " ORDER BY a.rowid DESC LIMIT 101",
                args,
            ).fetchall()
        return {
            "requests": [dict(r) for r in rows[:100]],
            "before": rows[99]["cursor"] if len(rows) > 100 else None,
        }

    def set_application_status(self, channel, sender, status):
        if not isinstance(sender, str) or not 1 <= len(sender) <= 300:
            raise ValueError("用户 ID 无效")
        if status not in ("approved", "disabled"):
            raise ValueError("申请状态无效")
        row = self.connection(channel)
        if not row or row["config"].get("session_validation", "false") != "true":
            raise ValueError("此接入未开启会话校验")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT status FROM access_requests WHERE channel=? AND sender_id=?",
                (channel, sender),
            ).fetchone()
            if not previous:
                raise ValueError("会话申请不存在")
            if previous[0] == status:
                return  # Duplicate admin request must not send the same notice again.
            db.execute(
                "UPDATE access_requests SET status=? WHERE channel=? AND sender_id=?",
                (status, channel, sender),
            )
            self.queue_access_notice(db, row, sender, status)

    def application_message(self, channel, sender):
        if not self.connection(channel) or not isinstance(sender, str):
            raise ValueError("申请不存在")
        with self.db() as db:
            row = db.execute(
                "SELECT first_message FROM access_requests WHERE channel=? AND sender_id=?",
                (channel, sender),
            ).fetchone()
        if not row:
            raise ValueError("申请不存在")
        return {"text": row[0]}

    def claim_access_notice(self, channel):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            notice = db.execute(
                "SELECT * FROM access_notice_queue WHERE channel=? AND status='queued' "
                "ORDER BY updated LIMIT 1",
                (channel,),
            ).fetchone()
            if not notice:
                return None
            db.execute(
                "UPDATE access_notice_queue SET status='sending' WHERE notice_id=?",
                (notice["notice_id"],),
            )
            db.execute(
                "UPDATE access_delivery SET status='sending' WHERE notice_id=?",
                (notice["notice_id"],),
            )
            return dict(notice)

    def finish_access_notice(self, notice_id, status, error=""):
        with self.db() as db:
            db.execute("DELETE FROM access_notice_queue WHERE notice_id=?", (notice_id,))
            db.execute(
                "UPDATE access_delivery SET status=?,error=? WHERE notice_id=?",
                (status, error, notice_id),
            )

    def recover_access_notices(self):
        with self.db() as db:
            db.execute(
                "UPDATE access_delivery SET status='failed',error=? WHERE status='sending'",
                ("通知发送中断，送达情况未知，未自动重发",),
            )
            db.execute("DELETE FROM access_notice_queue WHERE status='sending'")
