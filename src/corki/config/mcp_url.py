"""Pinned URL parsing without host capabilities; raw configuration identity stays intact."""

from functools import lru_cache
from importlib.resources import files

import wasmtime

_FUEL = 100_000_000
_MEMORY_BYTES = 256 * 1024 * 1024
_ERRORS = {
    -1: "invalid MCP URL",
    -2: "MCP URL must be absolute HTTP or HTTPS",
    -3: "Agent Plugin MCP URL must not contain user information or a fragment",
    -4: "non-loopback Agent Plugin MCP endpoints must use HTTPS",
}


@lru_cache(maxsize=1)
def _engine() -> tuple[wasmtime.Engine, wasmtime.Module]:
    config = wasmtime.Config()
    config.consume_fuel = True
    engine = wasmtime.Engine(config)
    binary = files("corki").joinpath("_native/mcp_url.wasm").read_bytes()
    module = wasmtime.Module(engine, binary)
    if module.imports:
        raise ValueError("MCP URL module must not import host capabilities")
    return engine, module


def _parse(raw: str, *, agent_policy: bool, relative: str | None = None) -> str:
    try:
        data = raw.encode("utf-8")
        if len(data) > _MEMORY_BYTES:
            raise ValueError("MCP URL input exceeds memory bound")
        engine, module = _engine()
        with wasmtime.Store(engine) as store:
            store.set_fuel(_FUEL)
            store.set_limits(memory_size=_MEMORY_BYTES, instances=1, memories=1)
            exports = wasmtime.Instance(store, module, []).exports(store)
            pointer = exports["allocate"](store, len(data))
            memory = exports["memory"]
            memory.write(store, data, pointer)
            if relative is None:
                result = exports["parse"](store, pointer, len(data), int(agent_policy))
            else:
                reference = relative.encode("utf-8")
                if len(reference) + len(data) > _MEMORY_BYTES:
                    raise ValueError("MCP URL input exceeds memory bound")
                reference_pointer = exports["allocate"](store, len(reference))
                memory.write(store, reference, reference_pointer)
                result = exports["join"](
                    store, pointer, len(data), reference_pointer, len(reference)
                )
            if result <= 0:
                raise ValueError(_ERRORS.get(result, "invalid MCP URL parser result"))
            start, size = result >> 32, result & 0xFFFFFFFF
            if start + size > memory.data_len(store):
                raise ValueError("invalid MCP URL parser output bounds")
            return bytes(memory.read(store, start, start + size)).decode("utf-8")
    except (wasmtime.Trap, wasmtime.WasmtimeError, OSError) as error:
        raise ValueError("MCP URL engine unavailable or resource limit exceeded") from error


def agent_url(raw: str) -> str:
    """Validate Agent Plugin endpoint policy using the parsed host, then canonicalize."""
    return _parse(raw, agent_policy=True)


def http_url(raw: str) -> str:
    """Canonicalize the transport address without changing raw source/policy identity."""
    return _parse(raw, agent_policy=False)


def join_http_url(base: str, reference: str) -> str:
    """Resolve redirect syntax before the caller checks its origin and policy."""
    return _parse(base, agent_policy=False, relative=reference)
