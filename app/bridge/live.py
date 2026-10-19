"""Transient display projection; canonical messages remain in Corki's repository."""

import tempfile

DELTA_KINDS = {"AssistantTextDelta", "AssistantReasoningDelta", "ToolOutputDelta"}
MEMORY_TEXT_BYTES = 4096


class SpooledText:
    """Small RAM window, anonymous temporary file for longer transient text.

    This is not another conversation store. The file is owned by the value,
    closed when its projection is discarded, and never used to resume an Agent.
    """

    def __init__(self, value=""):
        # Ownership follows this projection value, not the constructor scope.
        self.file = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=MEMORY_TEXT_BYTES, mode="w+b"
        )
        self.length = 0
        self.append(value)

    def append(self, value):
        self.file.seek(0, 2)
        self.file.write(value.encode("utf-8"))
        self.length += len(value)

    def __str__(self):
        self.file.seek(0)
        return self.file.read().decode("utf-8")

    def __len__(self):
        return self.length

    def __del__(self):
        file = getattr(self, "file", None)
        if file is not None:
            file.close()


def spool(value):
    if isinstance(value, str) and len(value.encode("utf-8")) > MEMORY_TEXT_BYTES:
        return SpooledText(value)
    if isinstance(value, dict):
        return {key: spool(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [spool(item) for item in value]
    return value


def append_event(events, kind, data):
    if kind in DELTA_KINDS:
        for previous in reversed(events):
            payload = previous["data"]
            # Anonymous model streams reuse their identity for the next response.
            # Never combine text across a completion or turn boundary.
            if payload.get("turn_id") != data.get("turn_id"):
                break
            if (
                kind == "AssistantTextDelta"
                and previous["type"] == "AssistantMessageCompleted"
                and (not data.get("item_id") or payload.get("item_id") == data.get("item_id"))
            ):
                break
            if previous["type"] != kind:
                continue
            if any(
                payload.get(key) != data.get(key) for key in ("item_id", "tool_call_id", "channel")
            ):
                continue
            if kind == "AssistantTextDelta":
                payload["delta"].append(data["delta"])
            else:
                combined = str(payload["delta"]) + data["delta"]
                payload["delta"] = spool(combined[-64000:])
            return
    payload = data.copy()
    if kind in DELTA_KINDS and kind != "AssistantTextDelta":
        payload["delta"] = payload["delta"][-64000:]
    payload = spool(payload)
    if kind == "AssistantTextDelta" and not isinstance(payload["delta"], SpooledText):
        payload["delta"] = SpooledText(payload["delta"])
    events.append({"type": kind, "data": payload})
    del events[:-200]


def interrupted_messages(events, turn):
    """Only unfinished visible text; completed items already belong to core history."""
    messages = {}
    for event in events:
        data = event["data"]
        if str(data.get("turn_id")) != turn:
            continue
        fallback = f"answer:{turn}"
        identity = data.get("item_id") or fallback
        if event["type"] == "AssistantTextDelta":
            messages[identity] = messages.get(identity, "") + str(data["delta"])
        elif event["type"] == "AssistantMessageCompleted":
            messages.pop(identity, None)
            messages.pop(fallback, None)
    return [
        {"id": identity, "content": content} for identity, content in messages.items() if content
    ]
