"""Default server-to-client RPC handling, separate from outbound response IDs."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from corki.mcp.active_time import ActiveTime
from corki.mcp.elicitation import ElicitationRouter, standard_request
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.json_values import json_value
from corki.protocol.wire_numbers import dumps_wire

logger = logging.getLogger(__name__)


class MCPRemoteCancelled(MCPProtocolError):
    """A remote RPC failure, never Python's local cancellation control flow."""


def request_id(value: object) -> bool:
    """Request IDs retain their exact wire type, unlike response numeric fallback."""
    return isinstance(value, str) or type(value) is int and -(1 << 63) <= value < 1 << 63


class InboundService:
    """Own default reply sends without keeping an outbound reader blocked on writes."""

    def __init__(
        self,
        pending: dict[int, asyncio.Future],
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        server_name: str = "",
        elicitations: ElicitationRouter | None = None,
        active_time: ActiveTime | None = None,
    ) -> None:
        self._pending = pending
        self._send = send
        self._tasks: set[asyncio.Task[None]] = set()
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._stop_drain = asyncio.Event()
        self._server_name = server_name
        self._elicitations = elicitations or ElicitationRouter()
        self._active_time = active_time or ActiveTime()

    @property
    def is_closed(self) -> bool:
        """New service work is forbidden while previously owned replies drain."""
        return self._closed

    def receive(self, message: dict[str, Any]) -> bool:
        """Consume incoming methods; they cannot complete an identically numbered RPC."""
        if "method" not in message:
            return False
        if self._closed:
            return True
        method, params = message["method"], message.get("params")
        if (
            message.get("jsonrpc") != "2.0"
            or not isinstance(method, str)
            or params is not None
            and not isinstance(params, dict)
            or isinstance(params, dict)
            and params.get("_meta") is not None
            and not isinstance(params["_meta"], dict)
        ):
            raise MCPProtocolError("Invalid MCP inbound message framing")
        identity = message.get("id")
        if request_id(identity):
            if method == "elicitation/create":
                task = asyncio.create_task(
                    self._elicit(identity, params), name="mcp-inbound-elicitation"
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                return True
            result = (
                {"result": {}}
                if method == "ping"
                else {"result": {"roots": []}}
                if method == "roots/list"
                else {"error": {"code": -32601, "message": method}}
            )
            reply = {"jsonrpc": "2.0", "id": identity, **result}
            task = asyncio.create_task(self._reply(reply), name="mcp-inbound-reply")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            self._notification(method, params)
        return True

    async def _elicit(self, identity: int | str, params: dict[str, Any] | None) -> None:
        try:
            request = standard_request(params)
        except ValueError:
            result = {"error": {"code": -32602, "message": "Invalid elicitation parameters"}}
        else:
            try:
                with self._active_time.pause():
                    response = await self._elicitations.request(self._server_name, request)
                result = {"result": response}
            except Exception as error:
                logger.warning("MCP elicitation host failed: %s", type(error).__name__)
                result = {"error": {"code": -32603, "message": "Elicitation host failed"}}
        await self._reply({"jsonrpc": "2.0", "id": identity, **result})

    async def _reply(self, message: dict[str, Any]) -> None:
        try:
            async with self._send_lock:
                await self._send(message)
        except Exception as error:
            # A failed reverse-RPC reply is not permission to replay a user tool.
            logger.warning("MCP inbound reply failed: %s", type(error).__name__)

    def _notification(self, method: str, params: dict[str, Any] | None) -> None:
        if method == "notifications/cancelled" and params is not None:
            identity, reason = params.get("requestId"), params.get("reason")
            if identity is not None and not request_id(identity):
                return
            if reason is not None and not isinstance(reason, str):
                return
            # Python bool/int aliasing is rejected above. Strings never alias integer IDs.
            future = self._pending.get(identity) if identity is not None else None
            if future is not None and not future.done():
                future.set_exception(
                    MCPRemoteCancelled(
                        f"task cancelled for reason {reason if reason is not None else '<unknown>'}"
                    )
                )
            logger.info(
                "MCP server cancelled request (request_id: %r, reason: %r)", identity, reason
            )
        elif method in {
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        }:
            logger.info("MCP server list changed: %s", method)
        elif method == "notifications/resources/updated" and params is not None:
            if isinstance(params.get("uri"), str):
                logger.info("MCP server resource updated: %s", params["uri"])
        elif method == "notifications/progress" and params is not None:
            if (
                request_id(params.get("progressToken"))
                and type(params.get("progress")) in (int, float)
                and (params.get("total") is None or type(params["total"]) in (int, float))
                and (params.get("message") is None or isinstance(params["message"], str))
            ):
                logger.info(
                    "MCP server progress (token: %r, progress: %s, total: %s, message: %r)",
                    params["progressToken"],
                    params["progress"],
                    params.get("total"),
                    params.get("message"),
                )
        elif method == "notifications/message" and params is not None:
            levels = {
                "emergency": logging.ERROR,
                "alert": logging.ERROR,
                "critical": logging.ERROR,
                "error": logging.ERROR,
                "warning": logging.WARNING,
                "notice": logging.INFO,
                "info": logging.INFO,
                "debug": logging.DEBUG,
            }
            level = params.get("level")
            if (
                isinstance(level, str)
                and level in levels
                and "data" in params
                and (params.get("logger") is None or isinstance(params["logger"], str))
            ):
                try:
                    data = dumps_wire(json_value(params["data"]))
                except MCPProtocolError:
                    # Invalid Value data is not a typed logging notification.
                    return
                logger.log(
                    levels[level],
                    "MCP server log (logger: %r): %s",
                    params.get("logger"),
                    data,
                )

    async def aclose(self, *, grace_seconds: float = 0.0) -> None:
        """Drain natural EOF replies; an explicit close can interrupt that grace."""
        self._closed = True
        if grace_seconds <= 0 and not self._stop_drain.is_set():
            self._stop_drain.set()
            for task in tuple(self._tasks):
                if not task.cancelling():
                    task.cancel()
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close(grace_seconds), name="mcp-inbound-close"
            )
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not self._close_task.cancelled():
                self._close_task.exception()
            raise asyncio.CancelledError
        self._close_task.result()

    async def _close(self, grace_seconds: float) -> None:
        tasks = tuple(self._tasks)
        if not tasks:
            return
        joined = asyncio.gather(*tasks, return_exceptions=True)
        interrupt = None
        try:
            if grace_seconds > 0 and not self._stop_drain.is_set():
                interrupt = asyncio.create_task(self._stop_drain.wait())
                await asyncio.wait(
                    (joined, interrupt),
                    timeout=grace_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not joined.done() and not self._stop_drain.is_set():
                    logger.warning("MCP timed out draining in-flight responses")
        finally:
            if interrupt is not None:
                interrupt.cancel()
                await asyncio.gather(interrupt, return_exceptions=True)
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            # The grace budget bounds sending, not ownership of transport cleanup.
            await joined
