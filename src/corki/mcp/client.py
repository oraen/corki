"""Small MCP 2025-03-26 JSON-RPC clients for stdio and Streamable HTTP."""

from __future__ import annotations

import asyncio
import os
import weakref
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from corki.config import MCPServerSettings
from corki.config.mcp_headers import RUST_WHITESPACE
from corki.config.mcp_url import http_url
from corki.mcp.active_time import ActiveTime
from corki.mcp.catalog_policy import effective_catalog_limit
from corki.mcp.content_blocks import content_block
from corki.mcp.elicitation import ElicitationRouter
from corki.mcp.environment import build_stdio_environment
from corki.mcp.header_helper import HeaderHelper
from corki.mcp.http_headers import build_http_headers, request_headers
from corki.mcp.http_recovery import BorrowedHttpTransport, HttpRecovery
from corki.mcp.http_redirect import MCPHttpSession, MCPRedirectPolicy, SendRequest
from corki.mcp.http_response import buffered_request, permits_error_body
from corki.mcp.http_routing import HttpResponseRouter
from corki.mcp.http_stream import owned_response
from corki.mcp.inbound import InboundService
from corki.mcp.initialization import handshake_message, validate_initialization
from corki.mcp.json_rpc import MCPProtocolError, check_known_fields, integer_response_id, plain_json
from corki.mcp.json_rpc import decode_json as _decode_json
from corki.mcp.json_values import json_value, metadata_map
from corki.mcp.oauth_discovery import discover_oauth
from corki.mcp.oauth_file import FileOAuthAuthority
from corki.mcp.oauth_refresh import refresh_oauth
from corki.mcp.oauth_store import OAuthCredentialAuthority
from corki.mcp.process_group import MCPProcessGroup
from corki.mcp.request_policy import effective_timeout, request_timeout
from corki.mcp.result_candidates import listed_tools, preceding_tool_result, resource_result
from corki.mcp.runtime_environment import (
    MCPHTTPEnvironment,
    MCPRuntimeContext,
    require_local_environment,
)
from corki.mcp.sse import SseParser, response_from_event
from corki.mcp.sse_resume import read_sse_response
from corki.mcp.wire_types import struct_object
from corki.protocol.wire_json import WireObject
from corki.protocol.wire_numbers import dumps_wire

# Keep Corki's stable MCP lifecycle on the same legacy proposal as Codex.
# The server may negotiate an older supported value during initialize.
PROTOCOL_VERSION = "2025-06-18"
CLIENT_CAPABILITIES = {"elicitation": {"form": {}, "url": {}}}
MAX_LIST_PAGES = 100
MAX_REMOTE_TOOLS = 1_024
MAX_TOOL_CATALOG_ITEMS = 2_048
MAX_HTTP_RESPONSE_BYTES = 16 * 1_024 * 1_024
STDIO_BUFFER_LIMIT = 4 * 1_024 * 1_024


def validate_tool_result(result: object) -> Mapping[str, Any]:
    """Check remote envelope fields before entering model/public projections."""
    if isinstance(result, (WireObject, list)) and preceding_tool_result(result) is not None:
        raise MCPProtocolError("MCP tools/call returned an unexpected response type")
    result = struct_object(
        result, ("resultType", "content", "structuredContent", "isError", "_meta")
    )
    if not isinstance(result, Mapping):
        raise MCPProtocolError("tools/call returned a non-object result")
    check_known_fields(result, {"resultType", "content", "structuredContent", "isError", "_meta"})
    result_type = result.get("resultType")
    if result_type is not None and (not isinstance(result_type, str) or result_type != "complete"):
        raise MCPProtocolError("MCP tools/call did not return a complete result")
    if not any(
        result.get(key) is not None for key in ("content", "structuredContent", "isError", "_meta")
    ):
        raise MCPProtocolError("MCP tools/call returned no known result fields")
    content = result.get("content")
    if content is not None and not isinstance(content, list):
        raise MCPProtocolError("MCP result.content must be an array")
    if result.get("isError") is not None and type(result["isError"]) is not bool:
        raise MCPProtocolError("MCP result.isError must be a boolean")
    if result.get("_meta") is not None and not isinstance(result["_meta"], Mapping):
        raise MCPProtocolError("MCP result._meta must be an object")
    # RMCP's Option<Vec<ContentBlock>> defaults missing/null content to [] only
    # after another known field establishes this is a CallToolResult, not an ack.
    projected = {**result, "content": [content_block(block) for block in content or []]}
    if result.get("structuredContent") is not None:
        projected["structuredContent"] = json_value(result["structuredContent"])
    if result.get("_meta") is not None:
        projected["_meta"] = metadata_map(result["_meta"])
    return plain_json(projected)


