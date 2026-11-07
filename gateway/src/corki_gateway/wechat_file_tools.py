"""Send local files/videos to the active WeChat recipient under session read policy."""

import base64
import hashlib
import json
from pathlib import Path

from corki.execution.backend import file_operation
from corki.media.images import joined_work
from corki.protocol.tools import ToolResult, ToolSpec

from .messages import send_logged
from .wechat_files import MAX_BYTES


class SendWeChatFile:
    def __init__(self, host, channel, thread, *, video=False):
        self.host, self.channel, self.thread, self.video = host, channel, thread, video
        self.platform = (host.store.connection(channel) or {}).get("type_id", "wechat")

    @property
    def spec(self):
        kind = "MP4 video" if self.video else "file"
        return ToolSpec(
            name=f"send_{self.platform}_video" if self.video else f"send_{self.platform}_file",
            description=f"Send a local {kind} to the current {self.platform} user when requested. "
            "Create the file first; reads follow session permissions. Only claim delivery "
            "after success. Never execute received attachments. "
            f"Use send_{self.platform}_image for images.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": f"Local {kind} path."},
                    "caption": {"type": "string", "maxLength": 2000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call, context):
        source, caption = call.arguments.get("path"), call.arguments.get("caption", "")
        if not isinstance(source, str) or not source or "\0" in source:
            raise ValueError("附件路径无效")
        if not isinstance(caption, str) or len(caption) > 2000:
            raise ValueError("附件说明不能超过 2000 字")
        delivery = self.host.deliveries.get(self.thread)
        row = self.host.store.connection(self.channel)
        if not delivery or delivery[0] != self.channel or not row or not row["enabled"]:
            raise ValueError("会话已断开，附件未发送")
        path = Path(source)
        path = path if path.is_absolute() else context.cwd / path
        if context.execution_permissions is None:

            def read():
                with path.open("rb") as stream:
                    return stream.read(MAX_BYTES + 1)

            raw = await joined_work(read)
        else:
            encoded = await file_operation(
                context.execution_permissions,
                context.cwd,
                "read_bytes",
                {"path": str(path), "max_bytes": MAX_BYTES},
                output_limit=MAX_BYTES * 2 + 4096,
            )
            raw = base64.b64decode(encoded, validate=True)
        name = path.name
        identity = hashlib.sha256(raw).hexdigest()
        if path.resolve() == self.host.store.wechat_files.file(self.channel, identity).resolve():
            name = self.host.store.wechat_files.info(self.channel, identity)["name"]
        info = await joined_work(
            self.host.store.wechat_files.save_bytes,
            self.channel,
            raw,
            name,
            "video" if self.video else "file",
        )
        if (
            self.host.deliveries.get(self.thread) is not delivery
            or self.host.stopping
            or not (self.host.store.connection(self.channel) or {}).get("enabled")
        ):
            raise ValueError("会话已更换或关闭，附件未发送")
        result = await send_logged(
            self.host,
            self.channel,
            {
                "message": delivery[1],
                "text": caption,
                "files": [info],
                "record_key": "tool-file:" + self.thread + ":" + call.id,
            },
            timeout=180,
            kind="agent",
        )
        if not result.get("sent"):
            raise ValueError("渠道未确认附件发送")
        return ToolResult(call.id, call.name, json.dumps({"sent": True, "file": info["id"]}))
