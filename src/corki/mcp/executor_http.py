"""Concrete executor http/request transport for host-bound MCP HTTP clients."""

import asyncio
import base64
from collections.abc import Mapping

import httpx

from corki.mcp.executor_body import BodyRouter
from corki.mcp.executor_rpc import ExecutorRpc
from corki.mcp.executor_wire import ExecutorProtocolError, HttpEnvelope, integer


class ExecutorHttpTransport(httpx.AsyncBaseTransport):
    """Own the executor connection; MCP clients only borrow this shared carrier.

    HTTP requests are executed by the selected executor, never sent directly by
    this controller. Host WebSocket connection setup is explicit and asynchronous.
    """

    def __init__(self, rpc: ExecutorRpc, bodies: BodyRouter) -> None:
        self._rpc, self._bodies = rpc, bodies
        self._header_capability: bool | None = None
        self._info_lock = asyncio.Lock()

    @classmethod
    async def connect(
        cls, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 10.0
    ) -> "ExecutorHttpTransport":
        """Connect and initialize one explicit host-owned executor WebSocket endpoint."""
        bodies = BodyRouter()
        rpc = await ExecutorRpc.connect(
            url,
            notification=bodies.receive,
            disconnected=bodies.fail_all,
            headers=headers,
            timeout=timeout,
        )
        return cls(rpc, bodies)

    @property
    def is_closed(self) -> bool:
        return self._rpc.closed

    async def resolves_header_env_vars(self) -> bool:
        """Use initialized metadata or lazily query legacy peers before resolving credentials."""
        async with self._info_lock:
            if self._header_capability is None:
                info = self._rpc.environment_info
                if info is None:
                    info = await self._rpc.call("environment/info", {})
                if not isinstance(info, dict) or not isinstance(info.get("capabilities", {}), dict):
                    raise ExecutorProtocolError("invalid executor environment capabilities")
                supported = info.get("capabilities", {}).get("httpHeaderEnvVars", False)
                if type(supported) is not bool:
                    raise ExecutorProtocolError("invalid executor header environment capability")
                self._rpc.environment_info = info
                self._header_capability = supported
            return self._header_capability

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme not in ("http", "https"):
            raise ExecutorProtocolError("executor HTTP requires an http or https URL")
        data = await request.aread()
        try:
            headers = [
                {"name": name.decode("ascii"), "value": value.decode("utf-8")}
                for name, value in request.headers.raw
            ]
        except UnicodeError as exc:
            raise ExecutorProtocolError("executor request headers must be UTF-8") from exc
        bearer_env = request.extensions.get("corki_executor_bearer_env_var")
        if bearer_env is not None:
            if not isinstance(bearer_env, str) or not await self.resolves_header_env_vars():
                raise ExecutorProtocolError("executor does not support header environment values")
            headers = [header for header in headers if header["name"].lower() != "authorization"]
            headers.append({"name": "authorization", "value": "Bearer ", "valueEnvVar": bearer_env})
        timeout = request.extensions.get("corki_executor_timeout_ms")
        if timeout is not None:
            timeout = integer(timeout, 0, (1 << 64) - 1)
        identity = self._bodies.next_id()
        streaming = request.method != "DELETE"
        body = self._bodies.register(identity) if streaming else None
        try:
            result = await self._rpc.call(
                "http/request",
                {
                    "method": request.method,
                    "url": str(request.url),
                    "headers": headers,
                    "bodyBase64": base64.b64encode(data).decode() if data else None,
                    "timeoutMs": timeout,
                    "redirectPolicy": "stop",
                    "requestId": identity,
                    "streamResponse": streaming,
                },
            )
            response = HttpEnvelope.parse(result)
            if body is not None:
                if response.body:
                    raise ExecutorProtocolError("streamed executor envelope contains buffered body")
                return httpx.Response(
                    response.status, headers=response.headers, stream=body, request=request
                )
            return httpx.Response(
                response.status, headers=response.headers, content=response.body, request=request
            )
        except BaseException:
            if body is not None:
                await body.aclose()
            raise

    async def aclose(self) -> None:
        """The embedding host closes the connection after all MCP borrowers finish."""
        await self._rpc.aclose()
