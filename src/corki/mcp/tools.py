"""Adapters exposing remotely described MCP tools through Corki's registry."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from corki.mcp.client import MCPClient
from corki.protocol.tools import (
    ImageAttachment,
    ToolCall,
    ToolConcurrency,
    ToolExposure,
    ToolResult,
    ToolSpec,
)
from corki.tools import ToolContext

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")
_MAX_IMAGE_DATA_CHARS = 20_000_000


def exposed_tool_name(server: str, remote: str) -> str:
    value = f"mcp__{_SAFE_NAME.sub('_', server)}__{_SAFE_NAME.sub('_', remote)}"
    if len(value) <= 64:
        return value
    return f"{value[:47]}_{sha256(value.encode()).hexdigest()[:16]}"


class MCPTool:
    def __init__(
        self,
        server_name: str,
        definition: Mapping[str, Any],
        client: MCPClient,
        *,
        exposure: ToolExposure = ToolExposure.DIRECT,
    ) -> None:
        remote_name = definition.get("name")
        if not isinstance(remote_name, str) or not remote_name:
            raise ValueError("MCP tool is missing a name")
        description = definition.get("description")
        schema = definition.get("inputSchema", {"type": "object", "properties": {}})
        if not isinstance(schema, Mapping):
            raise ValueError(f"MCP tool {remote_name} has an invalid input schema")
        annotations = definition.get("annotations", {})
        read_only = isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is True
        self.remote_name = remote_name
        self._client = client
        self._spec = ToolSpec(
            exposed_tool_name(server_name, remote_name),
            str(description or f"MCP tool {remote_name} from {server_name}")[:1_024],
            dict(schema),
            exposure=exposure,
            source=server_name,
            search_text=" ".join(
                [
                    exposed_tool_name(server_name, remote_name),
                    remote_name,
                    server_name,
                    str(definition.get("title") or ""),
                    str(description or ""),
                    *sorted(schema.get("properties", {})),
                ]
            ),
            concurrency=ToolConcurrency.PARALLEL if read_only else ToolConcurrency.EXCLUSIVE,
            output_char_budget=160_000,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        result = await self._client.call_tool(self.remote_name, call.arguments)
        is_error = result.get("isError") is True
        text_parts: list[str] = []
        attachments: list[ImageAttachment] = []
        for item in result.get("content", []):
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
            elif (
                item.get("type") == "image"
                and isinstance(item.get("data"), str)
                and isinstance(item.get("mimeType"), str)
                and item["mimeType"].startswith("image/")
            ):
                data = item["data"]
                mime_type = item["mimeType"]
                if len(data) <= _MAX_IMAGE_DATA_CHARS:
                    attachments.append(ImageAttachment(f"data:{mime_type};base64,{data}"))
                    text_parts.append(f"<MCP image: {mime_type}>")
                else:
                    text_parts.append("<MCP image omitted: payload exceeds 20,000,000 characters>")
            else:
                text_parts.append(json.dumps(dict(item), ensure_ascii=False))
        structured = result.get("structuredContent")
        if structured is not None:
            text_parts.append(json.dumps(structured, ensure_ascii=False))
        content = "\n".join(text_parts) or json.dumps(dict(result), ensure_ascii=False)
        return ToolResult(
            call.id,
            call.name,
            content,
            is_error=is_error,
            attachments=tuple(attachments),
        )
