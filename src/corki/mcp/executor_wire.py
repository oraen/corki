"""Bounded executor wire values, distinct from MCP and Responses JSON contracts."""

import base64
import binascii
import re
from dataclasses import dataclass

from corki.mcp.executor_json import ExecutorJSONError, ExecutorValueReader

MAX_RPC_BYTES = 64 * 1024 * 1024
MAX_RPC_VALUES = 256 * 1024
MAX_DELTA_BYTES = 1024 * 1024
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+\Z", re.ASCII)


class ExecutorProtocolError(RuntimeError):
    """Malformed executor traffic is not permission to replay an operation."""


def decode_packet(raw: str | bytes) -> dict:
    """Bound allocations while preserving native arbitrary-precision and private values."""
    try:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(data) > MAX_RPC_BYTES:
            raise ExecutorProtocolError("executor RPC message exceeds byte limit")
        text = data.decode("utf-8")
        value = ExecutorValueReader(text, [MAX_RPC_VALUES]).read()
        if not isinstance(value, dict):
            raise ExecutorProtocolError("executor RPC message must be an object")
        return value
    except ExecutorJSONError as exc:
        raise ExecutorProtocolError(str(exc)) from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExecutorProtocolError("invalid executor JSON") from exc


def integer(value: object, minimum: int, maximum: int) -> int:
    """Typed integer fields do not coerce strings, floating values or booleans."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ExecutorProtocolError("invalid executor integer field")
    return value


def request_id(value: object) -> str | int:
    """String IDs are distinct from numeric IDs; neither aliases the other."""
    if isinstance(value, str):
        return value
    return integer(value, -(1 << 63), (1 << 63) - 1)


def decode_bytes(value: object, *, maximum: int = MAX_RPC_BYTES) -> bytes:
    """Reject oversized encoded frames before allocating decoded bytes."""
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise ExecutorProtocolError("executor body exceeds size limit or is not base64")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ExecutorProtocolError("invalid executor base64 body") from exc
    if len(result) > maximum:
        raise ExecutorProtocolError("executor body exceeds size limit")
    if base64.b64encode(result).decode("ascii") != value:
        raise ExecutorProtocolError("noncanonical executor base64 body")
    return result


@dataclass(frozen=True, slots=True)
class BodyDelta:
    """Validated frame charged to a shared queue until consumed or discarded."""

    request_id: str
    seq: int
    data: bytes
    done: bool
    error: str | None

    @property
    def size(self) -> int:
        return len(self.data) + (len(self.error.encode("utf-8")) if self.error is not None else 0)

    @classmethod
    def parse(cls, value: object) -> "BodyDelta":
        if not isinstance(value, dict) or not isinstance(value.get("requestId"), str):
            raise ExecutorProtocolError("invalid executor body delta identity")
        done, error = value.get("done", False), value.get("error")
        if type(done) is not bool or (error is not None and not isinstance(error, str)):
            raise ExecutorProtocolError("invalid executor body delta terminal fields")
        return cls(
            value["requestId"],
            integer(value.get("seq"), 0, (1 << 64) - 1),
            decode_bytes(value.get("deltaBase64"), maximum=MAX_DELTA_BYTES),
            done,
            error,
        )


@dataclass(frozen=True, slots=True)
class HttpEnvelope:
    """Executor response envelope with repeated, ordered UTF-8 headers preserved."""

    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes

    @classmethod
    def parse(cls, value: object) -> "HttpEnvelope":
        if not isinstance(value, dict) or not isinstance(value.get("headers"), list):
            raise ExecutorProtocolError("invalid executor HTTP response")
        headers = []
        for header in value["headers"]:
            if not isinstance(header, dict) or not all(
                isinstance(header.get(key), str) for key in ("name", "value")
            ):
                raise ExecutorProtocolError("invalid executor HTTP header")
            try:
                name, data = header["name"], header["value"].encode("utf-8")
                if not _HEADER_NAME.fullmatch(name) or any(
                    byte != 9 and (byte < 32 or byte == 127) for byte in data
                ):
                    raise ExecutorProtocolError("invalid executor HTTP header bytes")
                headers.append((name.encode("ascii"), data))
            except UnicodeError as exc:
                raise ExecutorProtocolError("invalid executor HTTP header encoding") from exc
        return cls(
            integer(value.get("status"), 0, 65535),
            tuple(headers),
            decode_bytes(value.get("bodyBase64")),
        )
