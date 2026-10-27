"""Durable desktop follow-ups. Never replay an ambiguously delivered input."""

import asyncio
import json
from contextlib import closing
from datetime import UTC, datetime

from protocol import dumps

from corki.realtime import RealtimeTurnClosedError


class FollowUps:
    def __init__(self, host):
        self.host = host
        self.pumps = set()
        with closing(host.state.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS followups (
                  id TEXT PRIMARY KEY, thread TEXT NOT NULL, payload TEXT NOT NULL,
                  digest TEXT NOT NULL, position INTEGER NOT NULL,
                  state TEXT NOT NULL DEFAULT 'queued', error TEXT, item_id TEXT);
                CREATE INDEX IF NOT EXISTS followups_thread ON followups(thread,position);
                CREATE TABLE IF NOT EXISTS followup_settings (
                  thread TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'steer',
                  paused INTEGER NOT NULL DEFAULT 0);
            """)
            if "item_id" not in {r[1] for r in db.execute("PRAGMA table_info(followups)")}:
                db.execute("ALTER TABLE followups ADD COLUMN item_id TEXT")
            db.execute(
                "UPDATE followups SET state='unknown', "
                "error='上次发送结果待确认，请核对会话；不会自动重发。' "
                "WHERE state IN ('dispatching','delivered')"
            )
            db.execute(
                "UPDATE followup_settings SET paused=1 "
                "WHERE thread IN (SELECT thread FROM followups)"
            )

    def rows(self, thread):
        with closing(self.host.state.connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM followups WHERE thread=? ORDER BY position,rowid", (thread,)
                )
            ]

    def receipt(self, identity, digest=None):
        with closing(self.host.state.connect()) as db:
            r = db.execute("SELECT * FROM followups WHERE id=?", (identity,)).fetchone()
        if not r or r["state"] == "delivered":
            return None
        if digest is not None and r["digest"] != digest:
            raise ValueError("请求 ID 已用于其他内容")
        return {"id": identity, "thread_id": r["thread"], "state": "queued"}

    def snapshot(self, thread):
        with closing(self.host.state.connect()) as db:
            r = db.execute(
                "SELECT mode,paused FROM followup_settings WHERE thread=?", (thread,)
            ).fetchone()
        return {
            "mode": r["mode"] if r else "steer",
            "paused": bool(r["paused"]) if r else False,
            "messages": [
                {
                    "id": r["id"],
                    "content": json.loads(r["payload"])["content"],
                    "createdAt": json.loads(r["payload"]).get("submittedAt"),
                    "attachments": [
                        a["name"] for a in json.loads(r["payload"]).get("attachments", [])
                    ],
                    "state": r["state"],
                    "error": r["error"],
                }
                for r in self.rows(thread)
                if r["state"] != "delivered"
            ],
        }

    def setting(self, thread, *, mode=None, paused=None):
        with closing(self.host.state.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO followup_settings(thread) VALUES (?)", (thread,))
            if mode is not None:
                db.execute("UPDATE followup_settings SET mode=? WHERE thread=?", (mode, thread))
            if paused is not None:
                db.execute(
                    "UPDATE followup_settings SET paused=? WHERE thread=?", (int(paused), thread)
                )

    def stage(self, p, digest):
        thread = p["threadId"]
        if sum(r["state"] in ("queued", "dispatching", "delivered") for r in self.rows(thread)) >= 50:
            raise ValueError("消息发送较多，请稍后再试")
        self.setting(thread)
        with closing(self.host.state.connect()) as db, db:
            db.execute(
                "INSERT INTO followups(id,thread,payload,digest,position) VALUES "
                "(?,?,?,?,(SELECT COALESCE(MAX(position),0)+1 FROM followups WHERE thread=?))",
                (p["requestId"], thread, dumps({**p, "submittedAt": datetime.now(UTC).isoformat()}), digest, thread),
            )

    def resume_for_input(self, thread, keep_id=None):
        """A fresh send continues independently of old stopped/uncertain inputs."""
        with closing(self.host.state.connect()) as db:
            settings = db.execute("SELECT paused FROM followup_settings WHERE thread=?", (thread,)).fetchone()
        if not settings or not settings[0]:
            return
        for row in self.rows(thread):
            if row["state"] == "queued" and row["id"] != keep_id:
                self.mark(row["id"], "failed", "上一轮已结束，这条消息尚未发送。")
        self.setting(thread, paused=False)

    def mark(self, identity, state, error=None):
        with closing(self.host.state.connect()) as db, db:
            db.execute("UPDATE followups SET state=?,error=? WHERE id=?", (state, error, identity))

    def remove(self, identity):
        with closing(self.host.state.connect()) as db, db:
            db.execute("DELETE FROM followups WHERE id=?", (identity,))

    async def changed(self, thread):
        await self.host.emit("queue.changed", thread, self.snapshot(thread))

    def final_answer(self, thread):
        # Match Codex: only the latest assistant item with nonempty final text.
        for event in reversed(self.host.live.get(thread, [])):
            if event["type"] in ("AssistantTextDelta", "AssistantMessageCompleted"):
                d = event["data"]
                return d.get("phase") == "final_answer" and bool(
                    str(d.get("text", d.get("delta", ""))).strip()
                )
            if event["type"] in (
                "TurnStarted",
                "ToolCallStarted",
                "ToolCallCompleted",
                "AssistantReasoningDelta",
                "UserMessageSubmitted",
            ):
                break
        return False

    async def accept(self, p, digest, content, images, *, force=False):
        h, thread, identity = self.host, p["threadId"], p["requestId"]
        if not force:
            self.stage(p, digest)
        running = thread in h.tasks
        # A follow-up belongs to the active task even if the selector changed.
        # Only an already-final answer (or a closing runtime below) defers it.
        # Ignore legacy queue preferences: desktop offers steering only.
        may_steer = running and (
            force or (not self.final_answer(thread) and not self.snapshot(thread)["paused"])
        )
        if may_steer:
            self.mark(identity, "dispatching")
            try:
                item = await h.runtimes[thread].steer(content, attachments=images)
            except RealtimeTurnClosedError:
                self.mark(identity, "queued")
            except Exception as error:
                # Runtime validation fails before acceptance; retain editable input.
                self.mark(identity, "failed", h.safe_error(error))
                self.setting(thread, paused=True)
            else:
                h.state.admit(identity, thread, digest)
                h.state.update_request(identity, "accepted", str(item.turn_id))
                data = {
                    "request_id": identity,
                    "item_id": str(item.id),
                    "turn_id": str(item.turn_id),
                    "text": content,
                    "createdAt": datetime.now(UTC).isoformat(),
                }
                from live import append_event

                append_event(h.live[thread], "UserMessageSubmitted", data)
                self.mark(identity, "delivered")
                with closing(h.state.connect()) as db, db:
                    db.execute(
                        "UPDATE followups SET item_id=? WHERE id=?", (str(item.id), identity)
                    )
                await h.emit("UserMessageSubmitted", thread, data)
                await self.changed(thread)
                return h.state.request(identity)
        if running:
            h.runtimes[thread].defer_goal_continuation()
        await self.changed(thread)
        self.schedule(thread)
        return self.receipt(identity)

    def schedule(self, thread):
        if self.host.stopping:
            return
        task = asyncio.create_task(self.pump(thread))
        self.pumps.add(task)
        task.add_done_callback(self.pumps.discard)

    async def pump(self, thread):
        h = self.host
        lock = h.locks.setdefault(thread, asyncio.Lock())
        async with lock:
            if h.stopping or thread in h.tasks or self.snapshot(thread)["paused"]:
                return
            rows = [r for r in self.rows(thread) if r["state"] == "queued"]
            if not rows:
                return
            row = rows[0]
            self.mark(row["id"], "dispatching")
        try:
            await h._submit(json.loads(row["payload"]), queued_id=row["id"])
        except Exception as error:
            if h.state.request(row["id"]):
                self.remove(row["id"])
            else:
                self.mark(row["id"], "failed", h.safe_error(error))
                self.setting(thread, paused=True)
        await self.changed(thread)

    def unknown(self, thread):
        for row in self.rows(thread):
            if row["state"] == "delivered":
                self.mark(row["id"], "unknown", "发送结果待确认，请核对会话；不会自动重发。")

    def settle(self, thread, unsubmitted=()):
        returned = {str(item.id) for item in unsubmitted}
        with closing(self.host.state.connect()) as db, db:
            for row in self.rows(thread):
                if row["state"] != "delivered":
                    continue
                if row["item_id"] in returned:
                    db.execute(
                        "UPDATE followups SET state='queued',item_id=NULL,error=NULL WHERE id=?",
                        (row["id"],),
                    )
                    db.execute("DELETE FROM requests WHERE id=?", (row["id"],))
                else:
                    db.execute("DELETE FROM followups WHERE id=?", (row["id"],))

    async def action(self, p):
        from service import text

        thread = text(p, "threadId")
        action = text(p, "action", default="read")
        h = self.host
        lock = h.locks.setdefault(thread, asyncio.Lock())
        async with lock:
            if h.state.is_deleted(thread):
                raise ValueError("此会话已删除")
            if action == "mode":
                mode = p.get("mode")
                if mode not in ("queue", "steer"):
                    raise ValueError("无效的追加方式")
                self.setting(thread, mode=mode)
            elif action == "resume":
                if any(r["state"] == "unknown" for r in self.rows(thread)):
                    raise ValueError("请先核对发送结果并移除结果待确认的消息")
                self.setting(thread, paused=False)
                for r in self.rows(thread):
                    if r["state"] == "failed":
                        self.mark(r["id"], "queued")
            elif action in ("delete", "edit", "move", "send", "detail"):
                identity = text(p, "id")
                row = next((r for r in self.rows(thread) if r["id"] == identity), None)
                if row is None:
                    raise ValueError("该消息已发送或移除")
                if action == "detail":
                    return {"content": json.loads(row["payload"])["content"]}
                if row["state"] in ("dispatching", "delivered"):
                    raise ValueError("正在发送，请稍后再试")
                if action == "delete":
                    self.remove(identity)
                elif row["state"] == "unknown":
                    raise ValueError("发送结果待确认，请核对会话后移除此消息，避免重复执行")
                elif action == "edit":
                    value = text(p, "content", limit=100000)
                    payload = json.loads(row["payload"])
                    if not value.strip() and not payload.get("attachments"):
                        raise ValueError("请输入消息")
                    payload["content"] = value
                    with closing(h.state.connect()) as db, db:
                        db.execute(
                            "UPDATE followups SET payload=?,state='queued',error=NULL WHERE id=?",
                            (dumps(payload), identity),
                        )
                elif action == "move":
                    rows = self.rows(thread)
                    index = next(i for i, r in enumerate(rows) if r["id"] == identity)
                    direction = p.get("direction")
                    if direction not in (-1, 1):
                        raise ValueError("无效的排序方向")
                    other = index + direction
                    if 0 <= other < len(rows):
                        rows[index], rows[other] = rows[other], rows[index]
                        with closing(h.state.connect()) as db, db:
                            db.executemany(
                                "UPDATE followups SET position=? WHERE id=?",
                                [(i, r["id"]) for i, r in enumerate(rows)],
                            )
                else:
                    # Reuse the complete submit validation, outside this lock.
                    self.mark(identity, "queued")
                    self.resume_for_input(thread, keep_id=identity)
            elif action != "read":
                raise ValueError("无效的队列操作")
        if action == "send":
            try:
                await h._submit(json.loads(row["payload"]), queued_id=identity, force_steer=True)
            except Exception as error:
                self.mark(identity, "failed", h.safe_error(error))
                self.setting(thread, paused=True)
                await self.changed(thread)
                raise
        elif action == "resume":
            self.schedule(thread)
        if action != "read":
            await self.changed(thread)
        return self.snapshot(thread)
