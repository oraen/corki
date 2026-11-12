"""Per-connection admission decisions and bounded notification state."""

import json
import math
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
            if row["type_id"] in ("telegram", "whatsapp"):
                opened = db.execute(
                    "SELECT 1 FROM direct_open_contacts WHERE channel=? AND sender=?",
                    (channel, sender),
                ).fetchone()
                receipt = db.execute(
                    "SELECT status FROM receipts WHERE channel=? AND id=?", (channel, message["id"])
                ).fetchone()
                if opened and (not receipt or receipt[0] in ("queued", "running")):
                    db.execute(
                        "UPDATE access_requests SET first_message=?,first_message_id=?,"
                        "created_at=? WHERE channel=? AND sender_id=?",
                        (message["text"], message["id"], time.time(), channel, sender),
                    )
                    db.execute(
                        "DELETE FROM direct_open_contacts WHERE channel=? AND sender=?",
                        (channel, sender),
                    )
                    db.execute(
                        "INSERT INTO access_delivery(channel,sender_id,message) VALUES(?,?,?) "
                        "ON CONFLICT(channel,sender_id) DO UPDATE SET message=excluded.message",
                        (channel, sender, json.dumps(message)),
                    )
                    decision = db.execute(
                        "SELECT status FROM access_requests WHERE channel=? AND sender_id=?",
                        (channel, sender),
                    ).fetchone()
                    if not remember and decision and decision[0] != "approved":
                        self.queue_access_notice(db, row, sender, "pending")
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
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            name = message.get("name")
            direct = row["type_id"] in ("telegram", "whatsapp")
            if direct:
                from .direct_messages import next_request_id

                request_id = next_request_id(db)
            inserted = db.execute(
                "INSERT OR IGNORE INTO access_requests "
                + ("(rowid," if direct else "(")
                + "channel,sender_id,sender_name,first_message,first_message_id,"
                "external_conversation_id,created_at,status) VALUES("
                + ("?," if direct else "")
                + "?,?,?,?,?,?,?,'pending')",
                ((request_id,) if direct else ())
                + (
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

    def applications(self, channel, before=None, *, name="", status="", sort="id"):
        connection = self.connection(channel)
        if not connection:
            raise ValueError("接入不存在")
        if sort not in ("id", "last_chat", "tokens"):
            raise ValueError("会话排序方式无效")
        if before is not None:
            if sort == "id":
                valid = type(before) is int and 0 < before < 2**63
            else:
                valid = (
                    isinstance(before, dict)
                    and before.get("sort") == sort
                    and type(before.get("id")) is int
                    and 0 < before["id"] < 2**63
                    and type(before.get("value")) in (int, float)
                    and math.isfinite(before["value"])
                    and before["value"] >= 0
                )
            if not valid:
                raise ValueError("申请列表游标无效")
        if not isinstance(name, str) or len(name) > 80:
            raise ValueError("用户名称搜索最长 80 字符")
        if status not in ("", "pending", "approved", "disabled"):
            raise ValueError("申请筛选状态无效")
        conditions, args = ["a.channel=?"], [channel]
        if name.strip():
            user_id_search = (
                "CASE WHEN json_valid(d.message) THEN json_extract(d.message,'$.staff_id') END"
                if connection["type_id"] == "dingtalk"
                else "a.sender_id"
            )
            conditions.append(
                "(instr(lower(a.sender_name),lower(?))>0 OR "
                f"instr(lower({user_id_search}),lower(?))>0)"
            )
            args.extend([name.strip(), name.strip()])
        if status:
            conditions.append("a.status=?")
            args.append(status)
        user_id = (
            "NULLIF(trim(CASE WHEN json_valid(d.message) "
            "THEN json_extract(d.message,'$.staff_id') END),'')"
            if connection["type_id"] == "dingtalk"
            else "a.sender_id"
        )
        # Latest message includes user input, Agent replies and manually sent messages.
        # Historical accounts without a message ledger fall back to their last route/input.
        last_chat = (
            "max(a.created_at,coalesce((SELECT unixepoch(max(m.created_at)) "
            "FROM dingtalk_messages m WHERE m.channel=a.channel AND m.sender=a.sender_id),0),"
            "coalesce((SELECT max(r.last_seen) FROM routes r "
            "WHERE r.channel=a.channel AND r.sender_id=a.sender_id),0))"
        )
        if connection["type_id"] in ("telegram", "whatsapp"):
            last_chat = last_chat.replace("dingtalk_messages", "direct_messages")
        field = {"id": "cursor", "last_chat": "last_chat_at", "tokens": "token_sort"}[sort]
        with self.db() as db:
            prefix, usage_join, token_value = "", "", "0"
            query_args = list(args)
            database = self.home / "sessions/corki.db"
            if sort == "tokens" and database.is_file():
                db.execute("ATTACH DATABASE ? AS usage_core", (database.as_uri() + "?mode=ro",))
                prefix = (
                    "user_totals AS (SELECT u.sender,sum(s.input_tokens+s.output_tokens) AS tokens "
                    "FROM usage_core.usage_threads u CROSS JOIN usage_core.usage_events s "
                    "ON s.thread_id=u.thread_id WHERE u.channel=? GROUP BY u.sender),"
                )
                query_args.insert(0, channel)
                usage_join = " LEFT JOIN user_totals u ON u.sender=a.sender_id "
                token_value = "coalesce(u.tokens,0)"
            boundary = ""
            if before is not None:
                if sort == "id":
                    boundary = "WHERE cursor<? "
                    query_args.append(before)
                else:
                    boundary = f"WHERE ({field},cursor)<(?,?) "
                    query_args.extend((before["value"], before["id"]))
            rows = db.execute(
                "WITH " + prefix + "candidates AS ("
                "SELECT a.rowid AS cursor,a.sender_id,a.sender_name,"
                "substr(a.first_message,1,240) AS first_message,a.created_at,a.status,"
                "d.status AS notification_status,d.error AS notification_error, "
                "d.kind AS notification_kind,d.updated AS notification_time, "
                "d.notice_id AS notification_id, "
                + user_id
                + " AS user_id, "
                + last_chat
                + " AS last_chat_at,"
                + token_value
                + " AS token_sort "
                "FROM access_requests a LEFT JOIN access_delivery d "
                "ON d.channel=a.channel AND d.sender_id=a.sender_id "
                + usage_join
                + " WHERE "
                + " AND ".join(conditions)
                + ") SELECT * FROM candidates "
                + boundary
                + f"ORDER BY {field} DESC,cursor DESC LIMIT 101",
                query_args,
            ).fetchall()
        requests = [dict(r) for r in rows[:100]]
        usage = (
            self.user_usage.totals(channel, [r["sender_id"] for r in requests])
            if sort != "tokens"
            else {}
        )
        for row in requests:
            row["conversation_id"] = str(row["cursor"])
            row["token_usage"] = (
                row.pop("token_sort") if sort == "tokens" else usage[row["sender_id"]]
            )
            row.pop("token_sort", None)
        cursor = None
        if len(rows) > 100:
            last = requests[-1]
            cursor = (
                last["cursor"]
                if sort == "id"
                else {
                    "sort": sort,
                    "id": last["cursor"],
                    "value": last["last_chat_at"] if sort == "last_chat" else last["token_usage"],
                }
            )
        return {"requests": requests, "before": cursor}

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

    def retry_access_notice(self, channel, sender, notice_id):
        if not isinstance(sender, str) or not isinstance(notice_id, str) or not notice_id:
            raise ValueError("通知不存在")
        row = self.connection(channel)
        if not row or not row["enabled"]:
            raise ValueError("请先启用此接入")
        if row["config"].get("session_validation") != "true":
            raise ValueError("此接入未开启会话校验")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Only retry the failure the user opened. A newer decision or another
            # retry must never be overwritten by a stale dialog or another window.
            changed = db.execute(
                "UPDATE access_delivery SET notice_id=?,status='queued',error='',updated=? "
                "WHERE channel=? AND sender_id=? AND notice_id=? AND status='failed'",
                (uuid.uuid4().hex, time.time(), channel, sender, notice_id),
            ).rowcount
            if not changed:
                raise ValueError("通知状态已更新，请刷新后重试")
            message = json.loads(
                db.execute(
                    "SELECT message FROM access_delivery WHERE channel=? AND sender_id=?",
                    (channel, sender),
                ).fetchone()[0]
            )
            if row["type_id"] == "dingtalk":
                message["_access_notice_mode"] = "proactive"
            # Keep the original text and newest saved reply credentials.
            db.execute(
                "INSERT INTO access_notice_queue SELECT notice_id,channel,sender_id,?,"
                "kind,text,'queued',updated FROM access_delivery WHERE channel=? AND sender_id=?",
                (json.dumps(message, ensure_ascii=False), channel, sender),
            )

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
