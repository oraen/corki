"""Pair nearby WeChat text/image events without combining successive questions."""

import asyncio
import contextlib
import time

PAIR_WAIT = 1.5
MAX_WAIT = 3.0


def has_text(message):
    text = message["text"]
    markers = [(p, f"[Image #{i + 1}]") for i, p in enumerate(message.get("image_positions", []))]
    markers += [(p, f"[File #{i + 1}]") for i, p in enumerate(message.get("file_positions", []))]
    for position, label in sorted(markers, reverse=True):
        text = text[:position] + text[position + len(label) :]
    return bool(text.strip())


class WeChatBatch:
    def __init__(self, message):
        self.messages = [message]
        self.first = self.last = time.monotonic()
        self.sealed = False
        self.changed = asyncio.Event()

    @property
    def paired(self):
        return any(has_text(m) for m in self.messages) and any(
            m.get("image_refs") or m.get("file_refs") for m in self.messages
        )

    def append(self, message):
        now = time.monotonic()
        if (
            self.sealed
            or self.paired
            or now - self.last >= PAIR_WAIT
            or now - self.first >= MAX_WAIT
            or message.get("media_error")
            or any(m.get("media_error") for m in self.messages)
            or sum(
                len(m.get("image_refs", [])) + len(m.get("file_refs", [])) for m in self.messages
            )
            + len(message.get("image_refs", []))
            + len(message.get("file_refs", []))
            > 8
            or sum(len(m["text"]) + 1 for m in self.messages) + len(message["text"]) > 99000
            # Two separate text questions are separate turns, even when rapid.
            or has_text(message)
            and any(has_text(m) for m in self.messages)
        ):
            self.sealed = True
            self.changed.set()
            return False
        self.messages.append(message)
        self.last = now
        self.changed.set()
        return True

    async def collect(self):
        while not self.sealed and not self.paired:
            self.changed.clear()
            delay = min(self.last + PAIR_WAIT, self.first + MAX_WAIT) - time.monotonic()
            if delay <= 0:
                break
            try:
                await asyncio.wait_for(self.changed.wait(), delay)
            except TimeoutError:
                break
        self.sealed = True
        text, refs, positions, files, file_positions = "", [], [], [], []
        for message in self.messages:
            if text:
                text += "\n"
            cursor = 0
            markers = []
            for kind, ref_key, position_key in (
                ("Image", "image_refs", "image_positions"),
                ("File", "file_refs", "file_positions"),
            ):
                for index, (ref, position) in enumerate(
                    zip(message.get(ref_key, []), message.get(position_key, []), strict=True)
                ):
                    markers.append((position, kind, index, ref))
            for position, kind, index, ref in sorted(markers):
                text += message["text"][cursor:position]
                target, offsets = (refs, positions) if kind == "Image" else (files, file_positions)
                offsets.append(len(text))
                target.append(ref)
                text += f"[{kind} #{len(target)}]"
                cursor = position + len(f"[{kind} #{index + 1}]")
            text += message["text"][cursor:]
        return {
            **self.messages[0],
            "text": text,
            "image_refs": refs,
            "image_positions": positions,
            "file_refs": files,
            "file_positions": file_positions,
            "context_token": self.messages[-1].get("context_token")
            or self.messages[0].get("context_token", ""),
            "_wechat_parts": list(self.messages),
        }


async def typing(connector, message):
    """Best effort status only: failures must never fail or delay an Agent turn."""
    while True:
        with contextlib.suppress(Exception):
            await connector.call("typing", {"message": message, "active": True}, timeout=8)
        await asyncio.sleep(6)
