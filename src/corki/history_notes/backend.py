"""Owned, non-retrying HTTP transport for authenticated native history/notes."""

import asyncio
import json
import math
from typing import Any, Protocol

import httpx


class HistoryNotesError(ValueError):
    """Sanitized operation failure safe to return as a model observation."""


class RecoveryBackend(Protocol):
    """Shared execution port for actual native and local recovery backends."""

    native: bool

    async def call(
        self, action: str, arguments: dict[str, Any], *, session_id: str, budget: int
    ) -> Any:
        """Execute one operation using a trusted service identity and output budget."""
        ...

    async def aclose(self) -> None:
        """Release owned resources after active operations have been joined."""
        ...


class HistoryNotesBackend:
    """Single-attempt transport; borrowed clients remain caller-owned."""

    native = True

    def __init__(
        self, base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base_url, self._api_key = base_url.rstrip("/"), api_key
        self._client, self._owns_client = client, client is None
        self._closed = False

    async def call(
        self, action: str, arguments: dict[str, Any], *, session_id: str, budget: int
    ) -> Any:
        """Overwrite caller identity and bound the complete request, without retries."""
        if self._closed:
            raise HistoryNotesError("Unable to perform operation: backend is closed.")
        if not isinstance(arguments, dict):
            raise HistoryNotesError("History tool arguments must be a JSON object")
        namespace, name = action.split("::")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "x-openai-tool-output-truncation-policy": json.dumps(
                {"mode": "bytes", "limit": budget}
            ),
        }
        if name in ("search_contents", "append_to_file", "write_file"):
            headers["x-openai-encrypted-tool-arguments"] = "true"
        body = {
            **arguments,
            "context": {"session_id": str(session_id), "current_agent_name": "/root"},
        }
        if self._client is None:
            self._client = httpx.AsyncClient()
        try:
            async with (
                asyncio.timeout(35),
                self._client.stream(
                    "POST",
                    f"{self._base_url}/alpha/{namespace}/v2/{name}",
                    headers=headers,
                    json=body,
                    timeout=35,
                ) as response,
            ):
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 32_000_000:
                        raise HistoryNotesError(
                            "Unable to perform operation: backend response exceeds limit."
                        )
                    chunks.append(chunk)
        except (httpx.HTTPError, TimeoutError):
            raise HistoryNotesError(
                "Unable to perform operation: backend request failed; "
                "execution outcome may be unknown."
            ) from None
        try:
            return json.loads(
                b"".join(chunks).decode("utf-8"),
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
        except (ValueError, UnicodeError):
            raise HistoryNotesError(
                "Unable to perform operation: backend returned invalid JSON."
            ) from None

    async def aclose(self) -> None:
        """Close only the owned transport, including after a failed operation."""
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON number outside finite range")
    return result
