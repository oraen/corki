"""Explicit image replies bound to the current WeChat recipient and session permissions."""

import base64
import json
from pathlib import Path

from corki.execution.backend import file_operation
from corki.media.images import joined_work
from corki.protocol.tools import ToolResult, ToolSpec

from .images import MAX_BYTES
from .messages import send_logged


class SendWeChatImage:
    def __init__(self, host, channel, thread):
        self.host, self.channel, self.thread = host, channel, thread

    @property
    def spec(self):
        return ToolSpec(
            name="send_wechat_image",
            description=(
                "Send a local image file to the current WeChat user. Use when the user asks "
                "to receive a screenshot, photo, chart or generated image. Create the image first "
                "with available tools, then call this tool with its local path. This tool reads "
                "the file under the current session's permissions. Only claim the image was "
                "sent after this tool succeeds; do not send images merely viewed for analysis."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Local image file path."},
                    "caption": {"type": "string", "maxLength": 2000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(self, call, context):
        values = call.arguments or {}
        source = values.get("path")
        caption = values.get("caption", "")
        if not isinstance(source, str) or not source or "\0" in source:
            raise ValueError("图片路径无效")
        if not isinstance(caption, str) or len(caption) > 2000:
            raise ValueError("图片说明不能超过 2000 字")
        delivery = self.host.deliveries.get(self.thread)
        row = self.host.store.connection(self.channel)
        if (
            not delivery
            or delivery[0] != self.channel
            or not row
            or not row["enabled"]
            or self.channel not in self.host.connectors
            or self.host.stopping
        ):
            raise ValueError("微信连接已断开，图片未发送")
        path = Path(source)
        path = path if path.is_absolute() else context.cwd / path
        if context.execution_permissions is None:

            def read():
                with path.open("rb") as file:
                    return file.read(MAX_BYTES + 1)

            raw = await joined_work(read)
        else:
            encoded = await file_operation(
                context.execution_permissions,
                context.cwd,
                "image",
                {"path": str(path), "max_bytes": MAX_BYTES},
                output_limit=MAX_BYTES * 2 + 4096,
            )
            raw = base64.b64decode(encoded, validate=True)
        image = await joined_work(self.host.store.images.save_bytes, self.channel, raw)
        row = self.host.store.connection(self.channel)
        if (
            not row
            or not row["enabled"]
            or self.host.stopping
            or self.host.deliveries.get(self.thread) is not delivery
        ):
            raise ValueError("微信连接已断开，图片未发送")
        message = delivery[1]
        result = await send_logged(
            self.host,
            self.channel,
            {
                "message": message,
                "text": caption,
                "images": [image],
                "record_key": "tool-image:" + self.thread + ":" + call.id,
            },
            timeout=180,
            kind="agent",
        )
        if not result.get("sent"):
            raise ValueError("微信未确认图片发送")
        return ToolResult(call.id, call.name, json.dumps({"sent": True, "image": image["id"]}))
