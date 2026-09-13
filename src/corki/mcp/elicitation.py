"""Trusted host routing, distinct from model history and server wire request IDs."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from itertools import count
from typing import Any, Literal

from corki.mcp.elicitation_schema import normalize_schema
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.wire_types import metadata
from corki.protocol.wire_json import check_fields


@dataclass(frozen=True, slots=True)
class ElicitationRequest:
    server_name: str
    request_id: str
    params: dict[str, Any]
    kind: Literal[
        "server", "tool_approval", "shell_approval", "patch_approval", "skill_dependency_install"
    ] = "server"


ElicitationHandler = Callable[[ElicitationRequest], Awaitable[None]]
_IDENTITIES = count()
_LOG = logging.getLogger(__name__)


class ElicitationRouter:
    """One host authority per Runtime, shared by current and retiring connections."""

    def __init__(self, handler: ElicitationHandler | None = None) -> None:
        self.handler = handler
        self._pending: dict[tuple[str, str], asyncio.Future] = {}
        self._clear = asyncio.Event()
        self._clear.set()

    async def request(self, server: str, params: dict[str, Any]) -> dict[str, Any]:
        params = deepcopy(params)
        metadata = params.get("_meta") or {}
        metadata.pop("progressToken", None)
        if metadata:
            params["_meta"] = metadata
        else:
            params.pop("_meta", None)
        handler = self.handler
        strict_review = metadata.get("codex_strict_auto_review")
        # Corki has no privileged approval reviewer yet. Do not reinterpret such
        # requests as ordinary user data or infer consent from server-provided hints.
        if (
            handler is None
            or "codex_approval_kind" in metadata
            or "codex_strict_auto_review" in metadata
            and strict_review is not False
        ):
            return {"action": "decline"}
        return await self._deliver(server, params, kind="server")

    async def request_tool_approval(self, server: str, params: dict[str, Any]) -> dict[str, Any]:
        """Deliver a host-constructed approval, never a server wire request."""
        return await self._deliver(server, deepcopy(params), kind="tool_approval")

    async def request_shell_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        """Deliver host execution review on its dedicated router, never an MCP wire request."""
        return await self._deliver("local-shell", deepcopy(params), kind="shell_approval")

    async def request_patch_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        """Dedicated host kind on the existing local execution response channel."""
        return await self._deliver("local-shell", deepcopy(params), kind="patch_approval")

    async def request_skill_dependency_install(self, names: tuple[str, ...]) -> dict[str, Any]:
        """Only host-selected dependencies can request persistent installation."""
        return await self._deliver(
            "skill-dependencies",
            {
                "mode": "form",
                "message": "Install and enable these missing skill MCP dependencies in your "
                "global configuration? " + ", ".join(sorted(names)),
                "requestedSchema": {"type": "object", "properties": {}},
                "_meta": {"codex_approval_kind": "skill_mcp_dependency_install"},
            },
            kind="skill_dependency_install",
        )

    async def _deliver(
        self,
        server: str,
        params: dict[str, Any],
        *,
        kind: Literal[
            "server",
            "tool_approval",
            "shell_approval",
            "patch_approval",
            "skill_dependency_install",
        ],
    ) -> dict[str, Any]:
        handler = self.handler
        if handler is None:
            return {"action": "decline"}
        identity = f"corki-mcp-elicitation-{next(_IDENTITIES)}"
        key = server, identity
        response = asyncio.get_running_loop().create_future()
        self._pending[key] = response
        self._clear.clear()

        async def deliver():
            # Invoke inside the owned task: even a malformed/synchronous host
            # callback must release its pending registration on failure.
            await handler(ElicitationRequest(server, identity, params, kind))

        delivery = asyncio.create_task(deliver(), name="mcp-elicitation-delivery")
        try:
            await asyncio.wait((delivery, response), return_when=asyncio.FIRST_COMPLETED)
            if delivery.done():
                delivery.result()
            return await response
        finally:
            if not delivery.done() and not delivery.cancelling():
                delivery.cancel()
            joined = asyncio.gather(delivery, return_exceptions=True)
            cancelled = False
            try:
                while not joined.done():
                    try:
                        await asyncio.shield(joined)
                    except asyncio.CancelledError:
                        cancelled = True
                for result in joined.result():
                    if isinstance(result, Exception):
                        _LOG.warning("MCP elicitation delivery failed: %s", type(result).__name__)
            finally:
                self._pending.pop(key, None)
                if not self._pending:
                    self._clear.set()
            if cancelled:
                raise asyncio.CancelledError

    def respond(
        self,
        server: str,
        request_id: str,
        action: str,
        *,
        content: Any = None,
        meta: Any = None,
    ) -> None:
        if action not in ("accept", "decline", "cancel"):
            raise ValueError("Invalid MCP elicitation action")
        response = self._pending.get((server, request_id))
        if response is None or response.done():
            raise ValueError("Unknown or completed MCP elicitation request")
        result = {"action": action}
        if action == "accept":
            result["content"] = {} if content is None else deepcopy(content)
        if meta is not None:
            result["_meta"] = deepcopy(meta)
        response.set_result(result)

    async def wait_until_clear(self) -> None:
        while self._pending:
            await self._clear.wait()


def standard_request(params: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize standard request schemas before host interaction."""
    if not isinstance(params, dict) or not isinstance(params.get("message"), str):
        raise ValueError("MCP elicitation requires a message")
    check_fields(params, {"message", "_meta"})
    common = {"message": params["message"]}
    if params.get("_meta") is not None:
        try:
            common["_meta"] = metadata(params["_meta"])
        except MCPProtocolError:
            raise ValueError("Invalid MCP elicitation metadata") from None

    def form():
        check_fields(params, {"requestedSchema"})
        return {
            **common,
            "mode": "form",
            "requestedSchema": normalize_schema(params.get("requestedSchema")),
        }

    try:
        check_fields(params, {"mode"})
        mode = params.get("mode")
        if mode == "form":
            return form()
        if mode == "url":
            check_fields(params, {"url", "elicitationId"})
            if isinstance(params.get("url"), str) and isinstance(params.get("elicitationId"), str):
                return {
                    **common,
                    "mode": "url",
                    "url": params["url"],
                    "elicitationId": params["elicitationId"],
                }
    except ValueError:
        pass
    # RMCP's untagged LegacyForm follows the tagged attempt, including when a
    # present/duplicate/unknown mode or malformed URL makes that attempt fail.
    return form()
