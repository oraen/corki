"""Read a local image into a bounded multimodal tool result."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from corki.media.images import joined_work, validate_image_bytes
from corki.protocol.tools import (
    CodeModeOutput,
    ImageAttachment,
    ToolCall,
    ToolConcurrency,
    ToolResult,
    ToolSpec,
)
from corki.tools.base import ToolContext


class ViewImageTool:
    def __init__(
        self,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        supports_images=True,
        supports_original=False,
        unified_budget=False,
    ) -> None:
        self._max_bytes = max_bytes
        self._supports_images = supports_images
        self._supports_original = supports_original
        self._unified = unified_budget and supports_original
        self._gate = asyncio.Semaphore(2)

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
        if not self._supports_images:
            raise ValueError("view_image is not allowed because you do not support image inputs")
        assert call.arguments is not None
        path = Path(str(call.arguments["path"]))
        path = path if path.is_absolute() else context.cwd / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"image does not exist: {path}")
        async with self._gate:
            data = await joined_work(self._read, path)
        mime_type = "application/octet-stream"
        requested_detail = str(call.arguments.get("detail", "high"))
        if requested_detail not in {"high", "original"}:
            raise ValueError("view_image.detail only supports high or original")
        detail = (
            "original"
            if self._unified or requested_detail == "original" and self._supports_original
            else "high"
        )
        attachment = ImageAttachment(
            data_url=f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
            detail=detail,
        )
        return ToolResult(
            call.id,
            call.name,
            "",
            display_content=f"loaded {path.name} ({len(data)} bytes)",
            content_items=(attachment,),
            code_mode_output=CodeModeOutput(
                {"image_url": attachment.data_url, **({} if self._unified else {"detail": detail})}
            ),
        )

    def _read(self, path):
        with path.open("rb") as stream:
            data = stream.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise ValueError(f"image exceeds {self._max_bytes} byte limit")
        try:
            validate_image_bytes(data)
        except Exception:
            raise ValueError("unable to process image: invalid or unsupported image data") from None
        return data
