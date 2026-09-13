"""Incremental MCP SSE event framing, separate from connection/reconnect policy."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from corki.mcp.json_rpc import MCPProtocolError, decode_json, integer_response_id


class SseEventTooLarge(MCPProtocolError):
    """A size-limit failure is terminal even when an event cursor exists."""


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One terminated SSE event; event IDs are not JSON-RPC request identities."""

    data: str | None = None
    event: str | None = None
    id: str | None = None
    retry: int | None = None


class SseParser:
    """Keep only an unfinished line/event, and never synthesize an event at EOF."""

    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self._line = bytearray()
        self._retained = 0
        self._previous_cr = False
        self._first_line = True
        self._fields: dict[str, Any] = {}

    def feed(self, chunk: bytes) -> Iterator[SseEvent]:
        """Yield as boundaries arrive, including CRLF split across carrier chunks."""
        offset = 0
        if self._previous_cr and chunk:
            offset = int(chunk[0] == 10)
            self._previous_cr = False
        line_start = offset
        for match in re.finditer(rb"\r\n?|\n", chunk[offset:]):
            end, stop = offset + match.start(), offset + match.end()
            self._append(chunk[line_start:end])
            self._previous_cr = chunk[stop - 1] == 13
            line_start = stop
            yield from self._finish_line()
        self._append(chunk[line_start:])
        if line_start < len(chunk):
            self._previous_cr = False

    def _finish_line(self) -> Iterator[SseEvent]:
        raw = bytes(self._line)
        self._line.clear()
        if self._first_line:
            raw = raw.removeprefix(b"\xef\xbb\xbf")
            self._first_line = False
        if not raw:
            self._retained = 0
            if self._fields:
                fields, self._fields = self._fields, {}
                data = fields.pop("data", None)
                yield SseEvent(data="\n".join(data) if data is not None else None, **fields)
            return
        if raw.startswith(b":"):
            return
        self._retained += len(raw) + 1
        self._check_limit()
        field, separator, value = raw.partition(b":")
        if not separator or field not in (b"data", b"event", b"id", b"retry"):
            raise MCPProtocolError("Invalid MCP SSE field")
        if value.startswith(b" "):
            value = value[1:]
        if field == b"id" and b"\0" in value:
            return
        try:
            text = value.decode("utf-8")
        except UnicodeError:
            raise MCPProtocolError("MCP event stream contains invalid UTF-8") from None
        key = field.decode("ascii")
        if key == "data":
            self._fields.setdefault("data", []).append(text)
            return
        if key in self._fields:
            raise MCPProtocolError("Duplicate MCP SSE field")
        if key == "retry":
            text = text.strip(" \t\r\n\v\f")
            if re.fullmatch(r"\+?[0-9]+", text) is None:
                raise MCPProtocolError("Invalid MCP SSE retry interval")
            digits = text.lstrip("+").lstrip("0") or "0"
            if len(digits) > 20 or int(digits) >= 1 << 64:
                raise MCPProtocolError("Invalid MCP SSE retry interval")
            self._fields[key] = int(digits)
        else:
            self._fields[key] = text

    def _append(self, value: bytes) -> None:
        if len(value) > self.maximum_bytes - self._retained - len(self._line):
            raise SseEventTooLarge(f"MCP SSE event exceeds {self.maximum_bytes} bytes")
        self._line.extend(value)

    def _check_limit(self) -> None:
        if self._retained > self.maximum_bytes:
            raise SseEventTooLarge(f"MCP SSE event exceeds {self.maximum_bytes} bytes")


def message_from_event(event: SseEvent) -> dict[str, Any] | None:
    """Parse message events without confusing an inbound request with a response."""
    if event.event not in (None, "", "message") or event.data is None:
        return None
    try:
        value = decode_json(event.data)
    except MCPProtocolError:
        return None
    return value if isinstance(value, dict) else None


def response_from_event(event: SseEvent, expected_id: int | None) -> dict[str, Any] | None:
    """Control events and inbound requests are not an outbound call's completion."""
    value = message_from_event(event)
    if value is None or "method" in value:
        return None
    if ("result" in value) == ("error" in value):
        return None
    identity = integer_response_id(value.get("id"))
    if identity is None or expected_id is not None and identity != expected_id:
        return None
    return value
