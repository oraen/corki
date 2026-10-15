"""Read a local image into a bounded multimodal tool result."""

from __future__ import annotations

import asyncio
import base64
from copy import copy
from pathlib import Path

from corki.execution.backend import file_operation
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

    def with_image_policy(self, policy):
        tool = copy(self)
        tool._supports_images = policy.supports_images
        tool._supports_original = policy.supports_original
        tool._unified = policy.unified_budget and policy.supports_original
        return tool

    @property
    def spec(self) -> ToolSpec:
        properties = {
            "path": {
                "type": "string",
                "description": "Local filesystem path to an image file.",
            }
        }
        if self._supports_original and not self._unified:
            properties["detail"] = {
                "type": "string",
                "enum": ["high", "original"],
                "description": "Image detail level. Defaults to `high`; use `original` "
                "to preserve exact resolution.",
            }
        return ToolSpec(
            name="view_image",
            description="View a local image file from the filesystem when visual inspection "
            "is needed. Use this for images already available on disk.",
            parameters={
                "type": "object",
                "properties": properties,
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
        async with self._gate:
            if context.execution_permissions is None:
                path = path.resolve()
                if not path.is_file():
                    raise ValueError(f"image does not exist: {path}")
                data = await joined_work(self._read, path)
            else:
                encoded = await file_operation(
                    context.execution_permissions,
                    context.cwd,
                    "image",
                    {"path": str(path), "max_bytes": self._max_bytes},
                    output_limit=self._max_bytes * 2 + 4096,
                )
                data = base64.b64decode(encoded, validate=True)
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
