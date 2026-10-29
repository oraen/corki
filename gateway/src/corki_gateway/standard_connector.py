"""Sequential HTTP inbox/outbox worker; never rerun the Agent to resend a reply."""

import asyncio
import contextlib
import json
import uuid

import httpx

from .standard import MAX_BATCH, resolved


class StandardConnector:
    def __init__(self, host, row, *, client=None, sleep=asyncio.sleep):
        self.host, self.row, self.id = host, row, row["id"]
        self.queue = host.store.http_queue
        self.urls = resolved(row)
        self.retries = int(row["config"].get("http_retry_count", row["manifest"]["retry_count"]))
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        self.sleep = sleep
        self.reader = None
        self.wake = asyncio.Event()

    async def start(self):
        self.queue.recover(self.id)
        self.reader = asyncio.create_task(self.run())

    async def post(self, endpoint, payload):
        async with asyncio.timeout(15):
            return await self._post(endpoint, payload)

    async def _post(self, endpoint, payload):
        async with self.client.stream("POST", self.urls[endpoint], json=payload) as response:
            if not 200 <= response.status_code < 300:
                raise ValueError("接口返回非成功状态")
            raw = bytearray()
            async for part in response.aiter_bytes():
                raw.extend(part)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("接口响应超过 4 MB")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise ValueError("接口未返回有效 JSON") from error
        if not isinstance(value, dict):
            raise ValueError("接口应返回 JSON 对象")
        return value

    def status(self, state, code=None):
        self.host.connector_status(self.id, {"state": state, "code": code})

    async def deliver(self, row):
        payload = json.loads(row["reply"])
        while row["attempts"] < self.retries + 1:
            self.queue.attempt(self.id, row["id"])
            try:
                ack = await self.post("reply_url", payload)
                if (
                    ack.get("received") is not True
                    or ack.get("message_id") != payload["message_id"]
                    or ack.get("reply_id") != payload["reply_id"]
                ):
                    raise ValueError("回复回执不匹配")
            except (httpx.HTTPError, ValueError, TimeoutError):
                row = self.queue.get(self.id, row["id"])
                if row["attempts"] < self.retries + 1:
                    await self.sleep(min(5, 2 ** (row["attempts"] - 1)))
                    continue
                break
            self.queue.update(self.id, row["id"], "sent")
            self.status("online")
            return {"sent": True}
        self.queue.update(
            self.id, row["id"], "delivery_failed", error="回复未获确认，已达到重试次数"
        )
        self.host.store.finish(
            self.id, row["id"], "delivery_failed", "回复未获确认，已达到重试次数"
        )
        self.status("online")
        raise ValueError("渠道回复发送失败，回答已保存，将继续处理后续消息")

    async def call(self, method, params=None, timeout=45):
        if method != "send":
            raise ValueError("标准连接器不支持此操作")
        message, text = params["message"], params["text"]
        if params.get("final"):
            row = self.queue.reply(self.id, message["id"], text)
            if not row:
                raise ValueError("渠道消息已不存在")
            return await self.deliver(row)
        # Approval/questions are notices, not the final answer. Never acknowledge the inbox here.
        payload = {
            "protocol": 1,
            "type": "notice",
            "connection_id": self.id,
            "message_id": message["id"],
            "reply_id": params.get("notice_id") or uuid.uuid4().hex,
            "text": text,
        }
        ack = await self.post("reply_url", payload)
        if (
            ack.get("received") is not True
            or ack.get("reply_id") != payload["reply_id"]
            or ack.get("message_id") != message["id"]
        ):
            raise ValueError("渠道通知未获确认")
        return {"sent": True}

    async def run(self):
        empty = 0
        while True:
            row = self.queue.first(self.id)
            if row:
                empty = 0
                if row["phase"] == "blocked":
                    self.status("error", "interrupted")
                    self.wake.clear()
                    # State may have been changed before the event was cleared.
                    if self.queue.first(self.id)["phase"] == "blocked":
                        await self.wake.wait()
                    continue
                if row["phase"] == "ready":
                    account = self.host.store.connection(self.id)
                    if not self.host.store.allows_message(
                        account, json.loads(row["payload"]), remember=False
                    ):
                        self.queue.update(self.id, row["id"], "skipped")
                        self.host.store.finish(self.id, row["id"], "access_denied")
                        continue
                    try:
                        await self.deliver(row)
                        self.host.store.finish(self.id, row["id"], "completed")
                    except ValueError:
                        pass
                    continue
                if row["phase"] == "running":
                    self.queue.recover(self.id)
                    continue
                self.queue.update(self.id, row["id"], "running")
                message = json.loads(row["payload"])
                try:
                    result = await self.host.inbound(self.id, message)
                except ValueError:
                    self.queue.update(self.id, row["id"], "queued")
                    await self.sleep(1)
                    continue
                if result.get("access_denied"):
                    self.queue.update(self.id, row["id"], "skipped")
                    continue
                if result.get("thread"):
                    self.queue.bind_answer(self.id, row["id"], result["thread"])
                task = self.host.tasks.get((self.id, row["id"]))
                if result.get("duplicate") or task is None:
                    self.queue.update(
                        self.id,
                        row["id"],
                        "blocked",
                        error="消息已执行或结果未知，请检查会话后跳过；不会自动重复执行",
                    )
                    continue
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Revocation can cancel a task before execute() enters its handler.
                    # Only the worker's own cancellation should stop queue processing.
                    if asyncio.current_task().cancelling():
                        raise
                current = self.queue.get(self.id, row["id"])
                if current and current["phase"] == "running":
                    with self.host.store.db() as db:
                        receipt = db.execute(
                            "SELECT status FROM receipts WHERE channel=? AND id=?",
                            (self.id, row["id"]),
                        ).fetchone()
                    if receipt and receipt[0] == "access_denied":
                        self.queue.update(self.id, row["id"], "skipped")
                    elif receipt and receipt[0] == "failed":
                        # Confirm an explicit failed result before the next message.
                        with contextlib.suppress(ValueError):
                            await self.call(
                                "send",
                                {
                                    "message": message,
                                    "text": "任务执行失败，请在 Corki 中查看会话。",
                                    "final": True,
                                },
                            )
                    else:
                        self.queue.update(
                            self.id,
                            row["id"],
                            "blocked",
                            error="执行已停止，无法确认结果，请检查会话后跳过此消息",
                        )
                continue
            try:
                value = await self.post(
                    "pull_url",
                    {
                        "protocol": 1,
                        "connection_id": self.id,
                        "cursor": self.host.store.cursor(self.id),
                        "limit": MAX_BATCH,
                    },
                )
                count = self.queue.enqueue(self.id, value)
                self.status("online")
            except (httpx.HTTPError, ValueError, TimeoutError):
                self.status("error", "http_pull")
                await self.sleep(5)
                continue
            if count:
                empty = 0
            else:
                empty += 1
                await self.sleep(5 if empty >= 10 else 1)

    async def close(self):
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader
        self.queue.recover(self.id)
        await self.client.aclose()
