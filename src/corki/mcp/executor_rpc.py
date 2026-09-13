"""Explicit host WebSocket connection for the executor's JSON-RPC dialect."""

import asyncio
import json
from collections.abc import Callable, Mapping

import httpx
from websockets.asyncio.client import ClientConnection, connect

from corki.mcp.executor_wire import (
    MAX_RPC_BYTES,
    ExecutorProtocolError,
    decode_packet,
    integer,
    request_id,
)


class ExecutorRpc:
    """One physical RPC connection; disconnect never silently replays pending calls."""

    def __init__(
        self,
        socket: ClientConnection,
        notification: Callable[[str, object], None],
        disconnected: Callable[[str], None],
    ) -> None:
        self._socket = socket
        self._notification, self._disconnected = notification, disconnected
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self.closed = False
        self.session_id: str | None = None
        self.environment_info: dict | None = None
        self._close_task: asyncio.Task | None = None
        self._reader = asyncio.create_task(self._read(), name="executor-rpc-reader")

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        notification: Callable[[str, object], None],
        disconnected: Callable[[str], None],
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
    ) -> "ExecutorRpc":
        """Connect only to the host-selected endpoint, then complete the native handshake."""
        socket = await connect(
            url,
            additional_headers=headers,
            open_timeout=timeout,
            close_timeout=3,
            max_size=MAX_RPC_BYTES,
            max_queue=128,
        )
        rpc = cls(socket, notification, disconnected)
        try:
            async with asyncio.timeout(timeout):
                result = await rpc.call(
                    "initialize", {"clientName": "corki-environment", "resumeSessionId": None}
                )
                if not isinstance(result, dict) or not isinstance(result.get("sessionId"), str):
                    raise ExecutorProtocolError("invalid executor initialization response")
                rpc.session_id = result["sessionId"]
                info = result.get("environmentInfo")
                if info is not None and not isinstance(info, dict):
                    raise ExecutorProtocolError("invalid executor environment information")
                rpc.environment_info = info
                await rpc._send({"method": "initialized", "params": {}})
        except BaseException:
            await rpc.aclose()
            raise
        return rpc

    async def _send(self, packet: dict) -> None:
        encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_RPC_BYTES:
            raise ExecutorProtocolError("outgoing executor RPC exceeds byte limit")
        async with self._write_lock:
            if self.closed:
                raise httpx.ReadError("executor transport disconnected")
            await self._socket.send(encoded)

    async def call(self, method: str, params: dict) -> object:
        """Register before sending; cancellation removes only the exact pending response."""
        self._next_id += 1
        identity = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[identity] = future
        try:
            await self._send({"id": identity, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(identity, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _read(self) -> None:
        reason = "executor transport disconnected"
        try:
            async for raw in self._socket:
                packet = decode_packet(raw)
                if "method" in packet:
                    method = packet["method"]
                    if not isinstance(method, str):
                        raise ExecutorProtocolError("invalid executor RPC method")
                    if "id" in packet:
                        identity = request_id(packet["id"])
                        await self._send(
                            {
                                "id": identity,
                                "error": {
                                    "code": -32601,
                                    "message": "unsupported executor client method",
                                },
                            }
                        )
                    else:
                        self._notification(method, packet.get("params"))
                    continue
                identity = request_id(packet.get("id"))
                if "result" not in packet:
                    error = packet.get("error")
                    if (
                        not isinstance(error, dict)
                        or type(error.get("code")) is not int
                        or not isinstance(error.get("message"), str)
                    ):
                        raise ExecutorProtocolError("invalid executor RPC response")
                    integer(error["code"], -(1 << 63), (1 << 63) - 1)
                future = self._pending.get(identity)
                if future is not None and not future.done():
                    if "result" in packet:
                        future.set_result(packet["result"])
                    else:
                        future.set_exception(
                            ExecutorProtocolError(
                                f"executor RPC error {error['code']}: {error['message'][:1000]}"
                            )
                        )
        except asyncio.CancelledError:
            reason = "executor reader cancelled"
            raise
        except Exception as exc:
            # Don't expose WebSocket URLs/headers or raw untrusted packets in diagnostics.
            reason = f"executor transport failed: {type(exc).__name__}"
        finally:
            self.closed = True
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(httpx.ReadError(reason))
            self._disconnected(reason)
            await self._socket.close()

    async def aclose(self) -> None:
        """Join a single owned close task even when another closer was cancelled."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="executor-rpc-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self.closed = True
        await self._socket.close()
        await self._reader
