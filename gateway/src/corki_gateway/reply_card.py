"""Optional, coalesced presentation; no model calls or Agent execution semantics."""

import asyncio
import contextlib
import time


def card_options(row):
    config = row.get("config", {})
    return {
        "enabled": row.get("type_id") == "dingtalk" and config.get("ai_card_enabled") == "true",
        "template": config.get("ai_card_template", ""),
        "field": config.get("ai_card_content_key", "content") or "content",
    }


class ReplyCard:
    def __init__(self, connector, message, options):
        self.connector, self.message, self.options = connector, message, options
        self.task = None
        self.last_sent = 0.0
        self.last_phase = None
        self.answer_id = None
        self.answer = ""
        self.closed = False
        self.supported = True
        self.receipt = None

    def waiting(self):
        if (
            not self.closed
            and self.options["enabled"]
            and self.supported
            and (self.task is None or self.task.done())
        ):
            self.task = asyncio.create_task(self._send("waiting", ""))

    def offer(self, event=None):
        if self.closed or not self.options["enabled"] or not self.supported:
            return
        kind = type(event).__name__
        phase, text = "start", ""
        if kind == "AssistantTextDelta" and event.phase == "final_answer":
            if self.answer_id != event.item_id:
                self.answer_id, self.answer = event.item_id, ""
            self.answer = (self.answer + event.delta)[:16001]
            phase, text = "answer", self.answer
        elif kind == "AssistantMessageCompleted" and event.phase == "final_answer":
            self.answer_id, self.answer = event.item_id, event.text[:16001]
            phase, text = "answer", self.answer
        elif kind == "ToolCallStarted":
            phase = "working"
        elif kind in (
            "UserInputRequested",
            "ExecutionApprovalRequested",
            "McpElicitationRequested",
        ):
            phase = "waiting"
        elif event is not None:
            return
        if self.task is not None and not self.task.done():
            return
        now = time.monotonic()
        if phase == self.last_phase and (phase != "answer" or now - self.last_sent < 1):
            return
        self.last_sent, self.last_phase = now, phase
        self.task = asyncio.create_task(self._send(phase, text))

    async def _send(self, phase, text):
        try:
            result = await self.connector.call(
                "progress",
                {
                    "message": self.message,
                    "card": self.options,
                    "phase": phase,
                    "text": text,
                },
                timeout=50,
            )
            if isinstance(result, dict) and result.get("receipt"):
                self.receipt = result["receipt"]
            if not isinstance(result, dict) or not result.get("supported"):
                self.supported = False
        except Exception:
            self.supported = False

    async def settle(self, error=None, *, stopped=False):
        self.closed = True
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        if error and self.supported and self.options["enabled"]:
            await self._send("stopped" if stopped or error == "回答已停止" else "failed", error)
        return bool(error and self.options["enabled"] and self.supported)

    async def close(self):
        self.closed = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
