"""Small MCP 2025-03-26 JSON-RPC clients for stdio and Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import httpx

from corki.config import MCPServerSettings

# Keep Corki's stable MCP lifecycle on the same legacy proposal as Codex.
# The server may negotiate an older supported value during initialize.
PROTOCOL_VERSION = "2025-06-18"
MAX_LIST_PAGES = 100
MAX_REMOTE_TOOLS = 1_024
MAX_HTTP_RESPONSE_BYTES = 16 * 1_024 * 1_024
STDIO_BUFFER_LIMIT = 4 * 1_024 * 1_024


class MCPProtocolError(RuntimeError):
    pass


class MCPClient(ABC):
    def __init__(self, settings: MCPServerSettings) -> None:
        self.settings = settings
        self._next_id = 0
        self._initialized = False
        self._protocol_version = PROTOCOL_VERSION

    async def initialize(self) -> None:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "corki", "version": "0.1.0"},
            },
        )
        if not isinstance(result, Mapping):
            raise MCPProtocolError("initialize returned a non-object result")
        negotiated = result.get("protocolVersion")
        if not isinstance(negotiated, str) or not negotiated:
            raise MCPProtocolError("initialize result is missing protocolVersion")
        self._protocol_version = negotiated
        self._initialized = True
        await self.notify("notifications/initialized", {})

    @abstractmethod
    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[dict[str, Any], ...]:
        return await self._list_paginated("tools/list", "tools")

    async def list_resources(self) -> tuple[dict[str, Any], ...]:
        return await self._list_paginated("resources/list", "resources")

    async def list_resource_templates(self) -> tuple[dict[str, Any], ...]:
        return await self._list_paginated("resources/templates/list", "resourceTemplates")

    async def read_resource(self, uri: str) -> Mapping[str, Any]:
        result = await self.request("resources/read", {"uri": uri})
        if not isinstance(result, Mapping):
            raise MCPProtocolError("resources/read returned a non-object result")
        return result

    async def list_prompts(self) -> tuple[dict[str, Any], ...]:
        return await self._list_paginated("prompts/list", "prompts")

    async def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]:
        result = await self.request("prompts/get", {"name": name, "arguments": dict(arguments)})
        if not isinstance(result, Mapping):
            raise MCPProtocolError("prompts/get returned a non-object result")
        return result

    async def _list_paginated(self, method: str, result_key: str) -> tuple[dict[str, Any], ...]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_LIST_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = await self.request(method, params)
            if not isinstance(result, Mapping) or not isinstance(result.get(result_key), list):
                raise MCPProtocolError(f"{method} returned an invalid result")
            items.extend(item for item in result[result_key] if isinstance(item, dict))
            if len(items) > MAX_REMOTE_TOOLS:
                raise MCPProtocolError(f"{method} exposes more than 1024 entries")
            value = result.get("nextCursor")
            cursor = value if isinstance(value, str) and value else None
            if cursor is None:
                return tuple(items)
            if cursor in seen_cursors:
                raise MCPProtocolError(f"{method} repeated a pagination cursor")
            seen_cursors.add(cursor)
        raise MCPProtocolError(f"{method} exceeded 100 pages")

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        result = await self.request("tools/call", {"name": name, "arguments": dict(arguments)})
        if not isinstance(result, Mapping):
            raise MCPProtocolError("tools/call returned a non-object result")
        return result

    async def request(self, method: str, params: Mapping[str, Any]) -> Any:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)}
        response = await asyncio.wait_for(
            self._exchange(message), timeout=self.settings.timeout_seconds
        )
        if response.get("id") != message["id"]:
            raise MCPProtocolError("MCP response id does not match request")
        if "error" in response:
            raise MCPProtocolError(_error_text(response["error"]))
        if "result" not in response:
            raise MCPProtocolError("MCP response is missing result")
        return response["result"]

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._send_notification({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    @abstractmethod
    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def _send_notification(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class StdioMCPClient(MCPClient):
    def __init__(self, settings: MCPServerSettings) -> None:
        super().__init__(settings)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        assert self.settings.command is not None
        env = dict(os.environ)
        env.update(self.settings.env)
        self._process = await asyncio.create_subprocess_exec(
            self.settings.command,
            *self.settings.args,
            cwd=self.settings.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STDIO_BUFFER_LIMIT,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"mcp-{self.settings.name}-stdout"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"mcp-{self.settings.name}-stderr"
        )
        await self.initialize()

    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise MCPProtocolError("MCP stdio server is not running")
        future = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        try:
            await self._write(message)
            return await future
        finally:
            self._pending.pop(message["id"], None)

    async def _send_notification(self, message: dict[str, Any]) -> None:
        await self._write(message)

    async def _write(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        failure = "MCP stdio server closed its output"
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict) or not isinstance(message.get("id"), int):
                    continue
                future = self._pending.get(message["id"])
                if future is not None and not future.done():
                    future.set_result(message)
        except Exception as exc:  # convert reader failure into pending request failures
            failure = f"MCP stdio reader failed: {type(exc).__name__}: {exc}"
        finally:
            error = MCPProtocolError(failure)
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while await self._process.stderr.read(8192):
            pass

    async def aclose(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


class HttpMCPClient(MCPClient):
    def __init__(self, settings: MCPServerSettings) -> None:
        super().__init__(settings)
        self._client = httpx.AsyncClient(timeout=settings.timeout_seconds)
        self._session_id: str | None = None

    async def start(self) -> None:
        await self.initialize()

    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        response = await self._post(message)
        return _decode_http_response(response, expected_id=message["id"])

    async def _send_notification(self, message: dict[str, Any]) -> None:
        await self._post(message)

    async def _post(self, message: dict[str, Any]) -> httpx.Response:
        assert self.settings.url is not None
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **dict(self.settings.headers),
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._protocol_version
        response = await self._client.post(self.settings.url, headers=headers, json=message)
        response.raise_for_status()
        if len(response.content) > MAX_HTTP_RESPONSE_BYTES:
            raise MCPProtocolError("MCP HTTP response exceeds 16 MiB")
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        return response

    async def aclose(self) -> None:
        if self._session_id and self.settings.url:
            with suppress(httpx.HTTPError):
                await self._client.delete(
                    self.settings.url,
                    headers={
                        **dict(self.settings.headers),
                        "Mcp-Session-Id": self._session_id,
                        "MCP-Protocol-Version": self._protocol_version,
                    },
                )
        await self._client.aclose()


def create_client(settings: MCPServerSettings) -> MCPClient:
    return StdioMCPClient(settings) if settings.transport == "stdio" else HttpMCPClient(settings)


def _decode_http_response(
    response: httpx.Response, *, expected_id: int | None = None
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        value = response.json()
        if not isinstance(value, dict):
            raise MCPProtocolError("MCP HTTP response is not a JSON object")
        return value
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
        )
        if not data:
            continue
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and (expected_id is None or value.get("id") == expected_id):
            return value
    raise MCPProtocolError("MCP event stream contained no JSON-RPC response")


def _error_text(value: object) -> str:
    if isinstance(value, Mapping):
        code = value.get("code")
        message = value.get("message", "unknown MCP error")
        return f"MCP error {code}: {message}"
    return f"MCP error: {value}"