def _matches_id(value: object, expected: int) -> bool:
    return integer_response_id(value) == expected


class MCPClient(ABC):
    def __init__(self, settings: MCPServerSettings) -> None:
        self.settings = settings
        self._next_id = 0
        self._next_progress_token = 0
        self._initialized = False
        self._protocol_version = PROTOCOL_VERSION
        self.server_instructions: str | None = None
        self.server_info: dict | None = None
        self._tool_catalog_cacheable = True
        self.elicitations = ElicitationRouter()
        self.active_time = ActiveTime()

    @property
    def tool_catalog_cacheable(self) -> bool:
        return self._tool_catalog_cacheable

    @property
    def is_closed(self) -> bool:
        """Custom clients may report terminal transport failure for reconciliation."""
        return False

    def set_elicitation_router(self, router: ElicitationRouter) -> None:
        """Bind host authority before starting the service."""
        self.elicitations = router

    async def initialize(self) -> None:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": deepcopy(CLIENT_CAPABILITIES),
                "clientInfo": {"name": "corki", "version": "0.1.0"},
            },
        )
        initialized = validate_initialization(result)
        self._protocol_version = initialized.protocol_version
        self.server_instructions = initialized.instructions
        self.server_info = initialized.server_info
        self._tool_catalog_cacheable = initialized.tool_catalog_cacheable
        self._initialized = True
        await self.notify("notifications/initialized", {})

    @abstractmethod
    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[dict[str, Any], ...]:
        """Collect a legacy catalog within the exact host startup capacity."""
        result = await self.request("tools/list", {})
        try:
            tools = listed_tools(result)
        except MCPProtocolError:
            raise MCPProtocolError("Invalid MCP tools/list result") from None
        limit = effective_catalog_limit(self)
        if len(tools) > limit:
            raise MCPProtocolError(f"tools/list exceeded the catalog limit of {limit} items")
        # Codex deliberately ignores legacy nextCursor. A returned version string
        # is not a modern discovery handshake or host-owned Apps provenance.
        return tools

    async def list_resources(self, cursor: str | None = None) -> dict[str, Any]:
        """Read one resource page; aggregate collection belongs to the caller."""
        return await self._resource_request(
            "resources/list", "resources", {} if cursor is None else {"cursor": cursor}
        )

    async def list_resource_templates(self, cursor: str | None = None) -> dict[str, Any]:
        """Read one template page, retaining even an empty opaque cursor."""
        return await self._resource_request(
            "resources/templates/list", "templates", {} if cursor is None else {"cursor": cursor}
        )

    async def read_resource(self, uri: str) -> Mapping[str, Any]:
        return await self._resource_request("resources/read", "read", {"uri": uri})

    async def _resource_request(self, method: str, kind: str, params: dict) -> dict:
        result = await self.request(method, params)
        try:
            return resource_result(result, kind)
        except MCPProtocolError:
            raise MCPProtocolError(f"Invalid MCP {method} result") from None

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

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None) -> Mapping[str, Any]:
        params = {"name": name}
        if arguments is not None:
            params["arguments"] = dict(arguments)
        result = await self.request("tools/call", params)
        return validate_tool_result(result)

    async def request(self, method: str, params: Mapping[str, Any]) -> Any:
        parameters = deepcopy(dict(params))
        if method != "initialize":
            metadata = parameters.get("_meta")
            if metadata is not None and not isinstance(metadata, Mapping):
                raise MCPProtocolError("MCP request _meta must be an object or null")
            token = self._next_progress_token
            self._next_progress_token = (token + 1) % (1 << 64)
            parameters["_meta"] = {
                **(metadata or {}),
                "progressToken": token if token < 1 << 63 else token - (1 << 64),
            }
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": parameters}
        async with self.active_time.timeout(effective_timeout(self, self.settings.timeout_seconds)):
            response = await self._exchange(message)
        if method == "initialize":
            response = handshake_message(response)
            if response is None:
                raise MCPProtocolError("Expected an initialize response")
            if "error" in response and response.get("id") is None:
                raise MCPProtocolError(_error_text(response["error"]))
        if not _matches_id(response.get("id"), message["id"]):
            raise MCPProtocolError("MCP response id does not match request")
        if "error" in response:
            raise MCPProtocolError(_error_text(response["error"]))
        if "result" not in response:
            raise MCPProtocolError("MCP response is missing result")
        # Typed consumers must see every raw field occurrence. Initialization
        # cannot erase duplicates before validating or reading cache policy.
        return (
            response["result"]
            if method
            in (
                "initialize",
                "tools/call",
                "tools/list",
                "resources/list",
                "resources/templates/list",
                "resources/read",
            )
            else plain_json(response["result"])
        )

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._send_notification({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    @abstractmethod
    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def _send_notification(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class StdioMCPClient(MCPClient):
    @property
    def is_closed(self) -> bool:
        return (
            self._close_task is not None
            or self._process is None
            or self._process.returncode is not None
            or self._reader_task is None
            or self._reader_task.done()
            or self._inbound is not None
            and self._inbound.is_closed
        )

    def __init__(self, settings: MCPServerSettings) -> None:
        require_local_environment(settings)
        super().__init__(settings)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._initializing: list[asyncio.Future] = []
        self._inbound: InboundService | None = None
        self._write_lock = asyncio.Lock()
        self._spawn_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._process_group: MCPProcessGroup | None = None
        self._group_finalizer: weakref.finalize | None = None

    async def start(self) -> None:
        if self._spawn_task is not None or self._close_task is not None:
            raise RuntimeError("MCP stdio client already started or closed")
        assert self.settings.command is not None
        env = build_stdio_environment(self.settings)
        self._spawn_task = asyncio.create_task(self._spawn(env), name="mcp-stdio-spawn")
        try:
            await asyncio.shield(self._spawn_task)
            if self._close_task is not None:
                raise asyncio.CancelledError
            await self.initialize()
        except BaseException:
            await self.aclose()
            raise

    async def _spawn(self, env: dict[str, str]) -> None:
        self._process = await asyncio.create_subprocess_exec(
            self.settings.command,
            *self.settings.args,
            cwd=self.settings.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STDIO_BUFFER_LIMIT,
            **({"process_group": 0} if os.name == "posix" else {}),
        )
        if os.name == "posix":
            self._process_group = MCPProcessGroup(self._process.pid)
            self._group_finalizer = weakref.finalize(self, self._process_group.terminate)
        # Readers own pipes and pending replies, not a reference that keeps the client alive.
        assert self._process.stdout is not None and self._process.stderr is not None
        self._inbound = InboundService(
            self._pending,
            partial(_write_stdio, self._process.stdin, self._write_lock),
            server_name=self.settings.name,
            elicitations=self.elicitations,
            active_time=self.active_time,
        )
        self._reader_task = asyncio.create_task(
            _read_stdio(
                self._process.stdout,
                self._pending,
                self._initializing,
                partial(_invalid_stdio_request, self._process.stdin, self._write_lock),
                self._inbound,
                partial(_finish_stdio_eof, self._process),
            ),
            name=f"mcp-{self.settings.name}-stdout",
        )
        self._stderr_task = asyncio.create_task(
            _drain_stdio(self._process.stderr), name=f"mcp-{self.settings.name}-stderr"
        )

    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None:
            raise MCPProtocolError("MCP stdio server is not running")
        future = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        if message["method"] == "initialize":
            self._initializing.append(future)
        try:
            await self._write(message)
            return await future
        finally:
            self._pending.pop(message["id"], None)
            if future in self._initializing:
                self._initializing.remove(future)

    async def _send_notification(self, message: dict[str, Any]) -> None:
        await self._write(message)

    async def _write(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        data = dumps_wire(message).encode("utf-8") + b"\n"
        async with self._write_lock:
            if self._inbound is not None and self._inbound.is_closed:
                raise MCPProtocolError("MCP stdio transport is closed")
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        await _read_stdio(
            self._process.stdout,
            self._pending,
            self._initializing,
            partial(_invalid_stdio_request, self._process.stdin, self._write_lock),
            self._inbound,
            partial(_finish_stdio_eof, self._process),
        )

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mcp-stdio-close")
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                # Handle the retained terminal error below, after cancellation priority.
                break
        if cancelled:
            # Consume a terminal cleanup failure without replacing cancellation control flow.
            # The same owned task retains that failure for a subsequent explicit close.
            if not self._close_task.cancelled():
                self._close_task.exception()
            raise asyncio.CancelledError
        self._close_task.result()

    async def _close(self) -> None:
        if self._spawn_task is not None:
            with suppress(Exception):
                await self._spawn_task
        process = self._process
        try:
            if self._inbound is not None:
                await self._inbound.aclose()
            if process is not None:
                if self._group_finalizer is not None:
                    self._group_finalizer()
                elif process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.terminate()
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    # Inherited pipes must not prevent reaping the direct child.
                    process._transport.close()
                    await process.wait()
        finally:
            tasks = [t for t in (self._reader_task, self._stderr_task) if t is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None:
                # asyncio exposes no public Process.close for the owned pipe transport.
                process._transport.close()
            self._process = None


async def _read_stdio(
    stdout: asyncio.StreamReader,
    pending: dict[int, asyncio.Future],
    initializing: list[asyncio.Future] | None = None,
    invalid_request: Callable[[], Awaitable[None]] | None = None,
    inbound: InboundService | None = None,
    close_transport: Callable[[], Awaitable[None]] | None = None,
) -> None:
    failure = "MCP stdio server closed its output"
    natural_eof = False
    try:
        while line := await stdout.readline():
            try:
                message = _decode_json(line.removeprefix(b"\xef\xbb\xbf"))
            except MCPProtocolError:
                # Local legacy AsyncRwTransport skips syntactically invalid JSON.
                continue
            if initializing:
                try:
                    message = handshake_message(message)
                except MCPProtocolError:
                    # Typed framing errors receive Invalid Request; they are not
                    # the awaited handshake result, so keep reading afterward.
                    if invalid_request is not None:
                        await invalid_request()
                    continue
                if message is not None and not initializing[0].done():
                    initializing[0].set_result(message)
                continue
            if isinstance(message, dict) and inbound is not None:
                try:
                    if inbound.receive(message):
                        continue
                except MCPProtocolError:
                    if invalid_request is not None:
                        await invalid_request()
                    continue
            if not isinstance(message, dict) or "method" in message:
                continue
            identity = integer_response_id(message.get("id"))
            future = pending.get(identity)
            if future is not None and not future.done():
                future.set_result(message)
        natural_eof = True
    except Exception as exc:
        # Legacy AsyncRwTransport turns read I/O errors into receive EOF.
        natural_eof = isinstance(exc, OSError)
        failure = f"MCP stdio reader failed: {type(exc).__name__}: {exc}"
    finally:
        try:
            if inbound is not None:
                await inbound.aclose(grace_seconds=5.0 if natural_eof else 0.0)
        finally:
            try:
                if natural_eof and close_transport is not None:
                    await close_transport()
            finally:
                error = MCPProtocolError(failure)
                for future in tuple(pending.values()):
                    if not future.done():
                        future.set_exception(error)


async def _finish_stdio_eof(process: asyncio.subprocess.Process) -> None:
    """Close the drained write half and reap without retaining the client owner."""

    async def finish() -> None:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            # A descendant retaining stderr must not prevent direct-child reaping.
            process._transport.close()
            await process.wait()
        finally:
            process._transport.close()

    owner = asyncio.create_task(finish(), name="mcp-stdio-eof-close")
    cancelled = False
    while not owner.done():
        try:
            await asyncio.shield(owner)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not owner.cancelled():
            owner.exception()
        raise asyncio.CancelledError
    owner.result()


async def _drain_stdio(stderr: asyncio.StreamReader) -> None:
    while await stderr.read(8192):
        pass


async def _write_stdio(stdin, lock: asyncio.Lock, message: dict[str, Any]) -> None:
    data = dumps_wire(message).encode("utf-8") + b"\n"
    async with lock:
        stdin.write(data)
        await stdin.drain()


async def _invalid_stdio_request(stdin: asyncio.StreamWriter, lock: asyncio.Lock) -> None:
    async with lock:
        stdin.write(b'{"jsonrpc":"2.0","error":{"code":-32600,"message":"Invalid request"}}\n')
        await stdin.drain()


class HttpMCPClient(MCPClient):
    @property
    def tool_catalog_cacheable(self) -> bool:
        return self._recovery.current._tool_catalog_cacheable

    @property
    def is_closed(self) -> bool:
        return self._recovery.closed or self._recovery.current._client.is_closed

    def __init__(
        self,
        settings: MCPServerSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        environment: MCPHTTPEnvironment | None = None,
        agent_plugin: bool = False,
    ) -> None:
        if type(agent_plugin) is not bool:
            raise ValueError("MCP agent plugin attribution must be a host boolean")
        self._request_url = (
            http_url(settings.url) if agent_plugin and settings.url is not None else settings.url
        )
        if environment is None:
            require_local_environment(settings)
        else:
            if not isinstance(environment, MCPHTTPEnvironment):
                raise ValueError("MCP HTTP environment must be a host-owned binding")
            environment.validate(settings)
            if transport is not None:
                raise ValueError("a bound HTTP environment owns transport selection")
            transport = environment.transport
        super().__init__(settings)
        self._environment = environment
        self._agent_plugin = agent_plugin
        self._executor_bearer_env: str | None = None
        self._oauth_file: FileOAuthAuthority | OAuthCredentialAuthority | None = None
        self._oauth_token = None
        self._oauth_prepared = False
        self._transport = transport
        self._client = MCPHttpSession(
            redirect_policy=self._http_redirect_policy,
            send_hop=self._send_http_hop,
            timeout=settings.timeout_seconds,
            transport=BorrowedHttpTransport(transport) if transport is not None else None,
            event_hooks={"request": [self._executor_request_headers]},
        )
        self._session_id: str | None = None
        self._headers: httpx.Headers | None = None
        self._bearer: str | None = None
        self._helper: HeaderHelper | None = None
        self._http_close_task: asyncio.Task | None = None
        self._helper_cwd: Path | None = None
        self._executor_initialize_deadline: float | None = None
        pending: dict[int, asyncio.Future] = {}
        self._inbound = InboundService(
            pending,
            self._send_reply,
            server_name=settings.name,
            elicitations=self.elicitations,
            active_time=self.active_time,
        )
        self._responses = HttpResponseRouter(pending, self._inbound.receive)
        self._recovery = HttpRecovery(
            self,
            self._fresh_session,
            HttpMCPClient._initialize_session,
            MCPClient.request,
            HttpMCPClient._close_transport,
            settings.timeout_seconds,
            transport.aclose if transport is not None and environment is None else None,
            prepare_auth=HttpMCPClient._prepare_oauth,
        )

    async def start(self) -> None:
        if self._environment is not None:
            self._executor_bearer_env = await self._environment.executor_bearer(self.settings)
        self._ensure_headers()
        await self._recovery.start()
        self.server_instructions = self._recovery.current.server_instructions
        self.server_info = deepcopy(self._recovery.current.server_info)

    async def _executor_request_headers(self, request: httpx.Request) -> None:
        if self._executor_bearer_env is not None:
            request.extensions["corki_executor_bearer_env_var"] = self._executor_bearer_env

    def _http_redirect_policy(self) -> MCPRedirectPolicy:
        configured = bool(
            (
                self.settings.http_headers
                if self.settings.http_headers is not None
                else self.settings.headers
            )
            or self.settings.env_http_headers
            or self._executor_bearer_env
        )
        return MCPRedirectPolicy(self._agent_plugin, configured)

    async def _send_http_hop(self, request: httpx.Request, send: SendRequest) -> httpx.Response:
        if self._helper is None:
            return await send(request)
        body = await request.aread()

        async def with_headers(values: httpx.Headers, seconds: float) -> httpx.Response:
            # Helper-generated credentials are local to this hop. They must not
            # become explicit headers that suppress later helper refreshes.
            candidate = httpx.Request(
                request.method,
                request.url,
                headers=values,
                content=body,
                extensions=dict(request.extensions),
            )
            return await send(candidate)

        return await self._helper.request(
            request.method,
            str(request.url),
            request.headers,
            effective_timeout(self, self.settings.timeout_seconds),
            with_headers,
            active_time=self.active_time,
        )

    async def _initialize_session(self) -> None:
        self._executor_initialize_deadline = asyncio.get_running_loop().time() + effective_timeout(
            self, self.settings.timeout_seconds
        )
        try:
            await MCPClient.initialize(self)
            if self._session_id is not None:
                self._responses.start_receiver(self._receive_common_stream)
        finally:
            self._executor_initialize_deadline = None

    def set_oauth_file_home(self, home: Path) -> None:
        """Host composition only; a logical client's source cannot change after startup."""
        if self._oauth_file is not None or self._initialized:
            raise MCPProtocolError("OAuth credential authority already selected")
        self._oauth_file = FileOAuthAuthority(home)

    def set_oauth_store(self, home: Path, mode: str) -> None:
        if self._oauth_file is not None or self._initialized:
            raise MCPProtocolError("OAuth credential authority already selected")
        self._oauth_file = OAuthCredentialAuthority(home, mode)

    async def _prepare_oauth(self) -> None:
        self._ensure_headers()
        if self._oauth_prepared:
            await self._refresh_oauth()
            return
        if (
            self._oauth_file is None
            or self.settings.bearer_token_env_var is not None
            or "authorization" in self._headers
            or self._helper is not None
        ):
            return
        # Initial transport construction/retry reads the pinned source. Session-only
        # 404 reinitialization reuses the live AuthClient state, as RMCP does.
        self._bearer = None
        self._oauth_token = await self._oauth_file.load(self.settings.name, self.settings.url)
        if self._oauth_token is None:
            self._oauth_prepared = True
            return
        context = MCPRuntimeContext((self._environment,) if self._environment else ())
        discovery = await discover_oauth(self.settings, context, local_timeout=30.0)
        current_issuer = discovery.metadata.get("issuer") if discovery is not None else None
        token = self._oauth_token
        if (
            token.refresh_token is not None
            and token.refresh_token.strip(RUST_WHITESPACE)
            and (
                not token.issuer
                or not token.issuer.strip(RUST_WHITESPACE)
                or not current_issuer
                or not current_issuer.strip(RUST_WHITESPACE)
                or current_issuer != token.issuer
            )
        ):
            if not token.usable():
                raise MCPProtocolError(
                    "OAuth authorization required: refresh issuer binding failed"
                )
            # The fallback affects this generation only, never the saved credentials.
            self._oauth_token = replace(token, refresh_token=None, issuer=None)
        if not self._oauth_token.usable():
            await self._refresh_oauth()
        self._bearer = self._oauth_token.access_token
        self._oauth_prepared = True

    async def _refresh_oauth(self) -> None:
        if self._oauth_token is None or self._oauth_token.usable():
            return
        if self._oauth_file is None:
            raise MCPProtocolError("OAuth credential authority is unavailable")
        context = MCPRuntimeContext((self._environment,) if self._environment else ())
        token = await refresh_oauth(self._oauth_file, self._oauth_token, self.settings, context)
        self._oauth_token = token
        self._bearer = token.access_token

    async def _receive_common_stream(self) -> None:
        assert self.settings.url is not None
        headers = self._request_headers(post=False)
        headers["accept"] = "text/event-stream, application/json"
        request = self._client.build_request(
            "GET", self._request_url, headers=headers, timeout=None
        )
        response = await self._client.send(request, stream=True, follow_redirects=False)
        if not response.is_success or not response.headers.get(
            "content-type", ""
        ).lower().startswith(("text/event-stream", "application/json")):
            async with owned_response(response):
                if response.status_code == 405:
                    return
                raise MCPProtocolError("MCP common stream rejected HTTP status or content type")
        await read_sse_response(
            self._client,
            response,
            expected_id=None,
            maximum_bytes=MAX_HTTP_RESPONSE_BYTES,
            timeout=None,
            resumable=True,
            receive=self._responses.deliver,
            inbound=self._inbound.receive,
        )

    def _fresh_session(self) -> HttpMCPClient:
        # Keep the resolved static recipe; only per-generation helper state is fresh.
        self._ensure_headers()
        session = HttpMCPClient(
            self.settings,
            transport=self._transport if self._environment is None else None,
            environment=self._environment,
            agent_plugin=self._agent_plugin,
        )
        session.set_elicitation_router(self.elicitations)
        # Codex's pause state belongs to the logical client, not its HTTP generation.
        session.active_time = self.active_time
        session._inbound._active_time = self.active_time
        assert self._headers is not None
        session._headers = self._headers.copy()
        session._bearer = self._bearer
        session._executor_bearer_env = self._executor_bearer_env
        session._oauth_file = self._oauth_file
        if self._recovery.ready:
            current = self._recovery.current
            session._oauth_token = current._oauth_token
            session._oauth_prepared = current._oauth_prepared
            session._bearer = current._bearer
        session._helper_cwd = self._helper_cwd
        if self.settings.http_headers_helper is not None:
            assert self.settings.url is not None and self._helper_cwd is not None
            session._helper = HeaderHelper(
                self.settings.url, self.settings.http_headers_helper, self._helper_cwd
            )
        return session

    def set_elicitation_router(self, router: ElicitationRouter) -> None:
        self.elicitations = router
        self._inbound._elicitations = router

    async def request(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "initialize" or not self._recovery.ready:
            if self._environment is not None:
                self._executor_bearer_env = await self._environment.executor_bearer(self.settings)
            with self._recovery.operation():
                return await MCPClient.request(self, method, params)
        return await self._recovery.request(
            method, params, effective_timeout(self, self.settings.timeout_seconds)
        )

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None) -> Mapping[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except httpx.HTTPStatusError as error:
            challenges = error.response.headers.get_list("www-authenticate")
            if error.response.status_code != 401 or not challenges:
                raise
            # Transport/header-helper refresh already ran. Report the challenge
            # to the host without replaying this rejected operation.
            return {
                "content": [{"type": "text", "text": "Authentication required"}],
                "isError": True,
                "_meta": {"mcp/www_authenticate": [", ".join(challenges)]},
            }

    def _ensure_headers(self) -> None:
        if self._headers is None:
            headers, bearer = build_http_headers(
                replace(self.settings, bearer_token_env_var=None)
                if self._executor_bearer_env is not None
                else self.settings
            )
            helper = None
            if self.settings.http_headers_helper is not None:
                assert self.settings.url is not None
                self._helper_cwd = self.settings.cwd or Path.cwd()
                helper = HeaderHelper(
                    self.settings.url,
                    self.settings.http_headers_helper,
                    self._helper_cwd,
                )
            self._headers, self._bearer, self._helper = headers, bearer, helper

    def _request_headers(self, *, post: bool) -> httpx.Headers:
        self._ensure_headers()
        if self._oauth_token is not None and not self._oauth_token.usable():
            raise MCPProtocolError("OAuth authorization required: access token is expired")
        assert self._headers is not None
        return request_headers(
            self._headers,
            self._bearer,
            post=post,
            protocol_version=self._protocol_version if self._initialized else None,
            session=self._session_id,
        )

    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        if message["method"] == "initialize":
            response = await self._post(message)
            return _decode_http_response(response, expected_id=message["id"])

        async def send() -> tuple[dict[str, Any] | None, bool]:
            response = await self._post(message)
            if response.status_code in (202, 204):
                return None, False
            value = _decode_http_response(response)
            identity = value.get("id")
            if "method" not in value and not (
                isinstance(identity, str)
                or type(identity) is int
                and -(1 << 63) <= identity < 1 << 63
                or identity is None
                and "error" in value
            ):
                raise MCPProtocolError("Invalid MCP response id")
            if "method" not in value and "result" not in value and "error" not in value:
                raise MCPProtocolError("MCP response is missing result")
            return value, "corki_mcp_sse_response" in response.extensions

        return await self._responses.exchange(message["id"], send)

    async def _send_notification(self, message: dict[str, Any]) -> None:
        with (
            self._recovery.operation(),
            self._recovery.lease(self._recovery.current) as session,
            request_timeout(session, effective_timeout(self, self.settings.timeout_seconds)),
        ):
            response = await session._post(message)
            if message["method"] != "notifications/initialized" and response.status_code not in (
                202,
                204,
            ):
                session._responses.deliver(_decode_http_response(response))

    async def _post(self, message: dict[str, Any]) -> httpx.Response:
        assert self.settings.url is not None
        await self._refresh_oauth()
        headers = self._request_headers(post=True)
        has_session = self._session_id is not None
        try:
            response = await self._http_request("POST", headers, message)
        except httpx.LocalProtocolError:
            # HTTP libraries may include rejected authorization values in their errors.
            raise MCPProtocolError("MCP HTTP transport rejected request framing") from None
        if not response.is_success and not permits_error_body(
            response, method=message.get("method", ""), has_session=has_session
        ):
            response.raise_for_status()
        if response.status_code in (202, 204):
            return response
        if message.get("method") == "notifications/initialized":
            handshake_message(_decode_json(response.content))
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        return response

    async def _send_reply(self, message: dict[str, Any]) -> None:
        # Reverse RPC responses stay with this generation and never enter recovery.
        response = await self._post(message)
        if response.status_code not in (202, 204):
            self._responses.deliver(_decode_http_response(response))

    async def aclose(self) -> None:
        await self._recovery.aclose()

    async def _close_transport(self) -> None:
        if self._http_close_task is None:
            self._http_close_task = asyncio.create_task(self._close_http(), name="mcp-http-close")
        cancelled = False
        while not self._http_close_task.done():
            try:
                await asyncio.shield(self._http_close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not self._http_close_task.cancelled():
                self._http_close_task.exception()
            raise asyncio.CancelledError
        self._http_close_task.result()

    async def _close_http(self) -> None:
        try:
            await self._inbound.aclose()
            await self._responses.aclose()
            if (
                self._session_id
                and self.settings.url
                and not self._client.is_closed
                and (self._oauth_token is None or self._oauth_token.usable())
            ):
                with suppress(httpx.HTTPError):
                    await self._http_request("DELETE", self._request_headers(post=False))
        finally:
            try:
                if self._helper is not None:
                    await self._helper.aclose()
            finally:
                await self._client.aclose()

    async def _http_request(self, method, headers, message=None):
        assert self.settings.url is not None
        timeout = effective_timeout(self, self.settings.timeout_seconds)
        has_session = self._session_id is not None

        async def send(values, seconds):
            executor_timeout = None
            if (
                self._environment is not None
                and self._executor_initialize_deadline is not None
                and message is not None
                and message.get("method") in ("initialize", "notifications/initialized")
            ):
                executor_timeout = max(
                    1,
                    int(
                        (self._executor_initialize_deadline - asyncio.get_running_loop().time())
                        * 1000
                    ),
                )
            return await buffered_request(
                self._client,
                method,
                self._request_url,
                headers=values,
                message=message,
                timeout=None,
                maximum_bytes=MAX_HTTP_RESPONSE_BYTES,
                has_session=has_session,
                shared_responses=message is not None and message.get("method") != "initialize",
                inbound=self._inbound.receive,
                executor_timeout_ms=executor_timeout,
            )

        async with self.active_time.timeout(timeout):
            return await send(headers, timeout)


def create_client(settings: MCPServerSettings) -> MCPClient:
    return StdioMCPClient(settings) if settings.transport == "stdio" else HttpMCPClient(settings)


def _decode_http_response(
    response: httpx.Response, *, expected_id: int | None = None
) -> dict[str, Any]:
    if "corki_mcp_sse_response" in response.extensions:
        return response.extensions["corki_mcp_sse_response"]
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        value = _decode_json(response.content)
        if not isinstance(value, dict):
            raise MCPProtocolError("MCP HTTP response is not a JSON object")
        return value
    for event in SseParser(MAX_HTTP_RESPONSE_BYTES).feed(response.content):
        value = response_from_event(event, expected_id)
        if value is not None:
            return value
    raise MCPProtocolError("MCP event stream contained no JSON-RPC response")


def _error_text(value: object) -> str:
    if isinstance(value, Mapping):
        code = value.get("code")
        message = value.get("message", "unknown MCP error")
        return f"MCP error {code}: {message}"
    return f"MCP error: {value}"
