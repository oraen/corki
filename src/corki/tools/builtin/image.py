"""Read a local image into a bounded multimodal tool result."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path

from corki.protocol.tools import (
    ImageAttachment,
    ToolCall,
    ToolConcurrency,
    ToolResult,
    ToolSpec,
)
from corki.tools.base import ToolContext

_SUPPORTED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class ViewImageTool:
    def __init__(self, *, max_bytes: int = 20 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="view_image",
            description="Read a local PNG, JPEG, GIF, or WebP image for visual inspection.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "detail": {"type": "string", "enum": ["high", "original"]},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        path = Path(str(call.arguments["path"]))
        path = path if path.is_absolute() else context.cwd / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"image does not exist: {path}")
        mime_type = mimetypes.guess_type(path.name)[0]
        if mime_type not in _SUPPORTED_TYPES:
            raise ValueError(f"unsupported image type: {mime_type or 'unknown'}")
        data = await asyncio.to_thread(path.read_bytes)
        if len(data) > self._max_bytes:
            raise ValueError(f"image exceeds {self._max_bytes} byte limit")
        detail = str(call.arguments.get("detail", "high"))
        attachment = ImageAttachment(
            data_url=f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
            detail=detail,
        )
        metadata = json.dumps(
            {"path": str(path), "mime_type": mime_type, "bytes": len(data), "detail": detail}
        )
        return ToolResult(
            call.id,
            call.name,
            metadata,
            display_content=f"loaded {path.name} ({len(data)} bytes)",
            attachments=(attachment,),
        )
